# Nova Sonic hands-free 全雙工 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **專案交接指示：本線「勿委派 subagent」→ 採 inline 執行（executing-plans），不 dispatch 子代理。**

**Goal:** 把 Nova Sonic live S2S 從 hold-to-talk 升級為 hands-free 全雙工（麥常開、Nova VAD 分段、真 barge-in、點企鵝開/關）。

**Architecture:** `/ws/live` 加 `?mode=continuous` 開關；連續模式走上下行雙 asyncio.Task，AUDIO 內容常開交給 Nova server VAD 分段。Phase 0 先在此開關上做 AEC spike 當硬閘門，過關才做 Phase 1 barge-in。既有 hold-to-talk 路徑零改動、當降級安全網。

**Tech Stack:** FastAPI/Starlette WebSocket、asyncio、aws_sdk_bedrock_runtime（Nova 2 Sonic bidi）、Web Audio API（AudioWorklet + AudioContext）、pytest、node:test。

## Global Constraints

- 語音上行 16kHz PCM16 mono；下行 24kHz PCM16 mono（既有設定，不改）。
- `server/nova_sonic.py` import 絕不可炸（無 SDK 仍要能啟動）；重依賴一律 lazy import。
- 既有 hold-to-talk 方法（`pushToTalkStart/End`、`end_user_turn`、`events`）與 `/ws/live` 無 `mode` 參數的行為**一行不動**。
- getUserMedia 約束（連續模式）：`{ echoCancellation:true, noiseSuppression:true, autoGainControl:true, channelCount:1 }`。
- 伺服器啟動需帶 SigV4 env：`set -a; . ./.env; set +a` 再跑 uvicorn（憑證不進 repo）。
- pytest 從 `talkybuddy/` 目錄跑；node 測 `node --test web/`。
- 整合層（`LiveSession`/`index.html`/worklet）依專案慣例**無自動測試**，以 `node --check` + 真機 e2e 驗證。

---

# Phase 0 — hands-free 連續管線 + AEC spike（硬閘門）

> Phase 0 建立連續模式的伺服器與前端管線（Phase 1 保留沿用），並在其上做真機 AEC 量測。**Task 5 是硬閘門：未過關不得進 Phase 1。** interrupt 事件解析留到 Phase 1，因其欄位形態要靠 Task 5 量測探明。

## Task 1: `NovaSonicSession.events_continuous()` 連續多輪迭代

**Files:**
- Modify: `talkybuddy/server/nova_sonic.py`（`NovaSonicSession` 內新增方法，約 190–199 行 `events()` 之後）
- Test: `talkybuddy/tests/test_nova_sonic.py`

**Interfaces:**
- Consumes: 既有 `self._queue`（`_receive_loop` 已跨多輪 put `transcript`/`audio`/`turn_end`，串流結束 put `None`）。
- Produces: `async def events_continuous(self)` — 跨多輪連續 yield `NovaEvent`，只在收到 `None` 哨兵才 return（**不因 `turn_end` 而 return**，這是與既有 `events()` 的唯一差異）。

- [ ] **Step 1: Write the failing test**

在 `talkybuddy/tests/test_nova_sonic.py` 末端加：

```python
@pytest.mark.asyncio
async def test_events_continuous_yields_across_multiple_turns(monkeypatch):
    """events_continuous 不因 turn_end return，跨多輪吐到 None 哨兵才止。"""
    fake_client = _FakeClient()
    monkeypatch.setattr(nova_sonic.NovaSonicSession, "_build_client",
                        lambda self: fake_client)
    s = nova_sonic.NovaSonicSession("m", "tiffany", "us-east-1")
    await s.start("sys")
    # 直接灌 queue 模擬兩輪（繞過真 receive_loop）
    from server.nova_sonic import NovaEvent
    s._recv_task.cancel()  # 停背景 loop，改手動灌
    for ev in [NovaEvent("transcript", role="ASSISTANT", text="第一輪"),
               NovaEvent("turn_end"),
               NovaEvent("transcript", role="ASSISTANT", text="第二輪"),
               NovaEvent("turn_end"),
               None]:
        s._queue.put_nowait(ev)
    got = []
    async for e in s.events_continuous():
        got.append((e.kind, e.text))
    assert got == [("transcript", "第一輪"), ("turn_end", ""),
                   ("transcript", "第二輪"), ("turn_end", "")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd talkybuddy && python -m pytest tests/test_nova_sonic.py::test_events_continuous_yields_across_multiple_turns -v`
