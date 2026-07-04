"""全域設定（依 CONTRACTS.md 逐字定義，路徑皆以 talkybuddy 根目錄為基準）。"""

from pathlib import Path

# talkybuddy 根目錄（server/ 的上一層）
BASE_DIR: Path = Path(__file__).resolve().parent.parent

MODELS_DIR: Path = BASE_DIR / "models"
DATA_DIR: Path = BASE_DIR / "data"
DB_PATH: Path = DATA_DIR / "talkybuddy.db"

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

# ASR 信心門檻：低於此值走兜底話術（PLAN.md §3：雜音干擾判定門檻 0.5）
ASR_CONF_THRESHOLD = 0.5

# ASR 後端選擇：feature flag，可切換 sherpa-onnx SenseVoice 或 faster-whisper fallback
ASR_BACKEND = "whisper"  # "sensevoice" | "whisper"（Task 3 會改為預設 sensevoice）
# SenseVoice int8 模型解壓目錄（sherpa-onnx 官方 asr-models release）
SENSEVOICE_DIR: Path = MODELS_DIR / "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
# OpenCC 簡轉繁設定檔（簡體→繁體＋台灣慣用詞）
OPENCC_CONFIG = "s2twp"
