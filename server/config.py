"""全域設定（依 CONTRACTS.md 逐字定義，路徑皆以 talkybuddy 根目錄為基準）。"""

from pathlib import Path

# talkybuddy 根目錄（server/ 的上一層）
BASE_DIR: Path = Path(__file__).resolve().parent.parent

MODELS_DIR: Path = BASE_DIR / "models"
DATA_DIR: Path = BASE_DIR / "data"
DB_PATH: Path = DATA_DIR / "talkybuddy.db"

# 模型檔路徑
LLM_GGUF: Path = MODELS_DIR / "qwen2.5-1.5b-instruct-q4_k_m.gguf"
PIPER_ZH: Path = MODELS_DIR / "zh_CN-huayan-medium.onnx"
PIPER_EN: Path = MODELS_DIR / "en_US-lessac-medium.onnx"

# faster-whisper 模型規格
ASR_MODEL = "small"

# 裝置與學生識別（決賽 demo 固定值）
DEVICE_ID = "GENIO-520-X992"
STUDENT_ID = "STUDENT-AMING-004"

# ASR 信心門檻：低於此值走兜底話術
ASR_CONF_THRESHOLD = 0.45
