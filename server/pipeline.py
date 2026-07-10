"""VoicePipeline 狀態機（SPEC v2 §5.1、CONTRACTS.md pipeline 契約）。

流程：webm → ffmpeg 轉 16kHz mono wav → ASR → 鷹架引擎（scaffold）→
（LLM 可用時加值生成，逾時/失敗降級回 scaffold）→ TTS → SQLite。

設計重點：
- 依賴注入：__init__ 收 asr/llm/tts 實例，測試可傳 stub。
- 半雙工：asyncio.Lock，單一 session 同時只跑一輪，重入 emit busy 並回 None。
- 低信心（conf < ASR_CONF_THRESHOLD 或空字串）→ FALLBACK_LINES 輪替、不寫 DB。
- 每階段透過 emit(dict) async callback 送 {"type":"state","state":...} 事件。
"""

from __future__ import annotations

import asyncio
import datetime
import itertools
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field

from server import config, scaffold, store

# LLM 加值生成的逾時秒數（契約：>8s 即降級用 scaffold 結果；測試可 monkeypatch）
LLM_TIMEOUT_S: float = 8.0

# ffmpeg 轉檔逾時秒數
FFMPEG_TIMEOUT_S: float = 10.0

# 每 N 個「成功回合」觸發一次背景導師更新（回寫 companion_directive）
DIRECTIVE_REFRESH_EVERY: int = 5


@dataclass
class TurnResult:
    """單輪對話的完整結果（欄位名依 CONTRACTS.md 逐字一致）。"""

    state_events: list[str] = field(default_factory=list)
    asr_text: str = ""
    asr_conf: float = 0.0
    reply_text: str = ""
    tts_wav: bytes | None = None
    scores: dict = field(default_factory=dict)
    latency_ms: dict = field(default_factory=dict)
    fallback: bool = False
    seq: int = 0


def _now_iso_taipei() -> str:
    """回傳台北時區（UTC+8）的 ISO8601 時間字串。"""
    tz = datetime.timezone(datetime.timedelta(hours=8))
    return datetime.datetime.now(tz).isoformat(timespec="seconds")


def _webm_to_wav(webm_bytes: bytes) -> str | None:
    """把 webm/ogg 錄音 bytes 轉成 16kHz mono wav 暫存檔，回傳 wav 路徑。

    以 subprocess 呼叫 ffmpeg（-loglevel error，timeout=10s），
    失敗（ffmpeg 不存在 / 轉檔錯誤 / 逾時）回 None，由呼叫端走兜底路徑。
    此函式為同步阻塞，呼叫端應以 asyncio.to_thread 執行。

    PLAN.md 要求捨棄 ffmpeg、改用 Python soundfile 記憶體內解碼。
    已實測（.venv）：soundfile 0.14.0 綁定的 libsndfile 1.2.2
    `available_formats()` 不含 WEBM/Opus（無此容器解碼器），
    瀏覽器 MediaRecorder 錄出的 audio/webm;codecs=opus 無法直接餵給
    soundfile，故 PC 原型仍保留 ffmpeg subprocess 轉檔以維持可運行。
    Genio 520 移植階段改用 ALSA 直接錄 16kHz mono wav（跳過瀏覽器
    MediaRecorder/webm），屆時輸入本就是 wav，可直接用 soundfile
    讀取、完全省去本函式與 ffmpeg 依賴。
    """
    webm_path = None
    wav_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
            f.write(webm_bytes)
            webm_path = f.name
        fd, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", webm_path,
                "-ar", "16000", "-ac", "1", "-f", "wav",
                wav_path,
            ],
            capture_output=True,
            timeout=FFMPEG_TIMEOUT_S,
        )
        if proc.returncode != 0 or not os.path.getsize(wav_path):
            raise RuntimeError(f"ffmpeg failed: {proc.stderr.decode(errors='replace')[:200]}")
        return wav_path
    except Exception:
        # 轉檔失敗：清掉 wav 暫存檔，回 None 走兜底
        if wav_path:
            try:
                os.unlink(wav_path)
            except OSError:
                pass
        return None
    finally:
        # webm 原始暫存檔一律清除
        if webm_path:
            try:
                os.unlink(webm_path)
            except OSError:
                pass