Expected: FAIL — `AttributeError: 'NovaSonicSession' object has no attribute 'events_continuous'`

- [ ] **Step 3: Write minimal implementation**

在 `server/nova_sonic.py` 的 `events()` 方法之後加：

```python
    async def events_continuous(self):
        """連續模式：跨多輪 yield 事件，只在串流結束（None 哨兵）才 return。
        與 events() 差別＝不因 turn_end 中止（hands-free 一場對話多輪）。"""
        while True:
            ev = await self._queue.get()
            if ev is None:
                return
            yield ev
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd talkybuddy && python -m pytest tests/test_nova_sonic.py::test_events_continuous_yields_across_multiple_turns -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add talkybuddy/server/nova_sonic.py talkybuddy/tests/test_nova_sonic.py
git commit -m "feat(live): NovaSonicSession.events_continuous 連續多輪迭代（hands-free 前置）"
```

---

## Task 2: `ws_live` `?mode=continuous` 雙 Task 分支 + spike 事件日誌

**Files:**
- Modify: `talkybuddy/server/app.py`（`ws_live`，373–468 行）
- Test: `talkybuddy/tests/test_app_live.py`

**Interfaces:**
- Consumes: `websocket.query_params`、`session.send_audio`、`session.events_continuous`（Task 1）、`_store_live_turn`。
- Produces: `?mode=continuous` 連線走雙 Task：uplink `receive→send_audio`（忽略 `user_end`）、downlink `events_continuous→emit`；任一 Task 結束就收另一 + `session.close()`。**spike 期間** downlink 對每個事件 `print("[live-raw]", ...)`（Task 9 移除）。

- [ ] **Step 1: Write the failing test**

在 `talkybuddy/tests/test_app_live.py` 的 `_FakeSession` 加一個連續模式的事件序（含 `user_end` 應被忽略），並加測：

```python
class _FakeSessionContinuous(_FakeSession):
    """連續模式 fake：events_continuous 吐一輪後結束（None 由迭代自然收）。"""
    async def events_continuous(self):
        from server.nova_sonic import NovaEvent
        yield NovaEvent("transcript", role="USER", text="哈囉")
        yield NovaEvent("transcript", role="ASSISTANT", text="嗨呀")
        yield NovaEvent("audio", audio=b"\x00\x00" * 4)
        yield NovaEvent("turn_end")


def test_ws_live_continuous_forwards_and_ignores_user_end(monkeypatch):
    monkeypatch.setattr(config, "LIVE_S2S_ENABLED", True)
    monkeypatch.setattr(nova_sonic, "available", lambda: True)
    fake = _FakeSessionContinuous()
    monkeypatch.setattr(app_mod, "_make_live_session", lambda: fake)
    monkeypatch.setattr(app_mod.guardrails, "consent_granted", lambda: True)
    seen = {}
    monkeypatch.setattr(app_mod.store, "add_interaction",
                        lambda d: seen.update(d) or 1)
    client = TestClient(app_mod.app)
    with client.websocket_connect("/ws/live?mode=continuous") as ws:
        ws.send_bytes(b"\x01\x02" * 8)
        ws.send_text(json.dumps({"type": "user_end"}))  # 連續模式應被忽略、不觸發收尾
        kinds = []
        for _ in range(4):
            m = ws.receive()
            if m.get("text"):
                kinds.append(json.loads(m["text"]).get("type"))
            elif m.get("bytes"):
                kinds.append("audio")
    assert "live_transcript" in kinds and "audio" in kinds and "turn_end" in kinds
    assert fake.ended is False          # user_end 未觸發 end_user_turn
    assert seen.get("asr_text") == "哈囉" and seen.get("reply_text") == "嗨呀"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd talkybuddy && python -m pytest tests/test_app_live.py::test_ws_live_continuous_forwards_and_ignores_user_end -v`
Expected: FAIL —（現行單迴圈不吐下行、且 user_end 會呼 end_user_turn / 或無 continuous 分支導致無事件）

- [ ] **Step 3: Write minimal implementation**

