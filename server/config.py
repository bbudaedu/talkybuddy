"""全域設定（依 CONTRACTS.md 逐字定義，路徑皆以 talkybuddy 根目錄為基準）。"""

import os
from pathlib import Path

# talkybuddy 根目錄（server/ 的上一層）
BASE_DIR: Path = Path(__file__).resolve().parent.parent

MODELS_DIR: Path = BASE_DIR / "models"
DATA_DIR: Path = BASE_DIR / "data"
# DB_PATH 可用 TALKYBUDDY_DB_PATH 覆寫。
#
# 需要它的原因很具體：pipecat 那條路跑在 /root/pipecat-lab/（獨立目錄、獨立
# server/ 複本），於是它預設會寫進**自己的** data/talkybuddy.db，而教師儀表板
# 讀的是決賽路徑那一份。2026-08-01 發現：用 pipecat 對話之後，鏡頭 4 的儀表板
# 上看不到剛才那段互動——兩個不同的資料庫。
#
# 覆寫這個變數就能讓兩條路徑共用同一份記憶與同一份儀表板資料。
DB_PATH: Path = Path(os.environ.get("TALKYBUDDY_DB_PATH") or (DATA_DIR / "talkybuddy.db"))

# 模型檔路徑
LLM_GGUF: Path = MODELS_DIR / "qwen2.5-1.5b-instruct-q4_k_m.gguf"
# 註：PIPER_ZH/PIPER_EN 為 Piper=VITS 架構的 onnx 聲音檔，改由 sherpa-onnx（Apache-2.0）
# 載入合成，非 piper-tts（GPL-3.0）套件；沿用舊常數名以維持路徑相容。
PIPER_ZH: Path = MODELS_DIR / "zh_CN-huayan-medium.onnx"
PIPER_EN: Path = MODELS_DIR / "en_US-lessac-medium.onnx"

# sherpa-onnx 專用：patched onnx（補上 model_type/sample_rate 等 metadata）與
# tokens.txt 的快取目錄；首次載入時自動產生，之後重用（來源檔未變就不重算）。
SHERPA_CACHE_DIR: Path = MODELS_DIR / "_sherpa_cache"

# espeak-ng-data（音素化用資料檔）：優先找專案內建路徑，找不到再 fallback 到
# piper 套件內附的同名目錄。授權為 espeak-ng 上游 GPL-3.0，屬已知待確認風險
# （見 tts.py 開頭註解），非本次改動範圍。
ESPEAK_NG_DATA_DIR: Path = MODELS_DIR / "espeak-ng-data"

# faster-whisper 模型規格
ASR_MODEL = "small"

# 裝置與學生識別（決賽 demo 固定值）
DEVICE_ID = "GENIO-520-X992"
STUDENT_ID = "STUDENT-AMING-004"
# 教師儀表板顯示用的學生完整姓名。刻意不經 guardrails.deidentify()——身分欄位
# （student_id、姓名）與對話內容適用不同規則（見 11-CONTEXT.md D-05）：
# deidentify() 的目的是遮掉逐字稿裡偶發提到的他人個資，不是移除受評學生
# 本人的身分。此值不進上傳白名單（server/sync_client.py UPLOAD_FIELDS）——
# 姓名只存在 server 端，裝置端 push_pending() 只送 student_id 供綁定。
STUDENT_NAME = "阿明"

# ASR 信心門檻：低於此值走兜底話術（PLAN.md §3：雜音干擾判定門檻 0.5）
ASR_CONF_THRESHOLD = 0.5

# ASR 後端選擇：feature flag，可切換 sherpa-onnx SenseVoice 或 faster-whisper fallback
ASR_BACKEND = "sensevoice"  # "sensevoice" | "whisper"；切回 whisper 僅需改此值
# SenseVoice int8 模型解壓目錄（sherpa-onnx 官方 asr-models release）
SENSEVOICE_DIR: Path = MODELS_DIR / "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
# OpenCC 簡轉繁設定檔（簡體→繁體＋台灣慣用詞）
OPENCC_CONFIG = "s2twp"

