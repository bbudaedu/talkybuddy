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
import io
import itertools
import logging
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field

from server import config, guardrails, lesson, scaffold, store

_log = logging.getLogger(__name__)

# LLM 加值生成的逾時秒數（契約：>8s 即降級用 scaffold 結果；測試可 monkeypatch）。
# 此常數套用在 _process_text() 的 `for engine in engines:` 迴圈的**每一個**
# 引擎（cloud 與 edge 皆然），故它實際上是 **edge 引擎的安全邊界**——Phase 8
# 真機實測 edge LLM 單階段可達 4170ms（見 edge/EDGE_TURN_LOOP_VALIDATION.md）。
# 雲端的快速降級改由各雲端引擎自己的內層 urlopen 逾時負責
# （server/cloud_llm.py::_TIMEOUT_S、server/config.py::CLOUD_TTS_TIMEOUT_S）；
# 不要為了縮短雲端降級時間而調降這個值（NETCUT-02／D-03 的調和，見 09-RESEARCH.md
# Pitfall 2）。
LLM_TIMEOUT_S: float = 8.0

# ffmpeg 轉檔逾時秒數
FFMPEG_TIMEOUT_S: float = 10.0

# 每 N 個「成功回合」觸發一次背景導師更新（回寫 companion_directive）
DIRECTIVE_REFRESH_EVERY: int = 5

# 連續幾輪沒命中（scaffold matched=False）才觸發降階簡化提示
# （「連續兩輪答不出來」= 第 3 輪才簡化，而不是第一次沒答對就馬上介入）
STUCK_STREAK_THRESHOLD: int = 2


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
    # 以下供互動紀錄的 metadata 用（長期記憶的原料，見 _persist_turn）。
    # 不進 CONTRACTS.md 的 interaction dict 必要欄位，也不上雲
    # （sync_client.project_for_upload 是白名單）。
    reply_source: str = ""          # cloud / edge / scaffold / game / fallback
    matched: bool | None = None     # 這輪孩子有沒有命中詞庫
    game_kind: str | None = None
    game_turns: int | None = None


def _now_iso_taipei() -> str:
    """回傳台北時區（UTC+8）的 ISO8601 時間字串。"""
    tz = datetime.timezone(datetime.timedelta(hours=8))
    return datetime.datetime.now(tz).isoformat(timespec="seconds")


def _extract_fallback_prompt(diagnoses: list[dict]) -> str | None:
    """從診斷清單（依 date 升冪，最後一筆最新）取出最新的 fallback_prompt。

    真雲端 API 診斷目前不產這個欄位、正規化後是空字串；空字串／缺欄／任何
    格式問題一律回 None，讓呼叫端優雅退化成沒有 stuck_hint 的舊行為。
    """
    if not diagnoses:
        return None
    try:
        cd = diagnoses[-1].get("companion_directive") or {}
        fp = cd.get("fallback_prompt")
        return fp if isinstance(fp, str) and fp.strip() else None
    except Exception:
        return None


def _is_wav_riff(header: bytes) -> bool:
    """判斷前 12 bytes 是否為 RIFF/WAVE magic（WAV 容器）。

    只讀取固定前 12 bytes（offset 0 為 ``RIFF``、offset 8 為 ``WAVE``），
    不對整段音訊內容做假設；命中才進一步交給 soundfile 解析。
    """
    return len(header) >= 12 and header[0:4] == b"RIFF" and header[8:12] == b"WAVE"


class WavSpecMismatchError(ValueError):
    """WAV bytes 的取樣率/聲道不符 16kHz mono，且目前環境無法走 ffmpeg fallback。"""