在 `server/app.py` `ws_live` 的 `try:` 區塊，把 `await session.start(system_prompt)` 之後改為依 mode 分流。將現有 `while True: ...` 主迴圈包成 `_run_half_duplex()`，並新增連續分支：

```python
    continuous = websocket.query_params.get("mode") == "continuous"

    async def _uplink() -> None:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                return
            if msg.get("bytes") is not None:
                if msg["bytes"]:
                    await session.send_audio(msg["bytes"])
                continue
            raw = msg.get("text")
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            if payload.get("type") == "bye":
                return
            # 連續模式：user_end 無意義（turn 邊界交給 Nova VAD），忽略

    async def _downlink() -> None:
        async for ev in session.events_continuous():
            print("[live-raw]", ev.kind, getattr(ev, "role", ""), flush=True)  # spike，Task 9 移除
            if ev.kind == "audio":
                await emit_bytes(ev.audio)
            elif ev.kind == "transcript":
                await emit({"type": "live_transcript", "role": ev.role, "text": ev.text})
                if ev.role == "USER":
                    turn_user.append(ev.text)
                elif ev.role == "ASSISTANT":
                    turn_asst.append(ev.text)
            elif ev.kind == "turn_end":
                _store_live_turn(turn_user, turn_asst)
                turn_user.clear()
                turn_asst.clear()
                await emit({"type": "turn_end"})

    try:
        await session.start(system_prompt)
        if continuous:
            up = asyncio.create_task(_uplink())
            down = asyncio.create_task(_downlink())
            done, pending = await asyncio.wait(
                {up, down}, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
        else:
            while True:
                msg = await websocket.receive()
                # ...（既有 hold-to-talk 迴圈原封不動）
```

> 註：把既有 373–468 的 `while True` hold-to-talk 迴圈整段搬進 `else:` 分支即可（邏輯不改）。`_uplink/_downlink` 定義放在 `drain_events` 附近、`try` 之前。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd talkybuddy && python -m pytest tests/test_app_live.py -v`
Expected: PASS（新測綠、既有 hold-to-talk 測不退步）

- [ ] **Step 5: Commit**

```bash
git add talkybuddy/server/app.py talkybuddy/tests/test_app_live.py
git commit -m "feat(live): /ws/live ?mode=continuous 雙 Task 分支（uplink/downlink 常駐）+ spike 事件日誌"
```

---

## Task 3: `LiveSession` 連續擷取 + AEC 約束（前端整合層）

**Files:**
- Modify: `talkybuddy/web/live-client.js`（`LiveSession`，46–148 行）

**Interfaces:**
- Consumes: 既有 `liveWsURL()`、worklet、`_play()`、callbacks。
- Produces:
  - `startConversation()` — `capturing=true`（整場常開）＋ resume `micCtx`/`playCtx`＋狀態 `listen`。
  - `stopConversation()` — `capturing=false`＋送 `bye`＋狀態 `idle`。
  - `start()` 的 WS URL 於連續模式帶 `?mode=continuous`（新增 constructor 選項 `cb.continuous`）；getUserMedia 約束加 AEC 三旗標。

- [ ] **Step 1: 加連續選項與 AEC 約束**

在 `live-client.js` `start()`：WS URL 與 getUserMedia 兩處改。將 62 行：

```js
    this.ws = new WebSocket(liveWsURL() + (this.cb.continuous ? "?mode=continuous" : ""));
```

將 76 行 getUserMedia 改：

```js
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: {
      echoCancellation: true, noiseSuppression: true, autoGainControl: true,
      channelCount: 1 } });
```

- [ ] **Step 2: 加 startConversation/stopConversation**

在 `pushToTalkEnd()`（108 行）之後加：

```js
  // hands-free：整場常開擷取（非單輪）。點企鵝開始 → 呼此。
  startConversation() {
    this.capturing = true;
    if (this.micCtx && this.micCtx.state === "suspended") this.micCtx.resume();
    if (this.playCtx && this.playCtx.state === "suspended") this.playCtx.resume();
    if (this.cb.onStateChange) this.cb.onStateChange("listen");
  }

  stopConversation() {
    this.capturing = false;
    if (this.ws && this.ws.readyState === 1) {
      this.ws.send(JSON.stringify({ type: "bye" }));
    }
    if (this.cb.onStateChange) this.cb.onStateChange("idle");
  }