# ---------------------------------------------------------------------------
# A1 喚醒層（Porcupine Web）：AccessKey 由環境變數注入，不寫進 repo；經
# /api/wake-config 下發給瀏覽器。喚醒詞先用內建 BuiltInKeyword 跑通，之後換自訂 .ppn。
# ---------------------------------------------------------------------------
import os

PICOVOICE_ACCESS_KEY: str = os.environ.get("PICOVOICE_ACCESS_KEY", "")
# 內建喚醒詞名稱（BuiltInKeyword 之一，如 "Bumblebee" / "Porcupine" / "Grasshopper"）。
WAKE_KEYWORD_BUILTIN: str = os.environ.get("WAKE_KEYWORD_BUILTIN", "Bumblebee")
WAKE_KEYWORD_LABEL: str = os.environ.get("WAKE_KEYWORD_LABEL", "Bumblebee")
# 自訂 .ppn（Web/WASM target）相對 URL；非空則優先於內建，空字串=用內建。
WAKE_KEYWORD_PUBLIC_PATH: str = os.environ.get("WAKE_KEYWORD_PUBLIC_PATH", "")
# Porcupine 參數模型 .pv（英文）相對 URL（由 /static 掛載點提供，對應 web/assets/ 目錄）。
WAKE_MODEL_PUBLIC_PATH: str = os.environ.get("WAKE_MODEL_PUBLIC_PATH", "/static/assets/porcupine_params.pv")
# 喚醒靈敏度 0~1（越高越易觸發、也越易誤觸）。
WAKE_SENSITIVITY: float = float(os.environ.get("WAKE_SENSITIVITY", "0.6"))

# A1 喚醒層（sherpa-onnx KWS Web）：中文喚醒詞 Pinyin 標音 + 門檻設定
WAKE_SHERPA_ENABLED: bool = os.environ.get("WAKE_SHERPA_ENABLED", "1") not in ("", "0", "false", "False")
WAKE_SHERPA_BASE_URL: str = os.environ.get("WAKE_SHERPA_BASE_URL", "/static/vendor/sherpa-kws/")
WAKE_SHERPA_KEYWORDS: str = os.environ.get("WAKE_SHERPA_KEYWORDS", "sh uō sh uō x ué b àn @說說學伴")
WAKE_SHERPA_THRESHOLD: float = float(os.environ.get("WAKE_SHERPA_THRESHOLD", "0.25"))
WAKE_SHERPA_SCORE: float = float(os.environ.get("WAKE_SHERPA_SCORE", "1.0"))


# Bedrock 服務 region（Nova Sonic live S2S 沿用；陪聊/診斷雲端腦走 anthropic-relay，不需其餘 Converse 設定）。
BEDROCK_REGION: str = os.environ.get("BEDROCK_REGION", "us-east-1")

# ---------------------------------------------------------------------------
# B4-5 家長同意 gate（consent；見 research/b_axis/B4_隱私與Guardrails.md §2.3）
# demo 預設 True（正式版接學校/家長書面同意書，屬法遵層級，工程只留旗標）。
# 未同意 → 強制 edge-only：不切雲端、不送任何資料上雲。可由環境變數覆寫。
# ---------------------------------------------------------------------------
def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


CONSENT_GRANTED: bool = _env_bool("TALKYBUDDY_CONSENT_GRANTED", True)


# ---------------------------------------------------------------------------
# Nova Sonic 即時 S2S 陪聊（Phase 1；見 docs/superpowers/specs/2026-07-11-nova-sonic-live-s2s-design.md）
# bidi 只吃 SigV4：憑證走 env（AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY），不進 repo。
# region 沿用 BEDROCK_REGION。實際可用性 = LIVE_S2S_ENABLED AND nova_sonic.available()。
# （放在 _env_bool 定義之後，避免 NameError。）
# ---------------------------------------------------------------------------
NOVA_SONIC_MODEL_ID: str = os.environ.get("NOVA_SONIC_MODEL_ID", "amazon.nova-2-sonic-v1:0")
NOVA_SONIC_VOICE: str = os.environ.get("NOVA_SONIC_VOICE", "tiffany")
LIVE_S2S_ENABLED: bool = _env_bool("LIVE_S2S_ENABLED", True)


