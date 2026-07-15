# live 接 B1/B3 教學內容 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓純 live hands-free 對話每場開始都拿到「今日主題＋目標句＋B1 策略」，Nova 以教練角色跑跟讀迴圈，並在場末回寫診斷讓下一場自適應，根治「重複亂聊」。

**Architecture:** 新增 `server/lesson.py`（選教材純函式）；重寫 `scaffold.build_live_system_prompt` 與 `_LIVE_STATIC_FRAME`（教練跟讀迴圈）；`app.py ws_live` 連線時用 `build_lesson` 建 prompt、收尾背景跑一次診斷回寫。全部重用既有 `diagnose`/`curriculum`/`scaffold.VOCAB`/`store`，零新外部依賴。

**Tech Stack:** Python 3（stdlib + 既有 server 模組）、pytest。所有測試離線可跑。

## Global Constraints

- 一律繁體中文（台灣用語）回覆；程式碼/識別字保留英文。
- 零新外部依賴；重用既有模組。
- 任何選教材/診斷失敗都不可中斷 live 串流（try/except 安全退化）。
- `build_live_system_prompt` 對 `None`/空欄位向後相容（既有行為不變）。
- 冷啟動預設：`topic=curriculum.TOPIC_ORDER[0]`（`animal`）、`target_form=curriculum._TARGET_FORM[1]`。
- 每場對話開始算一次教學內容；不在場中改 Nova system prompt。
- 本計畫不做聲學發音評測（另 session）。

---

### Task 1: `pick_target_sentence`（lesson.py 選目標句）

**Files:**
- Create: `server/lesson.py`
- Test: `tests/test_lesson.py`

**Interfaces:**
- Consumes: `scaffold.VOCAB`（`dict[zh] -> {"en","cat","np","sent"}`）。
- Produces: `pick_target_sentence(topic: str, profile: dict | None = None) -> str`。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_lesson.py
from server import lesson


def test_pick_target_sentence_by_topic():
    s = lesson.pick_target_sentence("food", None)
    assert isinstance(s, str) and s
    # food 類的例句都在講吃/喝
    assert ("eat" in s) or ("drink" in s)


def test_pick_target_sentence_prefers_learning_vocab():
    # profile 指定正在學 banana → 應挑到 banana 的例句
    profile = {"learning_vocab": [{"en": "banana", "zh": "香蕉", "cat": "food"}]}
    s = lesson.pick_target_sentence("food", profile)
    assert "banana" in s


def test_pick_target_sentence_unknown_topic_falls_back():
    s = lesson.pick_target_sentence("no_such_topic", None)
    assert s == "How are you today?"


def test_pick_target_sentence_missing_profile_ok():
    s = lesson.pick_target_sentence("animal", {})
    assert isinstance(s, str) and s
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_lesson.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'server.lesson'`）

- [ ] **Step 3: Write minimal implementation**

```python
# server/lesson.py
# -*- coding: utf-8 -*-
"""lesson.py — 每場 live 對話開始時「選教材」：由最新診斷 + profile
決定今日主題、目標句與 B1 策略字串。與 WS/Nova 無耦合，離線可測。
"""

from __future__ import annotations

_DEFAULT_SENTENCE = "How are you today?"


