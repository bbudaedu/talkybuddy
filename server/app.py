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

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from server import auth, config, diagnose, guardrails, profile, store
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
# 承接佈署 profile 預設：cloud profile → 全語音走雲端管線；edge → 邊緣本地。
pipeline.network_mode = config.default_network_mode()


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
        "cloud_llm": bool(cloud_llm_engine.available()),
        "network_mode": pipeline.network_mode,
        "pending": store.pending_count(),
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


class LoginBody(BaseModel):
    email: str
    password: str


class NetworkModeBody(BaseModel):
    """POST /api/network_mode 的 body。"""

    mode: str


@app.post("/api/login")
async def api_login(body: LoginBody):
    ident = auth.authenticate(body.email, body.password)
    if ident is None:
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")
    token = auth.issue_token(ident["sub"], ident["role"])
    return {"token": token, "role": ident["role"], "sub": ident["sub"]}


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
        # 持久化那份帶上 student_id 供 /api/diagnoses 依學生過濾（Task 1/4）；
        # 回應的 new_diagnosis 維持既有契約 key 集合，故存副本、不動 new_diag。
        store.add_diagnosis({**new_diag, "student_id": store._student_id()})
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


def identity_from_header(authorization: str | None) -> dict:
    """從 Authorization header 解出 JWT claims；缺/壞格式/過期都 401。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少或格式錯誤的 token")
    try:
        return auth.verify_token(authorization[len("Bearer "):])
    except auth.InvalidToken:
        raise HTTPException(status_code=401, detail="token 無效或過期")


def _resolve_student(claims: dict, student_query: str | None) -> str:
    """依角色決定要查哪個學生：student 只能查自己；tutor/device 需帶 ?student=。"""
    if claims["role"] == "student":
        return claims["sub"]
    # tutor / device：需明確指定學生
    if not student_query:
        raise HTTPException(status_code=400, detail="tutor 需帶 ?student=<id>")
    return student_query


@app.get("/api/interactions")
async def api_interactions(limit: int = 50, student: str | None = None,
                           authorization: str | None = Header(default=None)):
    """最近互動紀錄（新→舊）；student 讀自己，tutor/device 需帶 ?student=。"""
    claims = identity_from_header(authorization)
    sid = _resolve_student(claims, student)
    return store.list_interactions(limit=limit, student_id=sid)


@app.get("/api/diagnoses")
async def api_diagnoses(student: str | None = None,
                        authorization: str | None = Header(default=None)):
    """全部診斷（date 升冪）；student 讀自己，tutor/device 需帶 ?student=。"""
    claims = identity_from_header(authorization)
    sid = _resolve_student(claims, student)
    return store.list_diagnoses(student_id=sid)


class SyncBody(BaseModel):
    interactions: list[dict]


@app.post("/api/sync")
async def api_sync(body: SyncBody, authorization: str | None = Header(default=None)):
    """玩偶上行同步：device token 綁定的學生，批次寫入並去重。

    去重鍵＝同 student_id + device_id + client_ts；重送同批全部跳過。
    回 {"accepted": n, "skipped": m}。
    """
    claims = identity_from_header(authorization)
    sid = claims["sub"]  # device token 綁定的 student
    accepted = skipped = 0
    for it in body.interactions:
        rec = dict(it)
        rec["student_id"] = sid
        dev = str(rec.get("device_id", ""))
        cts = str(rec.get("client_ts", ""))
        if cts and store.interaction_exists(sid, dev, cts):
            skipped += 1
            continue
        store.add_interaction(rec)
        accepted += 1
    return {"accepted": accepted, "skipped": skipped}


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

    # 解析 query ?token=，綁定本連線身份；缺/壞 token → policy close(1008)。
    token = websocket.query_params.get("token")
    try:
        claims = auth.verify_token(token) if token else None
    except auth.InvalidToken:
        claims = None
    if claims is None:
        await websocket.close(code=1008)  # policy violation
        return
    sid = claims["sub"]
    # 每連線一個獨立 VoicePipeline（共用引擎、綁 student_id），解單例污染
    conn_pipe = VoicePipeline(
        asr_engine, llm_engine, tts_engine,
        cloud_tts=cloud_tts_engine, cloud_llm=cloud_llm_engine, student_id=sid,
    )
    conn_pipe.network_mode = pipeline.network_mode  # 承接目前模式

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
            result = await conn_pipe.run_turn_audio(data, emit)
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
                    result = await conn_pipe.run_turn_text(text, emit)
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