def _webm_to_wav(webm_bytes: bytes) -> str | None:
    """把錄音 bytes 轉成 16kHz mono wav 暫存檔，回傳 wav 路徑。

    接受兩種輸入：
    - 原生 16kHz mono WAV bytes（Genio 520 ALSA 直接擷取）：以 RIFF/WAVE magic
      偵測命中後，走 soundfile 直讀 fast path，全程不呼叫 ffmpeg 子行程。
      規格不符（非 16k mono）時：edge profile（或 ffmpeg 不可用）明確 raise
      ``WavSpecMismatchError``，不靜默偽成功、不自作 resample；非 edge 且有
      ffmpeg 則退回下方 ffmpeg fallback 分支處理。
    - 瀏覽器 webm/ogg（MediaRecorder audio/webm;codecs=opus）：以 subprocess
      呼叫 ffmpeg（-loglevel error，timeout=10s）轉檔，失敗（ffmpeg 不存在 /
      轉檔錯誤 / 逾時）回 None，由呼叫端走兜底路徑。

    此函式為同步阻塞，呼叫端應以 asyncio.to_thread 執行。

    已實測（.venv）：soundfile 0.14.0 綁定的 libsndfile 1.2.2
    `available_formats()` 不含 WEBM/Opus（無此容器解碼器），故非 WAV 的瀏覽器
    輸入仍無法直接餵給 soundfile，PC 原型保留 ffmpeg subprocess 轉檔以維持
    可運行。
    """
    if _is_wav_riff(webm_bytes[:12]):
        import soundfile as sf  # lazy import，同 asr_sensevoice._read_wav 慣例

        samples, sample_rate = sf.read(io.BytesIO(webm_bytes), dtype="float32", always_2d=False)
        channels = 1 if getattr(samples, "ndim", 1) <= 1 else samples.shape[1]
        if sample_rate == 16000 and channels == 1:
            fd, fast_wav_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            sf.write(fast_wav_path, samples, sample_rate, subtype="PCM_16")
            return fast_wav_path
        # WAV 但規格不符 16k mono：edge（或無 ffmpeg）明確 raise，不靜默偽成功
        if config.PIPELINE_PROFILE == "edge" or shutil.which("ffmpeg") is None:
            raise WavSpecMismatchError(
                "WAV 音訊規格不符：需 16kHz mono，收到取樣率/聲道不符的輸入"
            )
        # 非 edge 且有 ffmpeg → 落回下方既有 ffmpeg fallback 分支

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


# 進行中的小遊戲（server/games.py）。None = 自由對話。
#
# **裝置級單例，不是每個 pipeline 實例一份。** `/api/game` 開的局掛在全域
# pipeline 上，而 `/ws/talk` 每條連線新建自己的 VoicePipeline——狀態放在實例上
# 時，老師開的局孩子那條連線根本看不到（2026-07-29 裝置實測：開了 i_spy，
# 孩子講 "I see a dog."，回覆走自由對話、這局 turns 停在 0）。
#
# 這台裝置前面就坐著那一個孩子，一局遊戲的壽命就是一次對話——裝置級才是這個
# 狀態真正的作用域。不進 DB 的理由不變：存了只多一份要清理的狀態。
#
# ⚠️ 代價：同一個行程的所有連線共用一局。單裝置（玩偶）正確；多個孩子連同一台
# 伺服器時會互相干擾，與 ASR/TTS in-process 單例同級的既有限制。
_active_game = None