def pick_target_sentence(topic, profile=None) -> str:
    """挑本場跟讀的英文目標句。

    從 scaffold.VOCAB 篩 cat==topic 且有 sent 的詞；優先挑 profile 正在學
    （learning_vocab）對應的詞的例句；缺就取該類第一句；完全無 → 通用預設。
    任何例外都退化為預設，不炸。
    """
    from server import scaffold
    try:
        cands = [info for info in scaffold.VOCAB.values()
                 if info.get("cat") == topic and info.get("sent")]
        if not cands:
            return _DEFAULT_SENTENCE
        learning = set()
        for v in (profile or {}).get("learning_vocab", []) or []:
            en = v.get("en") if isinstance(v, dict) else v
            if en:
                learning.add(str(en))
        for info in cands:
            if info.get("en") in learning:
                return info["sent"]
        return cands[0]["sent"]
    except Exception:
        return _DEFAULT_SENTENCE
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_lesson.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add server/lesson.py tests/test_lesson.py
git commit -m "feat(lesson): pick_target_sentence 由 topic/profile 選跟讀目標句（重用 VOCAB）"
```

---

### Task 2: `build_lesson` + `Lesson`（lesson.py 組教材）

**Files:**
- Modify: `server/lesson.py`
- Test: `tests/test_lesson.py`

**Interfaces:**
- Consumes: `store.list_diagnoses()`、`store.get_profile()` 的回傳（`diagnoses: list[dict]`、`profile: dict | None`）；`diagnose.format_directive_for_prompt(cd, level_state)`；`curriculum.TOPIC_ORDER`、`curriculum._TARGET_FORM`；`pick_target_sentence`（Task 1）。診斷 dict 形如 `{"companion_directive": {...}, "level_state": {"topic","target_form",...}, ...}`。
- Produces: `Lesson(topic: str, target_sentence: str, target_form: str | None, directive: str | None)`；`build_lesson(diagnoses: list[dict] | None, profile: dict | None = None) -> Lesson`。

- [ ] **Step 1: Write the failing test**

```python
# 追加到 tests/test_lesson.py
from server import curriculum


def test_build_lesson_cold_start_uses_defaults():
    lp = lesson.build_lesson([], None)
    assert lp.topic == curriculum.TOPIC_ORDER[0]  # "animal"
    assert lp.target_form == curriculum._TARGET_FORM[1]
    assert lp.directive is None
    assert isinstance(lp.target_sentence, str) and lp.target_sentence


def test_build_lesson_uses_latest_diagnosis():
    diagnoses = [{
        "companion_directive": {"difficulty": "up"},
        "level_state": {"topic": "food", "target_form": "短句 3-4 詞"},
    }]
    lp = lesson.build_lesson(diagnoses, None)
    assert lp.topic == "food"
    assert lp.target_form == "短句 3-4 詞"
    assert lp.directive and isinstance(lp.directive, str)
    assert ("eat" in lp.target_sentence) or ("drink" in lp.target_sentence)


def test_build_lesson_missing_level_state_falls_back():
    lp = lesson.build_lesson([{"companion_directive": {"difficulty": "keep"}}], None)
    assert lp.topic == curriculum.TOPIC_ORDER[0]
    assert isinstance(lp.target_sentence, str) and lp.target_sentence
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_lesson.py -q`
Expected: FAIL（`AttributeError: module 'server.lesson' has no attribute 'build_lesson'`）

- [ ] **Step 3: Write minimal implementation**

```python
# server/lesson.py 追加（頂部 import 區）
from dataclasses import dataclass


@dataclass
class Lesson:
    topic: str
    target_sentence: str
    target_form: str | None
    directive: str | None


