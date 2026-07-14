"""FastAPI 入口（CONTRACTS.md app.py 契約）。

- HTTP：/、/teacher、/api/status、/api/network_mode、/api/interactions、
  /api/diagnoses、/api/seed_reset；web/ 另掛 /static。
- WS /ws/talk：文字 frame（text_input / audio_end）與 binary frame（完整
  webm/ogg 錄音）。binary 相容兩種觸發方式：
  1. binary 之後跟 {"type":"audio_end"} → 收到 audio_end 立即整包處理；
  2. 單獨 binary（無 audio_end）→ 短暫 debounce 後自動整包處理。
- startup（lifespan）：init_db() + seed_demo()；引擎預熱丟 daemon thread，
  不阻擋伺服器啟動。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from server import (
    config, diagnose, guardrails, lesson, nova_sonic, profile, pronunciation,
    scaffold, store,
)
from server.asr import ASREngine
from server.llm import EdgeLLM
from server.cloud_tts import CloudTTS
from server.cloud_llm import CloudLLM
from server.pipeline import VoicePipeline
from server.tts import TTSEngine

WEB_DIR: Path = config.BASE_DIR / "web"

# 單獨 binary（無 audio_end）時的 debounce 秒數：等這麼久沒等到 audio_end
# 就把緩衝整包送進 pipeline。
AUDIO_DEBOUNCE_S: float = 0.35

logger = logging.getLogger("talkybuddy.wake")

# ---------------------------------------------------------------------------
# 全域單例：引擎與 VoicePipeline
# ---------------------------------------------------------------------------

asr_engine = ASREngine()
llm_engine = EdgeLLM()
tts_engine = TTSEngine()
cloud_tts_engine = CloudTTS()
cloud_llm_engine = CloudLLM()
pipeline = VoicePipeline(
    asr_engine, llm_engine, tts_engine,
    cloud_tts=cloud_tts_engine, cloud_llm=cloud_llm_engine,
)


def _make_live_session():
    """建一場 NovaSonicSession（測試以 monkeypatch 換 fake）。"""
    return nova_sonic.NovaSonicSession(
        model_id=config.NOVA_SONIC_MODEL_ID,
        voice=config.NOVA_SONIC_VOICE,
        region=config.BEDROCK_REGION,
    )


def _prewarm_engines() -> None:
    """背景預熱三引擎（懶載入的模型先摸一次），任何失敗都吞掉。"""
    try:
        if asr_engine.available():
            # 私有懶載入入口；失敗由引擎自行記錄降級
            asr_engine._ensure_model()
    except Exception:
        pass
    try:
        if llm_engine.available():
            llm_engine._get_model()
    except Exception:
        pass
    try:
        if tts_engine.available():
            tts_engine._get_voice("zh")
            tts_engine._get_voice("en")
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    """啟動：建表 + 首次種子資料；引擎預熱走 daemon thread 不擋啟動。"""
    store.init_db()
    store.seed_demo()
    threading.Thread(target=_prewarm_engines, daemon=True).start()
    yield


app = FastAPI(title="TalkyBuddy 說說學伴", lifespan=lifespan)


@app.middleware("http")
async def _cross_origin_isolation(request, call_next):
    """全站回應帶 COOP:same-origin + COEP:require-corp。

    sherpa-onnx KWS WASM 以 emscripten `-pthread` 建置，執行時要建立 shared
    WebAssembly.Memory，瀏覽器僅在 ``crossOriginIsolated === true`` 時才允許。
    缺這兩個 header 時，手機端 WASM init 會卡住 → live 喚醒層 Promise chain
    無法完成（畫面卡在「連線中」、點企鵝無反應）。全站頁面與資產皆同源，
    加這兩個 header 對其他資源無副作用。契約見 tests/test_cross_origin_isolation.py。
    """
    response = await call_next(request)
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
    return response


if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


# ---------------------------------------------------------------------------
# 頁面
# ---------------------------------------------------------------------------

@app.get("/")
async def index_page():
    """學生端頁面。"""
    return FileResponse(WEB_DIR / "index.html")


@app.get("/teacher")
async def teacher_page():
    """教師端頁面。"""
    return FileResponse(WEB_DIR / "teacher.html")


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

@app.get("/api/status")
async def api_status():
    """引擎可用性 + 網路模式 + 待同步筆數。"""
    return {
        "asr": bool(asr_engine.available()),
        "llm": bool(llm_engine.available()),
        "tts": bool(tts_engine.available()),
        "cloud_tts": bool(cloud_tts_engine.available()),
        "network_mode": pipeline.network_mode,
        "pending": store.pending_count(),
        "live_s2s": bool(config.LIVE_S2S_ENABLED and nova_sonic.available()),
    }


@app.get("/api/wake-config")
async def api_wake_config():
    """下發 Porcupine Web 喚醒設定給瀏覽器。

    AccessKey 存在 server 環境變數（不進 repo）。enabled=False（未設 key）時
    client 略過語音喚醒、只用 push；行為明確可預期。
    """
    return {
        "enabled": bool(config.PICOVOICE_ACCESS_KEY),
        "access_key": config.PICOVOICE_ACCESS_KEY,
        "keyword_builtin": config.WAKE_KEYWORD_BUILTIN,
        "keyword_label": config.WAKE_KEYWORD_LABEL,
        "keyword_public_path": config.WAKE_KEYWORD_PUBLIC_PATH,
        "model_public_path": config.WAKE_MODEL_PUBLIC_PATH,
        "sensitivity": config.WAKE_SENSITIVITY,
        "sherpa": {
            "enabled": bool(config.WAKE_SHERPA_ENABLED),
            "base_url": config.WAKE_SHERPA_BASE_URL,
            "keywords": config.WAKE_SHERPA_KEYWORDS,
            "keywords_threshold": config.WAKE_SHERPA_THRESHOLD,
            "keywords_score": config.WAKE_SHERPA_SCORE,
        },
    }


class NetworkModeBody(BaseModel):
    """POST /api/network_mode 的 body。"""

    mode: str


@app.post("/api/network_mode")
async def api_network_mode(body: NetworkModeBody):
    """切換網路模式。

    - edge：只更新模式。
    - cloud：mark_all_synced() → 近 10 筆互動 generate_diagnosis（prev=最新
      診斷）→ add_diagnosis → 回 {"synced": n, "new_diagnosis": {...}}。
    """
    mode = body.mode
    if mode not in ("edge", "cloud"):
        raise HTTPException(status_code=400, detail="mode 必須是 'edge' 或 'cloud'")

    # B4-5 consent gate：切雲端前先驗家長同意；未同意 → 強制 edge-only，
    # 不同步、不產雲端診斷、不動 directive 快取（資料不出境）。
    if mode == "cloud" and not guardrails.consent_granted():
        pipeline.network_mode = "edge"
        return {
            "network_mode": "edge",
            "synced": 0,
            "new_diagnosis": None,
            "consent_required": True,
        }

    pipeline.network_mode = mode
    if mode == "edge":
        return {"network_mode": "edge", "synced": 0, "new_diagnosis": None}

    # cloud：補同步 + 產出新診斷（mock Hermes/Bedrock 雲端層）
    just_synced = store.mark_all_synced()
    new_diag = None
    try:
        recent = store.list_interactions(limit=10)
        diagnoses = store.list_diagnoses()  # date 升冪 → 最後一筆是最新
        prev = diagnoses[-1] if diagnoses else None
        new_diag = diagnose.generate_diagnosis(recent, prev)
        store.add_diagnosis(new_diag)
        # B1：把新診斷的 companion_directive 推進 pipeline 快取，即時路徑下輪即採用
        # B3 接法 A：一併帶 level_state，讓 CEFR 難度/語言形式折進注入字串
        pipeline._directive = diagnose.format_directive_for_prompt(
            new_diag.get("companion_directive"), new_diag.get("level_state"))
        # B2：同異步時機更新長期 profile（全量重算；失敗不影響同步）
        all_inter = store.list_interactions(limit=500)
        prof = profile.build_profile(all_inter, store.list_diagnoses(), store.get_profile())
        store.save_profile(prof)
    except Exception:
        # 診斷失敗不影響同步結果（demo 韌性優先）
        new_diag = None
    return {
        "network_mode": "cloud",
        "synced": len(just_synced),
        "new_diagnosis": new_diag,
    }


@app.get("/api/interactions")
async def api_interactions(limit: int = 50):
    """最近互動紀錄（新→舊）。"""
    return store.list_interactions(limit=limit)


@app.get("/api/diagnoses")
async def api_diagnoses():
    """全部診斷（date 升冪）。"""
    return store.list_diagnoses()


@app.post("/api/seed_reset")
async def api_seed_reset():
    """清空兩表並重灌示範資料（demo 重置）。"""
    store.init_db()
    with store._lock:  # 借用 store 模組的共用連線與鎖
        conn = store._get_conn()
        conn.execute("DELETE FROM interactions")
        conn.execute("DELETE FROM diagnoses")
        conn.commit()
    store.seed_demo()
    return {"ok": True}


# ---------------------------------------------------------------------------
# WebSocket /ws/talk
# ---------------------------------------------------------------------------

@app.websocket("/ws/talk")
async def ws_talk(websocket: WebSocket):
    """語音/文字對話 WebSocket（協定見 CONTRACTS.md）。

    binary frame 累積進緩衝；收到 {"type":"audio_end"} 立即整包處理；
    若只收到 binary（前端省略 audio_end），debounce 逾時後自動處理。
    任何例外都 catch 住，不讓 server 掛掉。
    """
    await websocket.accept()

    send_lock = asyncio.Lock()

    async def emit(payload: dict) -> None:
        """統一出口：序列化送 JSON（多工發送用鎖保護；斷線時吞例外）。"""
        try:
            async with send_lock:
                await websocket.send_json(payload)
        except Exception:
            pass

    audio_buffer = bytearray()
    flush_task: asyncio.Task | None = None

    async def send_turn_result(result, include_asr: bool) -> None:
        """把 TurnResult 依協定送回：asr_result → reply → tts_audio/unavailable。"""
        if result is None:  # busy：pipeline 已 emit {"type":"busy"}
            return
        if include_asr:
            await emit({
                "type": "asr_result",
                "text": result.asr_text,
                "confidence": result.asr_conf,
            })
        await emit({
            "type": "reply",
            "text": result.reply_text,
            "scores": result.scores,
            "latency_ms": result.latency_ms,
            "fallback": bool(result.fallback),
            "seq": int(result.seq),
        })
        if result.tts_wav:
            await emit({
                "type": "tts_audio",
                "wav_b64": base64.b64encode(result.tts_wav).decode("ascii"),
            })
        else:
            await emit({"type": "tts_unavailable"})

    async def process_audio_buffer() -> None:
        """把緩衝的錄音整包送進 pipeline（空緩衝直接略過）。"""
        if not audio_buffer:
            return
        data = bytes(audio_buffer)
        audio_buffer.clear()
        try:
            result = await pipeline.run_turn_audio(data, emit)
            await send_turn_result(result, include_asr=True)
        except Exception:
            # 單輪失敗不斷線：回 idle 讓前端解除等待
            await emit({"type": "state", "state": "idle"})

    async def debounce_flush() -> None:
        """單獨 binary 的相容路徑：等不到 audio_end 就自動觸發。"""
        try:
            await asyncio.sleep(AUDIO_DEBOUNCE_S)
            await process_audio_buffer()
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    def cancel_flush() -> None:
        nonlocal flush_task
        if flush_task is not None and not flush_task.done():
            flush_task.cancel()
        flush_task = None

    try:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break

            # ---- binary frame：一段完整 webm/ogg 錄音 ----
            if msg.get("bytes") is not None:
                data = msg["bytes"]
                if data:
                    audio_buffer.extend(data)
                    # 重設 debounce：等 audio_end，等不到就自動處理
                    cancel_flush()
                    flush_task = asyncio.create_task(debounce_flush())
                continue

            # ---- 文字 frame：JSON 指令 ----
            raw = msg.get("text")
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            mtype = payload.get("type")

            if mtype == "audio_end":
                cancel_flush()
                await process_audio_buffer()
            elif mtype == "wake":
                # A1 喚醒事件：純記錄（source = "wakeword" | "push"），不改變一輪對話行為。
                source = str(payload.get("source", "") or "unknown")
                logger.info("wake event: source=%s", source)
            elif mtype == "text_input":
                text = str(payload.get("text", "") or "")
                try:
                    result = await pipeline.run_turn_text(text, emit)
                    await send_turn_result(result, include_asr=False)
                except Exception:
                    await emit({"type": "state", "state": "idle"})
            # 其他型別：忽略（前向相容）
    except WebSocketDisconnect:
        pass
    except Exception:
        # 任何未預期例外都不讓 server 掛掉
        pass
    finally:
        cancel_flush()


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket):
    """即時全雙工 S2S（Nova Sonic）：與 /ws/talk 並存。

    連線先過 consent + available gate；之後上行 PCM16(16k) → send_audio，
    下行 Nova Sonic audio(24k)/transcript 轉發；user_end → end_user_turn 後
    迭代 events() 直到 turn_end；turn_end 把 USER/ASSISTANT transcript 落地。
    """
    await websocket.accept()

    send_lock = asyncio.Lock()

    async def emit(payload: dict) -> None:
        try:
            async with send_lock:
                await websocket.send_json(payload)
        except Exception:
            pass

    async def emit_bytes(data: bytes) -> None:
        try:
            async with send_lock:
                await websocket.send_bytes(data)
        except Exception:
            pass

    # gate：consent 優先（資料出境），再 available
    if not guardrails.consent_granted():
        await emit({"type": "live_error", "reason": "consent_required"})
        await websocket.close()
        return
    if not (config.LIVE_S2S_ENABLED and nova_sonic.available()):
        await emit({"type": "live_error", "reason": "unavailable"})
        await websocket.close()
        return

    # 本場教材：由最新診斷 + profile 選今日主題/目標句/策略（安全退化，永不擋 live）。
    # 同一份 Lesson 供「發音評測參考句 target」與「教練跟讀 system_prompt」，單一真相來源。
    live_lesson = lesson.build_lesson(store.list_diagnoses(), store.get_profile())
    target = live_lesson.target_sentence  # 發音評測參考句 = 本場跟讀目標句
    system_prompt = scaffold.build_live_system_prompt(
        live_lesson.target_sentence, live_lesson.directive, topic=live_lesson.topic)

    session = _make_live_session()
    turn_user, turn_asst = [], []

    # 發音評測（B 軸背景）：available 才 tee 使用者上行 PCM，turn_end 背景評分。
    # 不可用→不 tee、不評分，行為與現行全雙工一致（降級安全）。
    pron_on = pronunciation.available()
    pcm_buf = bytearray()

    async def _finish_turn() -> None:
        """一輪結束：對已緩衝的使用者 PCM 背景評分（有逾時上限），把 pron 併入落地。"""
        pron = None
        if pron_on and pcm_buf:
            try:
                pron = await asyncio.wait_for(
                    asyncio.to_thread(_pcm_to_pron_score, bytes(pcm_buf), target),
                    timeout=config.PRON_SCORE_TIMEOUT_S,
                )
            except Exception:
                pron = None
        pcm_buf.clear()
        _store_live_turn(turn_user, turn_asst, pron)
        turn_user.clear()
        turn_asst.clear()
        await emit({"type": "turn_end"})

    async def drain_events() -> None:
        """迭代模型事件轉發前端；turn_end 落地 transcript。"""
        async for ev in session.events():
            if ev.kind == "audio":
                await emit_bytes(ev.audio)
            elif ev.kind == "transcript":
                await emit({"type": "live_transcript", "role": ev.role, "text": ev.text})
                if ev.role == "USER":
                    turn_user.append(ev.text)
                elif ev.role == "ASSISTANT":
                    turn_asst.append(ev.text)
            elif ev.kind == "turn_end":
                await _finish_turn()

    # hands-free 連續模式（?mode=continuous）：上下行雙 Task 常駐，turn 邊界交給 Nova VAD。
    continuous = websocket.query_params.get("mode") == "continuous"

    async def _uplink() -> None:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                return
            if msg.get("bytes") is not None:
                if msg["bytes"]:
                    await session.send_audio(msg["bytes"])
                    if pron_on:
                        pcm_buf.extend(msg["bytes"])   # tee 一份供 turn_end 評分
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
            if ev.kind == "audio":
                await emit_bytes(ev.audio)
            elif ev.kind == "transcript":
                await emit({"type": "live_transcript", "role": ev.role, "text": ev.text})
                if ev.role == "USER":
                    turn_user.append(ev.text)
                elif ev.role == "ASSISTANT":
                    turn_asst.append(ev.text)
            elif ev.kind == "turn_end":
                await _finish_turn()
            elif ev.kind == "interrupt":
                # barge-in：轉發給前端停播 + 切 listen（Nova server VAD 偵測到插話）
                await emit({"type": "interrupt"})

    try:
        await session.start(system_prompt)
        if continuous:
            up = asyncio.create_task(_uplink())
            down = asyncio.create_task(_downlink())
            _, pending = await asyncio.wait(
                {up, down}, return_when=asyncio.FIRST_COMPLETED)
            # 乾淨關法（避免 teardown race）：先 close session，讓內部 receive loop
            # 收斂並對 events_continuous 送 None 哨兵 → downlink 自然結束；再取消殘留
            # task 並 await 吞掉 CancelledError，避免 SDK receive() 被硬取消時丟
            # InvalidStateError。close() 每步吞例外、可與 finally 的再次 close 疊。
            try:
                await session.close()
            except Exception:
                pass
            # downlink drain：uplink 先收尾（掰掰/斷線）時，最後一輪 turn_end 可能還在
            # 佇列/評分中。直接 cancel 會砍掉該輪發音評測與落地（手機真機驗收踩到），
            # 故有界等待 downlink 把剩餘事件 drain 完（發音評測最久 PRON_SCORE_TIMEOUT_S）；
            # 逾時由 wait_for 自動 cancel 保底、不 hang。
            if down in pending:
                try:
                    await asyncio.wait_for(
                        down, timeout=config.PRON_SCORE_TIMEOUT_S + 2.0)
                except Exception:
                    pass
            for t in pending:
                if not t.done():
                    t.cancel()
                try:
                    await t
                except BaseException:
                    pass
        else:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    if msg["bytes"]:
                        await session.send_audio(msg["bytes"])
                        if pron_on:
                            pcm_buf.extend(msg["bytes"])   # tee 供 turn_end 評分
                    continue
                raw = msg.get("text")
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except Exception:
                    continue
                mtype = payload.get("type")
                if mtype == "user_end":
                    await session.end_user_turn()
                    await drain_events()
                elif mtype == "bye":
                    break
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await emit({"type": "live_error", "reason": "stream_error"})
        except Exception:
            pass
    finally:
        try:
            await session.close()
        except Exception:
            pass
        try:
            await asyncio.to_thread(_run_live_diagnosis)
        except Exception:
            logger.exception("live 場末診斷觸發失敗")


def _build_live_prompt() -> str:
    """組本場 live system prompt：選教材（build_lesson）→ 教練跟讀 prompt。

    離線可單元測試的 seam；ws_live 連線時另建同一份 Lesson 以同時取得發音
    評測參考句（target）與 prompt。build_lesson 全程安全退化，永不擋 live。
    """
    lp = lesson.build_lesson(store.list_diagnoses(), store.get_profile())
    return scaffold.build_live_system_prompt(
        lp.target_sentence, lp.directive, topic=lp.topic)


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


def _pcm_to_pron_score(pcm: bytes, reference: str, rate: int = 16000) -> float | None:
    """把一輪使用者 PCM16 mono 組成暫存 wav → pronunciation.score → 用後即刪。

    定位＝B 軸背景聲學評測（見 server/pronunciation.py，路 A）。純同步、CPU 密集，
    由呼叫端丟 asyncio.to_thread + 逾時包起來，避免擋事件迴圈。
    不可用／空 PCM／空 reference／任何例外 → None（呼叫端維持既有行為）。
    隱私：暫存 wav 評完即刪、只回 0-100 分數。
    """
    if not pcm or not (reference or "").strip():
        return None
    if not pronunciation.available():
        return None
    import os
    import tempfile
    import wave

    path = None
    try:
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)      # PCM16
            w.setframerate(rate)   # 上行＝16kHz mono（web/live-client.js downsampleTo16k）
            w.writeframes(pcm)
        return pronunciation.score(path, reference)
    except Exception:
        return None
    finally:
        if path:
            try:
                os.unlink(path)
            except Exception:
                pass


def _store_live_turn(
    user_texts: list[str], asst_texts: list[str], pron: float | None = None
) -> None:
    """把一輪 live transcript 落地；pron 有值時寫 scores["pronunciation"]。失敗不影響串流。"""
    asr_text = " ".join(t for t in user_texts if t).strip()
    reply_text = " ".join(t for t in asst_texts if t).strip()
    if not (asr_text or reply_text):
        return
    scores: dict = {}
    if pron is not None:
        scores["pronunciation"] = float(pron)
    try:
        store.add_interaction({
            "asr_text": asr_text,
            "asr_conf": 1.0,
            "reply_text": reply_text,
            "scores": scores,
            "source": "live_s2s",
        })
    except Exception:
        logger.exception("live transcript 落地失敗")