# ---------------------------------------------------------------------------
# 雲端情緒 TTS（ElevenLabs；見 docs/superpowers/specs/2026-07-08-cloud-emotional-tts-design.md）
# 全走環境變數、絕不 commit。network_mode=="cloud" 時 pipeline 才會嘗試雲端，
# 失敗/逾時/斷網 → 靜默降級回邊緣 Piper。
# ---------------------------------------------------------------------------
ELEVENLABS_API_KEY: str = os.environ.get("ELEVENLABS_API_KEY", "")
# 合成用 voice_id。預設 Alice（Xb7hH8MSUJpSbSDYk0k2）：英語母語 voice 讀中文帶外國腔，
# 正好契合「外國企鵝來跟小朋友學中文」的角色設定（免費 tier，繞過付費台灣腔）。
# 克隆/換聲僅覆蓋此 env，架構不動。空=不啟用雲端。
ELEVENLABS_VOICE_ID: str = os.environ.get("ELEVENLABS_VOICE_ID", "Xb7hH8MSUJpSbSDYk0k2")
# 合成模型。2026-07-30 從 eleven_v3 換成 eleven_turbo_v2_5：真機 A/B 試聽後
# 確認 turbo 相對邊緣 sherpa-onnx 已有明顯提升，不值得為 v3 多付延遲。實測
# （暖機後整包收完的中位數）：
#     eleven_v3          2.97s   ← 冷啟動 7.92s
#     eleven_turbo_v2_5  0.37s
#     eleven_flash_v2_5  0.48s   ← 最快但單次抖動大（0.34–0.72s）
# 換模型會改變放慢語速的實作路徑（v3 忽略 speed、v2_5 不忽略），
# 分流見 server/cloud_tts.py::_model_honours_speed。
ELEVENLABS_MODEL: str = os.environ.get("ELEVENLABS_MODEL", "eleven_turbo_v2_5")
# 雲端合成逾時（秒）；逾時即靜默降級回邊緣 TTS。
# 這個值有硬上界：斷網時的降級延遲必須 < 1–2 秒（ROADMAP／NETCUT-02、D-03），
# 由 tests/test_pipeline_timeout_isolation.py 釘住 <= 2.0。逾時等待是白花的
# 牆鐘時間——之後還要再跑一次邊緣合成，使用者聽到的是「先沉默、再回答」。
#
# 1.5s 對 eleven_turbo_v2_5（實測中位數 0.37s）有 4 倍餘裕，夠用。
# 2026-07-30 一度想放寬到 3.0s，那是為了遷就 eleven_v3 的約 3s 延遲——換成
# turbo 之後這個理由消失了。**逾時不該為了配合慢模型而放寬**，那是把降級
# 體驗賠掉去救一個本來就不該選的模型。
# 若實際耗時經常逼近這個值，問題是網路不是模型。
CLOUD_TTS_TIMEOUT_S: float = float(os.environ.get("CLOUD_TTS_TIMEOUT_S", "1.5"))
# 發音評測（B 軸背景，見 server/pronunciation.py）逾時（秒）；含首輪模型載入。
# 逾時→該輪 pron=None 照寫 transcript，避免分數與 interaction 脫鉤。
PRON_SCORE_TIMEOUT_S: float = float(os.environ.get("PRON_SCORE_TIMEOUT_S", "15.0"))
# voice_settings 情緒參數（預設值＝真 API 手測聽感選定，可用 env 調）：
# stability 低→情緒起伏大、高→平穩；style 誇張說話特色；similarity_boost 貼近原聲。
ELEVENLABS_STABILITY: float = float(os.environ.get("ELEVENLABS_STABILITY", "0.5"))
ELEVENLABS_SIMILARITY_BOOST: float = float(
    os.environ.get("ELEVENLABS_SIMILARITY_BOOST", "0.8")
)
ELEVENLABS_STYLE: float = float(os.environ.get("ELEVENLABS_STYLE", "0.2"))
ELEVENLABS_USE_SPEAKER_BOOST: bool = os.environ.get(
    "ELEVENLABS_USE_SPEAKER_BOOST", "true"
).strip().lower() in ("1", "true", "yes", "on")
# 放慢雲端語音的播放速度。<1 放慢、1.0 完全不處理。
# 預設 0.90＝比原聲再慢一點點，讓國小雙語帶讀更清楚；env 可微調（0.85 更慢等）。
# 實作路徑依模型而異（見 server/cloud_tts.py::_model_honours_speed）：
#   吃 speed 的（turbo/flash v2_5、multilingual_v2）→ 交給 API，零額外運算
#   不吃的（eleven_v3）→ 合成後對 raw PCM 做保持音高的時間伸縮
#                        （WSOLA，見 server/timestretch.py）
# 兩條路只能擇一，都做會變成放慢兩次、慢到不能聽且不報錯。
CLOUD_TTS_SPEED: float = float(os.environ.get("CLOUD_TTS_SPEED", "0.90"))