def build_lesson(diagnoses, profile=None) -> Lesson:
    """由最新診斷 + profile 組本場教材。全程安全退化，永不擋 live。"""
    from server import curriculum, diagnose
    default_topic = curriculum.TOPIC_ORDER[0]
    default_form = curriculum._TARGET_FORM[1]
    try:
        latest = (diagnoses or [])[-1] if (diagnoses or []) else None
        directive = None
        topic = default_topic
        target_form = default_form
        if latest:
            ls = latest.get("level_state") or {}
            cd = latest.get("companion_directive")
            if cd:
                directive = diagnose.format_directive_for_prompt(cd, ls) or None
            topic = ls.get("topic") or default_topic
            target_form = ls.get("target_form") or default_form
        return Lesson(topic, pick_target_sentence(topic, profile),
                      target_form, directive)
    except Exception:
        return Lesson(default_topic, pick_target_sentence(default_topic, profile),
                      default_form, None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_lesson.py -q`
Expected: PASS（7 passed）

- [ ] **Step 5: Commit**

```bash
git add server/lesson.py tests/test_lesson.py
git commit -m "feat(lesson): build_lesson 由最新診斷+profile 組本場主題/目標句/策略（冷啟動安全退化）"
```

---

### Task 3: 教練跟讀 prompt（scaffold）

**Files:**
- Modify: `server/scaffold.py`（`_LIVE_STATIC_FRAME` ≈481-490、`build_live_system_prompt` ≈493-508）
- Test: `tests/test_scaffold_live_prompt.py`

**Interfaces:**
- Consumes: `guardrails.CHILD_SAFETY_CLAUSE`。
- Produces: `build_live_system_prompt(target_sentence, directive, topic=None) -> str`（新增 `topic` 參數，向後相容）。

- [ ] **Step 1: Write the failing test（追加新測試）**

```python
# 追加到 tests/test_scaffold_live_prompt.py
from server import scaffold


def test_live_prompt_coach_and_shadowing():
    p = scaffold.build_live_system_prompt("I see a dog.", None, topic="animal")
    assert "教練" in p
    assert "跟讀" in p
    assert "I see a dog." in p
    assert "animal" in p


def test_live_prompt_backward_compat_two_args():
    p = scaffold.build_live_system_prompt(None, None)
    assert isinstance(p, str) and p
    assert "說說學伴" in p


def test_live_prompt_folds_directive():
    p = scaffold.build_live_system_prompt("Hi.", "【本輪策略】多鼓勵。", topic="food")
    assert "【本輪策略】多鼓勵。" in p
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_scaffold_live_prompt.py -q`
Expected: FAIL（`test_live_prompt_coach_and_shadowing`：舊 frame 無「教練」「跟讀」；`topic` 參數會 TypeError）

- [ ] **Step 3: Write minimal implementation**

替換 `server/scaffold.py` 的 `_LIVE_STATIC_FRAME`：

```python
_LIVE_STATIC_FRAME = (
    "你是陪台灣國小學生練習說話的英文教練企鵝「說說學伴」，溫暖、有耐心、像朋友。"
    "一、主要用繁體中文（台灣用語）回覆，語氣自然、每次回覆不超過兩句話。"
    "二、這一場的任務：帶孩子練「今天的主題」和「今天的目標句」，用「跟讀」的方式"
    "一句一句練，不要漫無目的閒聊。"
    "三、跟讀迴圈，每一輪照這個節奏：先用中文自然引出情境；再清楚放慢說出一句短英文"
    "（今天的目標句或它的小變化）；邀請孩子跟著說一次；孩子說完先具體誇獎，"
    "再溫和修正一兩個發音或用詞並示範一次；孩子跟上就換下一句或延伸一點，"
    "卡住就把句子拆更短、放更慢、再帶一次（降階護信心）。"
    "四、鷹架原則：先示範再邀請、給比孩子程度略高一點的內容（i+1）、"
    "說對給具體正向回饋、說錯溫和重述不指責、循序漸進不催促。"
    "五、不要每次都用同一句開場白，一次只給一句，不要長篇，不使用 markdown 符號或 emoji。"
    "六、務必明顯放慢說話速度，比平常慢很多：一個字一個字清楚地說，"
    "字與字、詞與詞之間都稍微停頓，像對三、四歲小小孩慢慢講話一樣。寧可太慢也不要快。"
)
```

替換 `build_live_system_prompt`：

```python
def build_live_system_prompt(target_sentence, directive, topic=None) -> str:
    """組裝即時陪聊 system prompt（教練角色 + 跟讀迴圈）。

    - target_sentence：今日目標句（可 None）→ 提示教練帶讀、請孩子跟讀。
    - directive：B 軸 companion_directive 注入字串（可 None）。
    - topic：今日主題（可 None）→ 提示先引出主題情境。
    None/空欄位一律略過，向後相容。
    """
    from server import guardrails

    parts = [_LIVE_STATIC_FRAME, guardrails.CHILD_SAFETY_CLAUSE]
    if topic and str(topic).strip():
        parts.append(f"今天的主題是「{str(topic).strip()}」，先用中文自然帶出這個主題的情境。")
    if target_sentence and str(target_sentence).strip():
        parts.append(f"今天想邀請孩子開口說的英文句是：「{str(target_sentence).strip()}」，"
                     "帶讀時放慢、一次一句，請孩子跟著說一次，再給回饋。")
    if directive and str(directive).strip():
        parts.append(str(directive).strip())
    return "".join(parts)
```

- [ ] **Step 4: Run whole file; fix any old assertion pinning removed wording**

Run: `.venv/bin/pytest tests/test_scaffold_live_prompt.py -q`
Expected: PASS。若既有測試斷言舊字串（如舊 frame 用語）而失敗，把該斷言改成仍存在的關鍵字（可用：`"說說學伴"`、`"帶讀"`、`"放慢"`、`"跟讀"`）。舊的 2 參數呼叫（無 `topic`）因預設 `None` 仍相容，勿改其呼叫方式。

- [ ] **Step 5: Commit**

```bash
git add server/scaffold.py tests/test_scaffold_live_prompt.py
git commit -m "feat(scaffold): live prompt 改教練角色+跟讀迴圈，build_live_system_prompt 加 topic"
```

---

### Task 4: `ws_live` 連線接 build_lesson（app.py）

**Files:**
- Modify: `server/app.py`（import ≈28、`ws_live` 內 ≈429-431）
- Test: `tests/test_app_live.py`

**Interfaces:**
- Consumes: `lesson.build_lesson`（Task 2）、`scaffold.build_live_system_prompt`（Task 3）、`store.list_diagnoses()`、`store.get_profile()`。
- Produces: 模組函式 `_build_live_prompt() -> str`（可離線單元測試）。

- [ ] **Step 1: Write the failing test**

```python
# 追加到 tests/test_app_live.py
from server import app as app_module


def test_build_live_prompt_uses_lesson(monkeypatch):
    from server import lesson, scaffold
    monkeypatch.setattr(app_module.store, "list_diagnoses", lambda: [])
    monkeypatch.setattr(app_module.store, "get_profile", lambda: None)
    monkeypatch.setattr(
        lesson, "build_lesson",
        lambda diags, prof: lesson.Lesson("food", "I want to eat an apple.",
                                          "單字", "【策略】多鼓勵。"))
    p = app_module._build_live_prompt()
    assert "I want to eat an apple." in p
    assert "food" in p
    assert "【策略】多鼓勵。" in p
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_app_live.py::test_build_live_prompt_uses_lesson -q`
Expected: FAIL（`AttributeError: module 'server.app' has no attribute '_build_live_prompt'`）

- [ ] **Step 3: Write minimal implementation**

在 `server/app.py` import 群組（第 28 行）加入 `lesson`：

```python
from server import config, diagnose, guardrails, lesson, nova_sonic, profile, scaffold, store
```

在 `ws_live` 上方（模組層，靠近 `_store_live_turn` 附近）新增 helper：

```python
def _build_live_prompt() -> str:
    """組本場 live system prompt：選教材（build_lesson）→ 教練跟讀 prompt。"""
    lp = lesson.build_lesson(store.list_diagnoses(), store.get_profile())
    return scaffold.build_live_system_prompt(
        lp.target_sentence, lp.directive, topic=lp.topic)
```

把 `ws_live` 內原本這三行（≈429-431）：

```python
    directive = getattr(pipeline, "_directive", None)
    target = "How are you today?"  # Phase 1 起始目標句（詞庫通用引導句）
    system_prompt = scaffold.build_live_system_prompt(target, directive)
```

替換為：

```python
    system_prompt = _build_live_prompt()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_app_live.py::test_build_live_prompt_uses_lesson -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/app.py tests/test_app_live.py
git commit -m "feat(live): ws_live 連線改用 build_lesson 組教學 prompt（取代寫死 target）"
```

---

### Task 5: 場末背景診斷回寫（元件④，app.py）

**Files:**
- Modify: `server/app.py`（`ws_live` 收尾 `finally` ≈549-552 之後；helper 靠近 `_store_live_turn`）
- Test: `tests/test_app_live.py`

**Interfaces:**
- Consumes: `store.list_interactions(limit)`、`store.list_diagnoses()`、`store.get_profile()`、`diagnose.generate_diagnosis(interactions, prev, profile=None)`、`store.add_diagnosis(diag)`。
- Produces: 模組函式 `_run_live_diagnosis() -> None`（離線可測；<2 筆 live 互動則跳過）。

- [ ] **Step 1: Write the failing test**

```python
# 追加到 tests/test_app_live.py
def test_run_live_diagnosis_records(monkeypatch):
    recorded = {}
    live = [{"source": "live_s2s", "asr_text": "dog", "reply_text": "Say dog"},
            {"source": "live_s2s", "asr_text": "cat", "reply_text": "Say cat"}]
    monkeypatch.setattr(app_module.store, "list_interactions", lambda limit=50: live)
    monkeypatch.setattr(app_module.store, "list_diagnoses", lambda: [])
    monkeypatch.setattr(app_module.store, "get_profile", lambda: None)
    monkeypatch.setattr(app_module.diagnose, "generate_diagnosis",
                        lambda inter, prev, profile=None: {"ok": True})
    monkeypatch.setattr(app_module.store, "add_diagnosis",
                        lambda d: recorded.setdefault("diag", d))
    app_module._run_live_diagnosis()
    assert recorded.get("diag") == {"ok": True}


def test_run_live_diagnosis_skips_when_too_few(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(app_module.store, "list_interactions",
                        lambda limit=50: [{"source": "live_s2s", "asr_text": "hi"}])
    monkeypatch.setattr(app_module.store, "add_diagnosis",
                        lambda d: called.__setitem__("n", called["n"] + 1))
    app_module._run_live_diagnosis()
    assert called["n"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_app_live.py::test_run_live_diagnosis_records -q`
Expected: FAIL（`AttributeError: ... '_run_live_diagnosis'`）

- [ ] **Step 3: Write minimal implementation**

在 `server/app.py` 靠近 `_store_live_turn` 新增：

```python
def _run_live_diagnosis() -> None:
    """場末回寫診斷：對本場 live 逐字稿跑一次 generate_diagnosis 並存，
    供下一場 build_lesson 自適應。失敗只記 log、不影響關閉。"""
    try:
        recent = [i for i in store.list_interactions(limit=20)
                  if i.get("source") == "live_s2s"][-10:]
        if len(recent) < 2:
            return
        diags = store.list_diagnoses()
        prev = diags[-1] if diags else None
        diag = diagnose.generate_diagnosis(recent, prev, store.get_profile())
        store.add_diagnosis(diag)
    except Exception:
        logger.exception("live 場末診斷失敗")
```

在 `ws_live` 尾端 `finally` 區塊（`await session.close()` 之後、同一 `finally` 內）追加背景觸發：

```python
    finally:
        try:
            await session.close()
        except Exception:
            pass
        try:
            await asyncio.to_thread(_run_live_diagnosis)
        except Exception:
            logger.exception("live 場末診斷觸發失敗")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_app_live.py -q`
Expected: PASS（含兩個新測試 + 既有 live 測試）

- [ ] **Step 5: Commit**

```bash
git add server/app.py tests/test_app_live.py
git commit -m "feat(live): 場末背景診斷回寫（元件④），下一場 build_lesson 自適應"
```

---

### Task 6: 全套回歸 + 手動驗證

**Files:** 無（驗證）

- [ ] **Step 1: 跑相關測試全綠**

Run: `.venv/bin/pytest tests/test_lesson.py tests/test_scaffold_live_prompt.py tests/test_app_live.py -q`
Expected: 全 PASS。

- [ ] **Step 2: 跑全套確認無退步**

Run: `.venv/bin/pytest -q`
Expected: 新測試 PASS；既有已知環境雜訊（`server/streaming` 缺 WAV fixture、`spike/a2_pipecat` py3.12 audioop）維持原狀，與本線無關。

- [ ] **Step 3: 手動端到端（真機，沿用喚醒 e2e checklist）**

- 啟動 server（帶憑證環境）。
- 喊「說說學伴」→ 確認 Nova 開場即**介紹今日主題並帶讀目標句、邀請跟讀**（非泛聊、非每次同一句開場）。
- 連續練兩場（第一場練得順）：觀察第二場主題/難度是否可見漸進（元件④生效）。
- 記錄結果；若 prompt 行為需微調，回 Task 3 調整 `_LIVE_STATIC_FRAME` 措辭。

---

## 備註
- 本計畫不 commit 前一 session 已在工作樹的未提交變更（`server/app.py` COOP/COEP、`server/scaffold.py` 放慢 prompt、`tests/test_cross_origin_isolation.py`）；那些屬另一線，各自處理。**注意 Task 3/4/5 會再改 `app.py`/`scaffold.py`，commit 前用 `git add` 精準只加本計畫相關 hunk**（必要時 `git add -p`）。
- 🔴 安全：外露 IAM key `AKIAZKQ2XL7Q6MQTTIMY` 仍待撤（與本線無關，另處理）。