```

- [ ] **Step 3: 語法檢查**

Run: `cd talkybuddy && node --check web/live-client.js && node --test web/`
Expected: `node --check` 無輸出（通過）；既有 4 個 node 測仍綠。

- [ ] **Step 4: Commit**

```bash
git add talkybuddy/web/live-client.js
git commit -m "feat(live): LiveSession 連續擷取 startConversation/stopConversation + AEC 約束"
```

---

## Task 4: `index.html` 點企鵝開/關 toggle + enterLiveMode 只建立不擷取

**Files:**
- Modify: `talkybuddy/web/index.html`（`enterLiveMode` 855–875、penguin 事件 648–670）

**Interfaces:**
- Consumes: `LiveSession.startConversation/stopConversation`（Task 3）、既有 `setVisualState`、`liveSession`、`liveMode`。
- Produces: 點企鵝在 live 模式 toggle 對話；移除 hold-to-talk pointer handler；`enterLiveMode` 建立 `LiveSession({continuous:true})` 但不自動擷取。

- [ ] **Step 1: enterLiveMode 帶 continuous、文案改就緒**

在 `enterLiveMode()`（856 行 `new mod.LiveSession({...})`）的 callback 物件加 `continuous: true`，並把 `onTurnEnd` 改為回 `listen`（整場還在）：

```js
    liveSession = new mod.LiveSession({
      continuous: true,
      onTranscript: function (role, text) {
        addBubble(role === "USER" ? "student" : "penguin", text, {});
      },
      onTurnEnd: function () { setVisualState("listen"); },
      onError: function (reason) { degradeToHalfDuplex(reason); },
      onStateChange: function (state) { setVisualState(state); }
    });
```

把 869 行 `holdHint.textContent` 與 871 行 toast 改：

```js
      holdHint.textContent = "👆 點企鵝開始對話，再點一下結束";
      showToast("🎙️ 即時對話模式已就緒，點企鵝開始");
```

- [ ] **Step 2: 移除 hold-to-talk pointer handler、penguin click 改 toggle**

刪除 656–670 行整段 hold-to-talk（`pointerdown`/`endPushToTalk`/`pointerup`/`pointercancel`/`pointerleave`）。把 648–655 的 penguin `click` handler 的 live 分支改 toggle：

```js
penguinWrap.addEventListener("click", function (e) {
  if (liveMode) {
    if (!liveSession) { return; }
    if (liveSession.capturing) { liveSession.stopConversation(); }
    else { liveSession.startConversation(); }
    return;
  }
  // ...（既有半雙工 tap-to-toggle 分支原封不動）
});
```

- [ ] **Step 3: 語法檢查**

Run: `cd talkybuddy && node --check web/index.html 2>/dev/null || node -e "require('fs');new Function(require('fs').readFileSync('web/index.html','utf8').match(/<script>([\s\S]*)<\/script>/)[1])" && echo SCRIPT_OK`
Expected: `SCRIPT_OK`（index.html 內嵌 script 語法通過；比照 Task 9' 驗法）

- [ ] **Step 4: Commit**

```bash
git add talkybuddy/web/index.html
git commit -m "feat(live): index 點企鵝開/關 toggle + enterLiveMode 只建立不擷取（hands-free）"
```

---

## Task 5: 🚦 真機 AEC spike 量測 + 硬閘門（手動）

**Files:**
- Modify: `talkybuddy/docs/superpowers/specs/2026-07-14-nova-sonic-handsfree-fullduplex-design.md`（填附錄 A）

**Interfaces:**
- Consumes: Task 1–4 的連續管線 + `[live-raw]` 伺服器日誌。
- Produces: spike findings（三題 PASS/FAIL + Nova 中斷事件欄位形態），寫入 spec 附錄 A。**閘門決策**供 Phase 1 或降級。

- [ ] **Step 1: 啟動帶憑證的 HTTPS server**

```bash
cd talkybuddy && set -a; . ./.env; set +a
uvicorn server.app:app --host 0.0.0.0 --port 8000 \
  --ssl-certfile /tmp/tb_certs/cert.pem --ssl-keyfile /tmp/tb_certs/key.pem 2>&1 | tee /tmp/tb_hf_spike.log