class VoicePipeline:
    """單一 session 的語音對話狀態機（半雙工）。"""

    def __init__(self, asr, llm, tts, cloud_tts=None, cloud_llm=None, student_id=None):
        """依賴注入 ASR / LLM / TTS 引擎實例（測試可傳 stub）。

        cloud_tts：選填的雲端 TTS（CloudTTS，同 available()/synth() 契約）；
        None（預設）→ 只走邊緣 TTS，向後相容既有呼叫端與測試。
        student_id：本連線綁定的學生身份；None（預設）→ 寫互動時退回
        config.STUDENT_ID（邊緣單機相容）。
        """
        self.asr = asr
        self.llm = llm
        self.tts = tts
        self.cloud_tts = cloud_tts
        self.cloud_llm = cloud_llm
        # 本連線身份（每連線一個 pipeline 實例，解單例污染）
        self.student_id = student_id
        # "edge" | "cloud"，由 app.py 切換
        self.network_mode: str = "edge"
        # 半雙工鎖：同時只跑一輪
        self._lock = asyncio.Lock()
        # 兜底話術輪替器
        self._fallback_cycle = itertools.cycle(scaffold.FALLBACK_LINES)
        # B1 雙 Agent 閉環：已格式化的陪聊策略字串（即時路徑只讀這個，零 DB/網路）
        self._directive: str | None = None
        # 今日課程（見 server/lesson.py）：主題／目標句，讓 push-to-talk 這條
        # 批次路徑也跟著課程進度走，而不是只反應學生剛剛講的那句話。
        self._lesson_topic: str | None = None
        self._lesson_target: str | None = None
        # 累計「成功回合」數，用於每 N 輪觸發背景導師更新；同時也是 scaffold
        # 鼓勵語輪替的 turn_index，避免同句話卡在同一句鼓勵語。
        self._turn_count: int = 0
        # 連續「沒命中」輪次計數（scaffold.ScaffoldResult.matched=False）；
        # 達門檻時把 _fallback_prompt 當 stuck_hint 傳給 scaffold.respond()，
        # 讓學生連續卡關時自動降階成簡化提示，而不是一直重複同一句完整目標句。
        # 命中一次就歸零；見 _process_text 尾端的更新邏輯。
        self._stuck_streak: int = 0
        # 已經開口邀請、還在等孩子回答的那個遊戲（None = 沒有待回覆的邀請）。
        # 刻意是**實例層**而非 `_active_game` 那樣的裝置級單例：邀請與回答一定
        # 發生在同一條連線的相鄰兩輪（裝置端 local_client 整場只開一條 ws，
        # 瀏覽器重整則等於換一場對話，邀請跟著失效才是對的）。
        # 一場對話的識別碼（EPISODIC 記憶的「情節」邊界）。一條連線 = 一場對話：
        # 裝置端 local_client 整場只開一條 ws，瀏覽器重整就是換一場。
        self._session_id: str = uuid.uuid4().hex[:16]
        self._pending_invite: str | None = None
        # 這一段卡關期間是否已經邀請過（命中一次詞庫就歸零，見 _process_text）。
        # 用它擋重複邀請，而不是把 _stuck_streak 歸零——後者是降階提示的依據。
        self._invite_offered: bool = False
        # 今日 companion_directive 的簡化提示（見 diagnose.py 的 fallback_prompt），
        # 跟 _directive 同一批背景刷新；None 表示還沒有可用的診斷資料。
        self._fallback_prompt: str | None = None
        # 背景刷新防重入旗標
        self._directive_refreshing: bool = False
        # D-03(b) 背景機會式同步防重入旗標
        self._sync_pushing: bool = False
        # 這裡**刻意不初始化 self.game**：它是模組級的 `_active_game`（見上方註解），
        # 在此寫 None 會讓每條新連線都清掉老師剛開的那一局。

    # ---------- 小遊戲 ----------

    @property
    def game(self):
        """進行中的那一局（裝置級單例，見模組頂端 ``_active_game``）。"""
        return _active_game

    @game.setter
    def game(self, state) -> None:
        global _active_game
        _active_game = state

    def start_game(self, kind: str, **kwargs):
        """開一局遊戲，回傳開場白。未知種類拋 ValueError。

        ``student_id`` 沒指定時用這條 pipeline 綁的學生——遊戲要拿它讀
        間隔重複的到期詞，漏傳的話所有孩子會拿到同一批提示。
        """
        from server import games

        kwargs.setdefault("student_id", self.student_id or config.STUDENT_ID)
        self.game = games.start(kind, **kwargs)
        return games.prompt(self.game)

    def end_game(self) -> None:
        self.game = None

    def play_turn(self, student_text):
        """把一句話交給進行中的遊戲判定；沒有進行中的遊戲回 None。

        **刻意不碰雲端。** 判定必須是確定性的（同一句話同一個結果），而且
        斷網橋段要跟連網時一模一樣——雲端 LLM 會讓兩者不同，那正是現場
        最不能發生的事。雲端的價值放在遊戲之外的自由對話。
        """
        from server import games

        if self.game is None:
            return None
        turn = games.judge(self.game, student_text)
        # 一局結束就把狀態清掉，否則下一句話會撞到「這關已完成」
        self.game = None if turn.state.done else turn.state
        return turn

    async def _speak_rule_reply(self, result: TurnResult, emit, t0: float,
                                text: str) -> TurnResult:
        """把一句**純規則**的回覆講出去並收掉這一輪。

        ``llm`` 記 0：這條路徑沒有推論，斷網與連網一模一樣。刻意不寫 DB——
        「我要玩火眼金睛」是指令不是學習內容，混進互動紀錄會稀釋老師看到的
        發音分數與診斷樣本。
        """
        result.reply_text = text
        result.latency_ms["llm"] = 0
        await self._synth_tts(result, emit, scaffold.split_tts_segments(text))
        result.latency_ms["round_total"] = int((time.monotonic() - t0) * 1000)
        await self._emit_state(emit, result, "idle")
        return result

    async def _handle_game_intent(self, result: TurnResult, emit,
                                  t0: float) -> TurnResult | None:
        """這句話是不是在開局／結束／回答邀請；不是的話回 None 讓流程繼續。"""
        from server import game_intent

        text = result.asr_text

        # 遊戲進行中只認「結束」——其餘交給 games.judge，否則遊戲裡講到
        # 「我要玩」會把同一局重開。
        if self.game is not None:
            if game_intent.detect_stop(text):
                self.end_game()
                self._pending_invite = None
                return await self._speak_rule_reply(
                    result, emit, t0, "好呀，我們不玩了。你想聊什麼都可以喔！"
                )
            return None

        # 對主動邀請的回應（B）：答應就開局；拒絕或聽不出來都清掉，不糾纏。
        if self._pending_invite is not None:
            kind, self._pending_invite = self._pending_invite, None
            answer = game_intent.detect_yes_no(text)
            if answer is True:
                return await self._speak_rule_reply(
                    result, emit, t0, self._start_game_line(kind, "太好了！")
                )
            if answer is False:
                return await self._speak_rule_reply(
                    result, emit, t0, "好，那我們繼續聊天！"
                )
            # 聽不出來 → 當作沒回應，這句話照常走一般流程

        kind = game_intent.detect_start(text)
        if kind is None:
            return None
        return await self._speak_rule_reply(
            result, emit, t0, self._start_game_line(kind, "好呀！")
        )

    def _maybe_invite_game(self) -> str | None:
        """連續卡關時開口邀請玩遊戲；不該邀請就回 None。

        固定推**火眼金睛**：它門檻最低（看到什麼就說什麼），是卡關情境下唯一
        合理的選擇——另外兩個遊戲要問句或要照流程點餐，對正在挫折的孩子更難。
        想玩那兩個可以直接用講的叫出來（見 `game_intent.detect_start`）。

        擋重複用獨立的 `_invite_offered`，**刻意不動 `_stuck_streak`**：那個計數
        器是既有降階提示（`stuck_hint`）的依據，把它歸零會讓孩子拿不到簡化提示
        ——用邀請功能吃掉既有的教學行為，代價遠大於收益。
        """
        from server import games

        if self.game is not None or self._pending_invite is not None:
            return None
        if self._stuck_streak < STUCK_STREAK_THRESHOLD:
            return None
        if self._invite_offered:
            return None
        self._pending_invite = games.GAMES[0]["kind"]
        self._invite_offered = True
        return f"要不要玩{games.GAMES[0]['zh']}？"

    def _start_game_line(self, kind: str, prefix: str) -> str:
        """開一局並組出開場白。孩子看不到螢幕，規則說明只能用聽的。

        `ANY_GAME`（孩子說「我要玩小遊戲」但沒指定）→ 開清單第一個，並在開場白
        **報出另外兩個的名字**。刻意不反問「你想玩哪一個」：反問等於再賭一次
        ASR，而真機實測遊戲名正是最容易聽錯的部分（「火眼金睛」→「佛火眼鏡」）。
        直接開最低門檻的那個，把選項用講的告訴他——沒有螢幕就沒有選單，
        不講出來等於不存在。
        """
        from server import game_intent, games

        suffix = ""
        if kind == game_intent.ANY_GAME:
            kind = games.GAMES[0]["kind"]
            others = "、".join(g["zh"] for g in games.GAMES[1:])
            if others:
                suffix = f"想玩別的就跟我說「{others}」喔！"
        line = self.start_game(kind)
        return " ".join(p for p in (prefix, line.zh, line.en, suffix) if p)

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

    def _persist_turn(self, result: TurnResult) -> None:
        """把一輪對話寫進 DB。遊戲回合與一般回合共用，欄位不會漂開。

        寫入失敗不阻斷回覆（demo 韌性優先）——孩子已經聽到回應了，
        因為 DB 壞掉就讓整輪失敗沒有意義。
        """
        try:
            result.seq = store.add_interaction(
                {
                    "device_id": config.DEVICE_ID,
                    "student_id": self.student_id or config.STUDENT_ID,
                    "ts": _now_iso_taipei(),
                    "network_mode": self.network_mode,
                    "student_text": result.asr_text,
                    "asr_confidence": round(result.asr_conf, 4),
                    "ai_response_text": result.reply_text,
                    "scores": result.scores,
                    "latency_ms": dict(result.latency_ms),
                    "synced": self.network_mode == "cloud",
                    # --- 長期記憶的原料（本地限定，白名單投影不會上雲）---
                    # network_mode 說的是「這輪打算試雲端嗎」，reply_source 說的是
                    # 「誰真的生出這句話」。雲端逾時降級 edge 時兩者不一致，
                    # 而那正是最該記下來的一輪。
                    "reply_source": result.reply_source,
                    "matched": result.matched,
                    "stuck_streak": self._stuck_streak,
                    "session_id": self._session_id,
                    "game": (
                        {"kind": result.game_kind, "turns": result.game_turns}
                        if result.game_kind
                        else None
                    ),
                    "lesson": (
                        {"topic": self._lesson_topic,
                         "target_sentence": self._lesson_target}
                        if self._lesson_topic or self._lesson_target
                        else None
                    ),
                }
            )
        except Exception:
            _log.warning("互動寫入失敗，本輪不記錄", exc_info=True)
            result.seq = 0

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

        # 用講的開局／結束。**裝置沒有螢幕**，這是現場唯一叫得出遊戲的方式
        # （`edge/runtime/local_client.py` 走的就是這條 /ws/talk）。
        # 放在遊戲判定之前：遊戲進行中喊「不玩了」也要出得來。
        handled = await self._handle_game_intent(result, emit, t0)
        if handled is not None:
            return handled

        # 遊戲進行中：判定由 games.py 接手，**完全不走雲端**。
        # 這條路徑刻意短——判定是純函式，斷網與連網一模一樣，
        # 而且不吃 1.5 秒的雲端預算。
        if self.game is not None:
            turn = self.play_turn(result.asr_text)
            if turn is not None:
                pieces = [turn.reply_zh]
                if turn.target_en:
                    pieces.append(f"跟我說一遍：{turn.target_en}")
                elif turn.reply_en:
                    pieces.append(turn.reply_en)
                result.reply_text = " ".join(p for p in pieces if p)
                result.scores = scaffold.compute_scores(result.asr_text)
                result.latency_ms["llm"] = 0
                result.reply_source = "game"
                result.game_kind = turn.state.game
                result.game_turns = turn.state.turns
                segments = scaffold.split_tts_segments(result.reply_text)
                await self._synth_tts(result, emit, segments)
                result.latency_ms["round_total"] = int((time.monotonic() - t0) * 1000)
                self._persist_turn(result)
                await self._emit_state(emit, result, "idle")
                return result

        self._ensure_lesson()
        # 連續卡關達門檻才把簡化提示傳下去；平常（streak 未達門檻）維持現行行為。
        stuck_hint = (
            self._fallback_prompt
            if self._stuck_streak >= STUCK_STREAK_THRESHOLD
            else None
        )
        sc = scaffold.respond(
            result.asr_text,
            turn_index=self._turn_count,
            lesson_topic=self._lesson_topic,
            lesson_target_sentence=self._lesson_target,
            stuck_hint=stuck_hint,
        )
        # 命中就歸零、沒命中就累加；LLM 加值（下面）會沿用 sc.target_sentence，
        # 所以就算換成 LLM 生成的文字，簡化後的目標句一樣會被帶到（見 llm.py
        # generate() 的「目標英文句一定出現在回覆中」護欄）。
        self._stuck_streak = 0 if sc.matched else self._stuck_streak + 1
        if sc.matched:
            # 孩子重新跟上了 → 下一次卡關可以再邀請一次
            self._invite_offered = False
        result.reply_text = sc.reply_text
        result.scores = dict(sc.scores)
        result.matched = bool(sc.matched)
        result.reply_source = "scaffold"  # LLM 接手成功時於下方覆寫
        segments = list(sc.tts_segments)

        # LLM 加值：cloud → edge → scaffold 降級鏈；任一層逾時/例外/None 續試下一層。
        # 雲端只在 network_mode=="cloud" 且取得家長同意時進入（資料出境 chokepoint）。
        t_llm = time.monotonic()
        llm_text: str | None = None
        engines = []
        if (
            self.network_mode == "cloud"
            and self.cloud_llm is not None
            and self.cloud_llm.available()
            and guardrails.consent_granted()
        ):
            engines.append(self.cloud_llm)
        if self.llm is not None and self.llm.available():
            engines.append(self.llm)
        for engine in engines:
            try:
                candidate = await asyncio.wait_for(
                    asyncio.to_thread(
                        engine.generate, result.asr_text, sc, self._directive
                    ),
                    timeout=LLM_TIMEOUT_S,
                )
            except Exception:
                candidate = None
            if candidate and isinstance(candidate, str) and candidate.strip():
                llm_text = candidate
                # 記下真正生出這句話的引擎（不是「打算用哪個」）
                result.reply_source = (
                    "cloud" if engine is self.cloud_llm else "edge"
                )
                break
        result.latency_ms["llm"] = int((time.monotonic() - t_llm) * 1000)
        if llm_text:
            result.reply_text = llm_text.strip()
            segments = scaffold.split_tts_segments(result.reply_text)

        # 連續卡關 → 主動邀請玩遊戲（接在回覆後面，不取代它）。
        # 必須在 _synth_tts 之前併進 reply_text：孩子只聽得到聲音，
        # 沒進 TTS 的邀請等於沒發生。
        invite = self._maybe_invite_game()
        if invite:
            result.reply_text = f"{result.reply_text} {invite}".strip()
            segments = scaffold.split_tts_segments(result.reply_text)

        # 階段：TTS
        await self._synth_tts(result, emit, segments)

        # 寫 DB（低信心兜底已在前面 return，不會到這裡）
        result.latency_ms["round_total"] = int((time.monotonic() - t0) * 1000)
        self._persist_turn(result)

        # B1：每 N 個成功回合，背景（不 await）觸發導師更新 companion_directive
        self._turn_count += 1
        if DIRECTIVE_REFRESH_EVERY > 0 and self._turn_count % DIRECTIVE_REFRESH_EVERY == 0:
            asyncio.create_task(self._refresh_directive())

        # D-03(b) 回合尾兜底同步：cloud 模式下當輪紀錄在上面 add_interaction 當下
        # 已標記 synced（見 "synced": self.network_mode == "cloud" 那一行），所以
        # 這裡撈到的 pending 必定來自先前的離線視窗；用途是接住 D-03(a) 漏接的
        # 情形（server 重啟、手動改 flag），避免 pending 永久卡在佇列。
        if self.network_mode == "cloud" and store.pending_count() > 0:
            asyncio.create_task(self._opportunistic_sync())

        await self._emit_state(emit, result, "idle")
        return result

    def _ensure_lesson(self) -> None:
        """首次通話前確保有今日課程可用（主題／目標句）；安全退化，永不擋對話。

        只在尚未取得課程時才讀 DB（同步、輕量的本機 SQLite 讀取，與既有
        ``store.add_interaction`` 同步呼叫風格一致）；之後的更新交給
        ``_refresh_directive`` 背景刷新，避免每輪都重算。
        """
        if self._lesson_target is not None:
            return
        try:
            diagnoses = store.list_diagnoses()
            lp = lesson.build_lesson(diagnoses, store.get_profile())
            self._lesson_topic = lp.topic
            self._lesson_target = lp.target_sentence
            self._fallback_prompt = _extract_fallback_prompt(diagnoses)
        except Exception:
            pass

    async def _refresh_directive(self) -> None:
        """背景更新 directive + 今日課程：讀 DB→產診斷→存 DB→更新記憶體快取。

        全程在 asyncio.to_thread 執行，導師絕不進即時路徑；失敗維持舊快取。
        """
        if self._directive_refreshing:
            return
        self._directive_refreshing = True
        try:
            from server import diagnose

            # 09-RESEARCH.md Pitfall 4 的側通道閘門：在進入背景任務當下就把
            # network_mode 取出成區域變數，讓閉包捕捉的是確定值而非執行緒
            # 執行時才讀屬性；edge 模式下不得觸發雲端出境（consent 之外的
            # 第三道閘門），但本地規則式刷新仍要跑，不整段跳過。
            allow_cloud = self.network_mode == "cloud"

            def _work():
                recent = store.list_interactions(limit=10)
                diagnoses = store.list_diagnoses()
                prev = diagnoses[-1] if diagnoses else None
                # 間隔重複：把這幾回合折算成複習排程（純本地、不出境）。
                # 放在診斷之前，派作業 agent 這一輪就讀得到最新的到期詞。
                # 每個詞記著自己算過的 seq，重跑不會重複計分。
                try:
                    from server import srs
                    srs.record_interactions(recent, self.student_id or config.STUDENT_ID)
                except Exception:
                    _log.warning("複習排程更新失敗，本輪照原邏輯出題", exc_info=True)
                diag = diagnose.generate_diagnosis(recent, prev, allow_cloud=allow_cloud)
                store.add_diagnosis(diag)  # 持久化（含 companion_directive）
                # 子專案 B/C/E：診斷產出後才做編排決策——它要看的就是這份新診斷。
                # 整段包在自己的 try 內，agent 出事不得影響 directive 更新
                # （directive 停更 = 導師層在現場悄悄死掉）。
                # diagnoses 是在 diag 產生「之前」讀的，必須補上這份新的，
                # 否則週報永遠落後一個循環——demo 主軸是「孩子練完、家長端
                # 立刻看到」，少這一筆首次刷新還會印「尚無任何練習紀錄」。
                self._run_agents(diag, diagnoses + [diag], allow_cloud=allow_cloud)
                # B3 接法 A：帶 level_state，CEFR 難度/語言形式折進注入字串
                directive = diagnose.format_directive_for_prompt(
                    diag.get("companion_directive"), diag.get("level_state"))
                # 診斷更新後，今日課程（主題/目標句）也可能跟著換一階/換主題
                lp = lesson.build_lesson(store.list_diagnoses(), store.get_profile())
                fallback_prompt = _extract_fallback_prompt([diag])
                return directive, lp.topic, lp.target_sentence, fallback_prompt

            (
                self._directive,
                self._lesson_topic,
                self._lesson_target,
                self._fallback_prompt,
            ) = await asyncio.to_thread(_work)
        except Exception:
            # 更新失敗維持舊快取（directive/lesson 皆不變），即時路徑不受影響
            # ——但必須留下日誌。無聲的 `except: pass` 正是本專案吃過虧的形狀：
            # 導師層在現場悄悄停更，畫面照跑，沒有任何人會發現。
            _log.exception("背景 directive 刷新失敗，沿用前一版 directive")
        finally:
            self._directive_refreshing = False

    async def _opportunistic_sync(self) -> None:
        """D-03(b) 回合尾兜底：cloud 模式下補傳先前離線期間累積的 pending。

        比照 _refresh_directive 的骨架：再入旗標 + 進入背景任務當下就把
        network_mode 取成區域變數（09-RESEARCH.md Pitfall 4 的側通道閘門，
        讓閉包捕捉的是確定值而非執行緒真正跑到時才讀屬性，否則 kill-switch
        會有競態）+ asyncio.to_thread（SQLite 寫入不得卡在事件迴圈上）+
        _log.exception（絕不無聲吞例外）+ finally 復位。不接受也不傳遞
        transport 參數，一律走 opportunistic_sync() 的本機路徑（同程序拓樸
        下沒有跨程序邊界要跨，見 sync_client.opportunistic_sync 的 docstring）。
        """
        if self._sync_pushing:
            return
        self._sync_pushing = True
        try:
            allow_cloud = self.network_mode == "cloud"
            if not allow_cloud:
                return
            from server import sync_client

            await asyncio.to_thread(sync_client.opportunistic_sync)
        except Exception:
            _log.exception("背景機會式同步失敗，pending 留待下次補傳")
        finally:
            self._sync_pushing = False

    def _run_agents(self, diag: dict, diagnoses: list[dict], *, allow_cloud: bool) -> None:
        """依編排決策執行派作業／週報 agent 並持久化產出。

        在 _refresh_directive 的背景執行緒內呼叫，絕不進即時路徑。

        三個原則：
        1. **編排只做決策，執行在這裡。** orchestrator.decide_next_actions 不呼叫
           B/C，由本函式依 actions 決定要不要真的跑——kill-switch 與失敗降級
           因此集中在 pipeline 一處，而不是散在三個 agent 裡。
        2. **allow_cloud 一路傳下去。** 漏傳任何一個，斷網示範時就會有元件
           偷偷出境，那正是 NETCUT 要防的事。
        3. **每個 agent 各自 try。** 一個爆掉不得拖垮另一個，更不得讓
           _refresh_directive 整個失敗（directive 停更 = 導師層悄悄死掉）。
        """
        from server.agents import homework, orchestrator, report

        sid = self.student_id or config.STUDENT_ID
        try:
            profile = store.get_profile(sid) or {}
        except Exception:
            profile = {}
        # 學生第一次上線／DB 剛重置時 profile 是空的，而 agent 是從 profile
        # 取 student_id 當 AgentCore Memory 的分群鍵。少了它會讓所有孩子
        # 共用同一份長期記憶——這是隱私事故，不是功能瑕疵。sid 在這裡是
        # 權威值，直接補進去，不要讓下游猜。
        profile.setdefault("student_id", sid)

        try:
            decision = orchestrator.decide_next_actions(
                profile, diag, diagnoses, self._turn_count, allow_cloud=allow_cloud,
            )
            actions = list(decision.get("actions") or [])
        except Exception:
            # 編排失敗：寧可什麼都不派，也不要亂派
            _log.exception("編排決策失敗，本輪不派發任何 agent 產出")
            return

        if "homework" in actions:
            try:
                store.add_agent_output(
                    "homework",
                    homework.generate_homework(profile, diag, allow_cloud=allow_cloud),
                    student_id=sid,
                )
            except Exception:
                _log.exception("派作業 agent 失敗，略過本輪作業")

        if "report" in actions:
            try:
                store.add_agent_output(
                    "report",
                    report.generate_report(profile, diagnoses, allow_cloud=allow_cloud),
                    student_id=sid,
                )
            except Exception:
                _log.exception("週報 agent 失敗，略過本輪週報")

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
                    and guardrails.consent_granted()
                ):
                    wav = await asyncio.to_thread(self.cloud_tts.synth, segments)
                if wav is None and self.tts is not None and self.tts.available():
                    wav = await asyncio.to_thread(self.tts.synth, segments)
        except Exception:
            wav = None
        result.latency_ms["tts_first"] = int((time.monotonic() - t_tts) * 1000)
        result.tts_wav = wav