# ---------------------------------------------------------------------------
# 佈署 profile：edge（玩偶本地）/ cloud（瀏覽器終端，AI 在雲端）
# ---------------------------------------------------------------------------
PIPELINE_PROFILE: str = os.environ.get("TALKYBUDDY_PIPELINE_PROFILE", "edge")

# LLM_N_CTX：llama.cpp context 視窗大小，依 PIPELINE_PROFILE 決定預設值。
# edge=512 避免記憶體吃緊（Genio 520 僅 4GB，Phase 8 實測峰值 RSS 已達 2,723MB）、
# cloud/PC=1024 記憶體充足維持較大視窗；
# 註：本行原註記「無 GPU」是錯的——這顆 Genio 520 有 Mali-G57 2 cores
#（/dev/mali0、vulkaninfo、clinfo 三方確認）。但板上 llama.cpp binary 未編進 GPU 後端，
# 目前仍是純 CPU 推論，故 512 的選擇不變。
# TALKYBUDDY_LLM_N_CTX 可強制覆寫，優先於 profile 預設。
_LLM_N_CTX_DEFAULT = 512 if PIPELINE_PROFILE == "edge" else 1024
LLM_N_CTX: int = int(os.environ.get("TALKYBUDDY_LLM_N_CTX", str(_LLM_N_CTX_DEFAULT)))

# llama-server 連線設定：llama-server 為獨立 OS 行程（非 in-process Llama 物件），
# 這些值供 edge/runtime/run_llama_server.py 組出啟動 argv（--host/--port/--threads）。
# host 預設 loopback 127.0.0.1（不可對外可路由，見 threat_model T-08-01）。
# LLM_THREADS = 6：ELOOP-03 真機 llama-bench 掃描的最佳值（`-t 1,2,4,6,8 -r 3`，
# 見 edge/EDGE_TURN_LOOP_VALIDATION.md §A）——4 → pp 26.49／tg 10.50，
# 6 → pp 39.06／tg 12.35，8 反而略降（超過大核心配置）。
#
# **這裡曾經是佔位的 4。** 量測早就做完並選定 6，但只用 `TALKYBUDDY_LLM_THREADS=6`
# env override 套在裝置上，預設值沒改回來；env override 活在環境裡，重開機或啟動
# 腳本沒帶就悄悄退回 4（run_edge.sh 確實沒設定它）。2026-07-29 實測裝置正是跑
# --threads 4，prefill 因此慢 1.47×。已量過的結論要釘在程式碼裡，別只寫在文件。
LLM_SERVER_HOST: str = os.environ.get("TALKYBUDDY_LLM_SERVER_HOST", "127.0.0.1")
LLM_SERVER_PORT: int = int(os.environ.get("TALKYBUDDY_LLM_SERVER_PORT", "8080"))
LLM_THREADS: int = int(os.environ.get("TALKYBUDDY_LLM_THREADS", "6"))


def default_network_mode() -> str:
    """cloud profile 預設走雲端管線（ElevenLabs TTS 等）；否則邊緣。"""
    return "cloud" if PIPELINE_PROFILE == "cloud" else "edge"