```
（手機開 `https://<PC 區網 IP>:8000/`；確認 `/api/status` `live_s2s:true`。）

- [ ] **Step 2: 題1 靜默漏音測**

點企鵝開始 → 讓 AI 講一段長回覆 → 使用者**完全不出聲**。觀察：AI 是否被自己回音打斷？是否冒假 USER 氣泡？`grep "\[live-raw\]" /tmp/tb_hf_spike.log` 看有無非預期 USER/中斷事件。
判定：整段講完、無假 USER、無中斷 = **PASS**。

- [ ] **Step 3: 題2 正常分段測**

說一句話 → 停頓 → 觀察 Nova 是否**不靠尾靜音**自動判定 turn 結束並回覆。判定：有回覆 = **PASS**。

- [ ] **Step 4: 題3 barge-in 中斷事件形態**

AI 講話中使用者出聲 → `grep "\[live-raw\]" /tmp/tb_hf_spike.log`，並在 `server/nova_sonic.py` `_receive_loop` 臨時把未處理事件的完整 raw 印出（`print("[live-raw-full]", raw[:400], flush=True)`），記錄 Nova barge-in 時送的事件鍵與 `stopReason`（例如 `contentEnd` 帶 `stopReason:"INTERRUPTED"`）。這是 Phase 1 Task 6 解析的依據。

- [ ] **Step 5: 填 findings + 閘門判定**

把三題結果與觀測到的中斷事件形態填入 spec 附錄 A。**閘門**：
- 三題皆 PASS → 進 Phase 1（Task 6）。
- 題1 FAIL → **停**，先試把 `index.html` 播放改走共用擷取 context / WebRTC audio element 再重測；仍不行 → 回報用戶決定改「AI 說話時軟閘麥」（放棄 barge-in）。**未過關不得進 Phase 1。**

- [ ] **Step 6: Commit findings**

```bash
git add talkybuddy/docs/superpowers/specs/2026-07-14-nova-sonic-handsfree-fullduplex-design.md
git commit -m "docs(spike): Nova Sonic hands-free AEC spike findings（附錄 A）"
```

---

# Phase 1 — 真 barge-in（Task 5 閘門 PASS 後）

> 依 Task 5 探明的 Nova 中斷事件形態實作。若閘門未過，**不執行本 Phase**。

## Task 6: `NovaSonicSession` 解析 Nova 中斷事件 → `NovaEvent("interrupt")`

**Files:**
- Modify: `talkybuddy/server/nova_sonic.py`（`_receive_loop`，149–188 行）
- Test: `talkybuddy/tests/test_nova_sonic.py`

**Interfaces:**
- Consumes: Task 5 findings 的中斷事件形態（下方以 `contentEnd` 帶 `stopReason:"INTERRUPTED"` 為預設；**若 findings 不同，改對應鍵**）。
- Produces: `_receive_loop` 偵測中斷 → `put NovaEvent("interrupt")` 並 reset `got_assistant`；`events_continuous` 因此會 yield `kind=="interrupt"`。

- [ ] **Step 1: Write the failing test**

在 `test_nova_sonic.py` 加（假設 findings＝`contentEnd.stopReason=="INTERRUPTED"`；依實測調整）：

```python
@pytest.mark.asyncio
async def test_receive_loop_emits_interrupt_on_barge_in(monkeypatch):
    """Nova 中斷事件 → queue 出現 NovaEvent(kind='interrupt')。"""
    import json as _json
    events = [
        _json.dumps({"event": {"textOutput": {"role": "ASSISTANT", "content": "我正在說"}}}).encode(),
        _json.dumps({"event": {"contentEnd": {"stopReason": "INTERRUPTED"}}}).encode(),
    ]
    fake_client = _FakeClient(out_events=events)
    monkeypatch.setattr(nova_sonic.NovaSonicSession, "_build_client",
                        lambda self: fake_client)
    s = nova_sonic.NovaSonicSession("m", "tiffany", "us-east-1")
    await s.start("sys")
    kinds = []
    async for e in s.events_continuous():
        kinds.append(e.kind)
        if e.kind == "interrupt":
            break
    assert "interrupt" in kinds
    await s.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd talkybuddy && python -m pytest tests/test_nova_sonic.py::test_receive_loop_emits_interrupt_on_barge_in -v`