class VoicePipeline:
    """單一 session 的語音對話狀態機（半雙工）。"""

    def __init__(self, asr, llm, tts, cloud_tts=None, cloud_llm=None):
        """依賴注入 ASR / LLM / TTS 引擎實例（測試可傳 stub）。

        cloud_tts：選填的雲端 TTS（CloudTTS，同 available()/synth() 契約）；
        None（預設）→ 只走邊緣 TTS，向後相容既有呼叫端與測試。

        cloud_llm：選填的雲端 LLM provider（CloudLLM，同 available()/generate()
        契約）；network_mode=cloud 時優先於本地 llm，None（預設）→ 純本地，
        行為與現況一致（向後相容）。
        """
        self.asr = asr
        self.llm = llm
        self.tts = tts
        self.cloud_tts = cloud_tts
        self.cloud_llm = cloud_llm
        # "edge" | "cloud"，由 app.py 切換
        self.network_mode: str = "edge"
        # 半雙工鎖：同時只跑一輪
        self._lock = asyncio.Lock()
        # 兜底話術輪替器
        self._fallback_cycle = itertools.cycle(scaffold.FALLBACK_LINES)
        # B1 雙 Agent 閉環：已格式化的陪聊策略字串（即時路徑只讀這個，零 DB/網路）
        self._directive: str | None = None
        # 累計「成功回合」數，用於每 N 輪觸發背景導師更新
        self._turn_count: int = 0
        # 背景刷新防重入旗標
        self._directive_refreshing: bool = False

    # ---------- 對外入口 ----------

    async def run_turn_audio(self, webm_bytes: bytes, emit) -> TurnResult | None:
        """語音輪：webm bytes → ffmpeg 轉 wav → ASR → 共同文字流程。

        半雙工：若上一輪還在跑，emit {"type":"busy"} 並回 None。
        """
        if self._lock.locked():
            await emit({"type": "busy"})
            return None
        async with self._lock:
            result = TurnResult()
            t0 = time.monotonic()

            # 階段：ASR（含轉檔）
            await self._emit_state(emit, result, "asr")
            t_asr = time.monotonic()
            wav_path = await asyncio.to_thread(_webm_to_wav, webm_bytes)
            text, conf = "", 0.0
            if wav_path is not None:
                try:
                    if self.asr is not None and self.asr.available():
                        text, conf = await asyncio.to_thread(self.asr.transcribe, wav_path)
                except Exception:
                    text, conf = "", 0.0
                finally:
                    try:
                        os.unlink(wav_path)
                    except OSError:
                        pass
            result.latency_ms["asr"] = int((time.monotonic() - t_asr) * 1000)
            result.asr_text = (text or "").strip()
            result.asr_conf = float(conf)

            return await self._process_text(result, emit, t0)

    async def run_turn_text(self, text: str, emit) -> TurnResult | None:
        """文字輪（快速語句）：跳過 ASR，直接走共同文字流程（asr_conf=1.0）。"""
        if self._lock.locked():
            await emit({"type": "busy"})
            return None
        async with self._lock:
            result = TurnResult()
            t0 = time.monotonic()
            result.latency_ms["asr"] = 0
            result.asr_text = (text or "").strip()
            result.asr_conf = 1.0
            return await self._process_text(result, emit, t0)

    # ---------- 內部流程 ----------

    async def _emit_state(self, emit, result: TurnResult, state: str) -> None:
        """emit 狀態事件並記錄到 state_events。"""
        result.state_events.append(state)
        await emit({"type": "state", "state": state})

    async def _process_text(self, result: TurnResult, emit, t0: float) -> TurnResult:
        """共同文字流程：低信心兜底 / scaffold → LLM 加值 → TTS → 寫 DB。"""
        # 低信心或空字串 → 兜底話術輪替，不寫 DB
        if (not result.asr_text) or result.asr_conf < config.ASR_CONF_THRESHOLD:
            result.fallback = True
            result.reply_text = next(self._fallback_cycle)
            result.latency_ms.setdefault("llm", 0)
            segments = scaffold.split_tts_segments(result.reply_text)
            await self._synth_tts(result, emit, segments)
            result.latency_ms["round_total"] = int((time.monotonic() - t0) * 1000)
            await self._emit_state(emit, result, "idle")
            return result

        # 階段：thinking（鷹架 + LLM）
        await self._emit_state(emit, result, "thinking")
        scaffold.safety_check(result.asr_text)  # 禁詞檢查（respond 內部亦會處理安撫話術）
        sc = scaffold.respond(result.asr_text)
        result.reply_text = sc.reply_text
        result.scores = dict(sc.scores)
        segments = list(sc.tts_segments)

        # LLM 加值：cloud 模式先試雲端 provider，None/逾時降級回本地 llm，
        # 再 None 由後續 scaffold 兜底。逾時/例外一律不拋、不阻塞即時路徑。
        t_llm = time.monotonic()
        llm_text: str | None = None
        try:
            if (
                self.network_mode == "cloud"
                and self.cloud_llm is not None
                and self.cloud_llm.available()
            ):
                llm_text = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.cloud_llm.generate, result.asr_text, sc, self._directive
                    ),
                    timeout=LLM_TIMEOUT_S,
                )
            if llm_text is None and self.llm is not None and self.llm.available():
                llm_text = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.llm.generate, result.asr_text, sc, self._directive
                    ),
                    timeout=LLM_TIMEOUT_S,
                )
        except Exception:
            llm_text = None
        result.latency_ms["llm"] = int((time.monotonic() - t_llm) * 1000)
        if llm_text and isinstance(llm_text, str) and llm_text.strip():
            result.reply_text = llm_text.strip()
            segments = scaffold.split_tts_segments(result.reply_text)

        # 階段：TTS
        await self._synth_tts(result, emit, segments)

        # 寫 DB（低信心兜底已在前面 return，不會到這裡）
        result.latency_ms["round_total"] = int((time.monotonic() - t0) * 1000)
        try:
            result.seq = store.add_interaction(
                {
                    "device_id": config.DEVICE_ID,
                    "student_id": config.STUDENT_ID,
                    "ts": _now_iso_taipei(),
                    "network_mode": self.network_mode,
                    "student_text": result.asr_text,
                    "asr_confidence": round(result.asr_conf, 4),
                    "ai_response_text": result.reply_text,
                    "scores": result.scores,
                    "latency_ms": dict(result.latency_ms),
                    "synced": self.network_mode == "cloud",
                }
            )
        except Exception:
            # DB 寫入失敗不阻斷回覆（demo 韌性優先）
            result.seq = 0

        # B1：每 N 個成功回合，背景（不 await）觸發導師更新 companion_directive
        self._turn_count += 1
        if DIRECTIVE_REFRESH_EVERY > 0 and self._turn_count % DIRECTIVE_REFRESH_EVERY == 0:
            asyncio.create_task(self._refresh_directive())

        await self._emit_state(emit, result, "idle")
        return result

    async def _refresh_directive(self) -> None:
        """背景更新 directive：讀 DB→產診斷→存 DB→更新記憶體快取。

        全程在 asyncio.to_thread 執行，導師絕不進即時路徑；失敗維持舊 directive。
        """
        if self._directive_refreshing:
            return
        self._directive_refreshing = True
        try:
            from server import diagnose

            def _work():
                recent = store.list_interactions(limit=10)
                diagnoses = store.list_diagnoses()
                prev = diagnoses[-1] if diagnoses else None
                diag = diagnose.generate_diagnosis(recent, prev)
                store.add_diagnosis(diag)  # 持久化（含 companion_directive）
                # B3 接法 A：帶 level_state，CEFR 難度/語言形式折進注入字串
                return diagnose.format_directive_for_prompt(
                    diag.get("companion_directive"), diag.get("level_state"))

            self._directive = await asyncio.to_thread(_work)
        except Exception:
            pass  # 更新失敗維持舊 directive（或 None），即時路徑不受影響
        finally:
            self._directive_refreshing = False

    async def _synth_tts(self, result: TurnResult, emit, segments: list[tuple[str, str]]) -> None:
        """階段：TTS 合成（to_thread；不可用/失敗 → tts_wav=None，前端降級）。

        cloud 模式先試雲端 CloudTTS，回 None（逾時/斷網/錯誤）→ 靜默降級邊緣 TTSEngine；
        edge 模式完全不碰雲端。任一失敗最終 tts_wav=None，維持既有前端降級行為。
        """
        await self._emit_state(emit, result, "tts")
        t_tts = time.monotonic()
        wav: bytes | None = None
        try:
            if segments:
                if (
                    self.network_mode == "cloud"
                    and self.cloud_tts is not None
                    and self.cloud_tts.available()
                ):
                    wav = await asyncio.to_thread(self.cloud_tts.synth, segments)
                if wav is None and self.tts is not None and self.tts.available():
                    wav = await asyncio.to_thread(self.tts.synth, segments)
        except Exception:
            wav = None
        result.latency_ms["tts_first"] = int((time.monotonic() - t_tts) * 1000)
        result.tts_wav = wav