Expected: FAIL（無 interrupt 事件被 put）

- [ ] **Step 3: Write minimal implementation**

在 `_receive_loop` 的 `elif "completionEnd" in ev:` 之前加中斷分支（依 findings 調整鍵）：

```python
                elif "contentEnd" in ev and ev["contentEnd"].get("stopReason") == "INTERRUPTED":
                    await self._queue.put(NovaEvent("interrupt"))
                    got_assistant = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd talkybuddy && python -m pytest tests/test_nova_sonic.py -v`
Expected: PASS（新測綠、既有 nova_sonic 測不退步）

- [ ] **Step 5: Commit**

```bash
git add talkybuddy/server/nova_sonic.py talkybuddy/tests/test_nova_sonic.py
git commit -m "feat(live): NovaSonicSession 解析 Nova 中斷事件 → interrupt（barge-in 前置）"
```

---

## Task 7: `ws_live` 轉發 interrupt → `{type:"interrupt"}`

**Files:**
- Modify: `talkybuddy/server/app.py`（`_downlink`，Task 2 新增處）
- Test: `talkybuddy/tests/test_app_live.py`

**Interfaces:**
- Consumes: `session.events_continuous` 吐出的 `NovaEvent("interrupt")`（Task 6）。
- Produces: downlink 收 `interrupt` → `emit({"type":"interrupt"})`。

- [ ] **Step 1: Write the failing test**

```python
def test_ws_live_continuous_forwards_interrupt(monkeypatch):
    class _FakeInterrupt(_FakeSession):
        async def events_continuous(self):
            from server.nova_sonic import NovaEvent
            yield NovaEvent("audio", audio=b"\x00\x00" * 4)
            yield NovaEvent("interrupt")
    monkeypatch.setattr(config, "LIVE_S2S_ENABLED", True)
    monkeypatch.setattr(nova_sonic, "available", lambda: True)
    monkeypatch.setattr(app_mod, "_make_live_session", lambda: _FakeInterrupt())
    monkeypatch.setattr(app_mod.guardrails, "consent_granted", lambda: True)
    client = TestClient(app_mod.app)
    with client.websocket_connect("/ws/live?mode=continuous") as ws:
        kinds = []
        for _ in range(2):
            m = ws.receive()
            if m.get("text"):
                kinds.append(json.loads(m["text"]).get("type"))
            elif m.get("bytes"):
                kinds.append("audio")
    assert "interrupt" in kinds
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd talkybuddy && python -m pytest tests/test_app_live.py::test_ws_live_continuous_forwards_interrupt -v`
Expected: FAIL（interrupt 未轉發）

- [ ] **Step 3: Write minimal implementation**

在 `_downlink` 的 `elif ev.kind == "turn_end":` 之後加：

```python
            elif ev.kind == "interrupt":
                await emit({"type": "interrupt"})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd talkybuddy && python -m pytest tests/test_app_live.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add talkybuddy/server/app.py talkybuddy/tests/test_app_live.py
git commit -m "feat(live): ws_live 連續模式轉發 interrupt → {type:interrupt}"
```

---

## Task 8: `LiveSession` barge-in 播放 flush

**Files:**
- Modify: `talkybuddy/web/live-client.js`（`_play` 126–139、`_onMessage` 110–124、constructor 49–58）

**Interfaces:**
- Consumes: 伺服器 `{type:"interrupt"}`（Task 7）。
- Produces: `_flushPlayback()` 停所有排程中的 `BufferSourceNode` 並歸零 `_nextStart`；`_onMessage` 收 interrupt 呼之並切 `listen`。

- [ ] **Step 1: constructor 加 `_sources`**

在 constructor（57 行 `this._nextStart = 0;` 後）加：`this._sources = [];`

- [ ] **Step 2: `_play` 追蹤 source、播完自動移除**

把 `_play()` 的 `node.connect(...)` 之後、`node.start(...)` 附近改為：

```js
    node.connect(this.playCtx.destination);
    this._sources.push(node);
    node.onended = () => {
      const i = this._sources.indexOf(node);
      if (i >= 0) this._sources.splice(i, 1);
    };
    const now = this.playCtx.currentTime;
    this._nextStart = Math.max(this._nextStart, now);
    node.start(this._nextStart);
    this._nextStart += buf.duration;
```

- [ ] **Step 3: 加 `_flushPlayback` + `_onMessage` 收 interrupt**

在 `_play()` 之後加：

```js
  _flushPlayback() {
    this._sources.forEach((n) => { try { n.stop(); } catch (e) {} });
    this._sources = [];
    if (this.playCtx) this._nextStart = this.playCtx.currentTime;
  }
```

在 `_onMessage` 的 `else if (m.type === "turn_end")` 分支後加：

```js
    } else if (m.type === "interrupt") {
      this._flushPlayback();
      if (this.cb.onStateChange) this.cb.onStateChange("listen");
```

- [ ] **Step 4: 語法檢查**

Run: `cd talkybuddy && node --check web/live-client.js && node --test web/`
Expected: `node --check` 通過；既有 node 測綠。

- [ ] **Step 5: Commit**

```bash
git add talkybuddy/web/live-client.js
git commit -m "feat(live): LiveSession barge-in 播放 flush（收 interrupt 停播 + 切 listen）"
```

---

## Task 9: 清 spike 日誌 + hands-free e2e checklist

**Files:**
- Modify: `talkybuddy/server/app.py`（移除 `[live-raw]` print）、`talkybuddy/server/nova_sonic.py`（移除 `[live-raw-full]` 臨時 print，若有）
- Modify/Create: hands-free e2e checklist（附於既有 live e2e checklist 文件，沿用 Task 9' 位置）

**Interfaces:**
- Consumes: 全部前面任務。
- Produces: 乾淨的伺服器日誌 + hands-free 手動 e2e 檢查表。

- [ ] **Step 1: 移除 spike 日誌**

刪 `server/app.py` `_downlink` 的 `print("[live-raw]", ...)` 一行；刪 `server/nova_sonic.py` 任何 Task 5 加的臨時 `[live-raw-full]` print。

- [ ] **Step 2: 全測回歸**

Run: `cd talkybuddy && python -m pytest -q && node --test web/`
Expected: 全綠（pytest 既有 + 新增；node 4 測）。

- [ ] **Step 3: 補 hands-free e2e checklist**

在既有 live e2e checklist 文件加 hands-free 案例：
- 點企鵝 → 麥常開、狀態 listen；再點 → 停、回 idle。
- 連續多輪不需再按，VAD 自動接話。
- AI 講話中出聲 → 立即打斷（barge-in flush 生效）。
- 靜默時無自問自答（AEC 過關實證）。
- live 失敗 → 自動降半雙工。

- [ ] **Step 4: 真機 e2e 驗證**

依上表逐項真機驗過（手機 → HTTPS server），記錄結果。

- [ ] **Step 5: Commit**

```bash
git add talkybuddy/server/app.py talkybuddy/server/nova_sonic.py talkybuddy/docs
git commit -m "chore(live): 移除 hands-free spike 日誌 + 補 hands-free e2e checklist"
```

---

## 收尾提醒（非任務）

- 🔴 **安全**：真機驗完必到 AWS console 刪除/輪替外露 IAM key `AKIAZKQ2XL7Q6MQTTIMY`（AKIA 永久）。
- 更新交接記憶 `talkybuddy-nova-sonic-live.md`：hands-free 完成狀態、spike findings、下一步。
- push / PR 與否待用戶決定。

## Self-Review 對照

- spec §4 Phase 0 spike（開關、三題量測、閘門）→ Task 1–5 ✓
- spec §5 伺服器（開關、雙 Task、events_continuous、interrupt）→ Task 1/2/6/7 ✓
- spec §6 前端（startConversation/stopConversation、AEC、flush）→ Task 3/8 ✓
- spec §7 index（toggle、build-only enterLiveMode）→ Task 4 ✓
- spec §8 降級（沿用 degradeToHalfDuplex、gather 收斂）→ Task 2（gather）、既有降級零改動 ✓
- spec §9 測試（pytest 連續分支、node 純函式、e2e）→ 各 Task 測步 + Task 9 ✓
- 型別一致：`events_continuous`、`NovaEvent("interrupt")`、`{type:"interrupt"}`、`startConversation/stopConversation`、`_flushPlayback`、`_sources`、`?mode=continuous` 全計畫一致 ✓
