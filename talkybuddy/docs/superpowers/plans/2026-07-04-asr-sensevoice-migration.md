# ASR 換 sherpa-onnx + SenseVoice-Small 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `talkybuddy/` PC 原型的 ASR 從 faster-whisper 換成 sherpa-onnx + SenseVoice-Small（int8）+ OpenCC 簡轉繁，並保留 faster-whisper 為 feature flag 可切換的 fallback。

**Architecture:** 拆檔為 `asr_base.py`（工廠）+ `asr_whisper.py`（既有 faster-whisper 搬入）+ `asr_sensevoice.py`（新 sherpa-onnx 引擎）+ `asr.py`（薄 shim，維持 `from server.asr import ASREngine` 相容）。工廠依 `config.ASR_BACKEND` 選類別。`pipeline.py`/`app.py` 契約不變。

**Tech Stack:** sherpa-onnx 1.13.3（OfflineRecognizer.from_sense_voice）、SenseVoice-Small int8、OpenCC 1.4.0（s2twp）、soundfile、pytest。

## Global Constraints

- 契約不變：`ASREngine` 需可 `ASREngine()` 實例化，並具 `available() -> bool`、`transcribe(wav_path: str) -> tuple[str, float]`、`_ensure_model()`（`app.py:54` 預熱會呼叫）。
- 降級安全：所有引擎 import 期不可炸（lazy import + try/except）；任何 transcribe 失敗回 `("", 0.0)`，不 throw。
- 信心分數決策：SenseVoice 辨識為空字串 → `("", 0.0)`；非空 → `(繁體text, 1.0)`。
- 不自動 fallback：`ASR_BACKEND=sensevoice` 但模型不可用時 `available()=False`，pipeline 走兜底；不靜默切回 whisper。
- 不破壞任何現有可運行狀態；既有 41 個 pytest 需維持綠。
- 檔案路徑一律以 `/home/budaedu/hackathon/talkybuddy` 為基準。
- 指令一律用 `.venv/bin/python` / `.venv/bin/pytest`。

---

### Task 1: 重構為 backend 架構（保留 whisper、加工廠與 shim）

純重構，不改行為。把現有 `asr.py` 的 faster-whisper 實作搬到 `asr_whisper.py`，
新增工廠 `asr_base.py`、config flag、薄 shim `asr.py`。此任務 `ASR_BACKEND` 先設 `"whisper"`，
確保既有 41 測試維持綠。

**Files:**
- Create: `server/asr_whisper.py`
- Create: `server/asr_base.py`
- Modify: `server/asr.py`（改成 shim）
- Modify: `server/config.py`（加 `ASR_BACKEND` / `SENSEVOICE_DIR` / `OPENCC_CONFIG`）
- Test: `tests/test_asr_backend.py`

**Interfaces:**
- Consumes: `server.config.ASR_BACKEND`（str）。
- Produces:
  - `server.asr_whisper.WhisperASREngine`（class，含 `available()`/`transcribe()`/`_ensure_model()`）。
  - `server.asr_base.get_asr_engine_class(backend: str | None = None) -> type`。
  - `server.asr.ASREngine`（= 工廠選定的 class）。

- [ ] **Step 1: 加 config flag**

在 `server/config.py` 末尾（`ASR_CONF_THRESHOLD` 之後）加入：

```python
# ASR 後端選擇：feature flag，可切換 sherpa-onnx SenseVoice 或 faster-whisper fallback
ASR_BACKEND = "whisper"  # "sensevoice" | "whisper"（Task 3 會改為預設 sensevoice）
# SenseVoice int8 模型解壓目錄（sherpa-onnx 官方 asr-models release）
SENSEVOICE_DIR: Path = MODELS_DIR / "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
# OpenCC 簡轉繁設定檔（簡體→繁體＋台灣慣用詞）
OPENCC_CONFIG = "s2twp"
```

- [ ] **Step 2: 建立 asr_whisper.py（搬入現有 faster-whisper 實作）**

把現有 `server/asr.py` 的整個 class 內容複製到 `server/asr_whisper.py`，class 更名為
`WhisperASREngine`（其餘方法/邏輯逐字不變）：

```python
"""ASR 引擎（faster-whisper）—— 保留為 feature flag 可切換的 fallback。

契約：available() -> bool；transcribe(wav_path) -> tuple[str, float]；_ensure_model()。
規格與行為與原 asr.py 完全一致（small / int8 / language=None / beam_size=1 /
confidence=exp(mean avg_logprob) 夾 0-1 / 單例懶載入 / 降級安全）。
"""

from __future__ import annotations

import math
import threading


class WhisperASREngine:
    """faster-whisper 語音辨識引擎（單例懶載入、降級安全）。"""

    def __init__(self) -> None:
        self._model = None
        self._load_failed = False
        self._lock = threading.Lock()

    def available(self) -> bool:
        if self._model is not None:
            return True
        if self._load_failed:
            return False
        try:
            import faster_whisper  # noqa: F401
            return True
        except Exception:
            return False

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        if self._load_failed:
            return None
        with self._lock:
            if self._model is not None:
                return self._model
            if self._load_failed:
                return None
            try:
                from faster_whisper import WhisperModel
                try:
                    from server.config import ASR_MODEL
                except Exception:
                    ASR_MODEL = "small"
                self._model = WhisperModel(
                    ASR_MODEL, device="cpu", compute_type="int8"
                )
            except Exception:
                self._model = None
                self._load_failed = True
        return self._model

    def transcribe(self, wav_path: str) -> tuple[str, float]:
        model = self._ensure_model()
        if model is None:
            return ("", 0.0)
        try:
            segments, _info = model.transcribe(
                wav_path,
                language=None,
                beam_size=1,
            )
            texts: list[str] = []
            logprobs: list[float] = []
            for seg in segments:
                text = (seg.text or "").strip()
                if text:
                    texts.append(text)
                lp = getattr(seg, "avg_logprob", None)
                if lp is not None:
                    logprobs.append(float(lp))

            full_text = "".join(texts).strip() if texts else ""
            if not full_text:
                return ("", 0.0)

            if logprobs:
                mean_lp = sum(logprobs) / len(logprobs)
                confidence = math.exp(mean_lp)
                confidence = max(0.0, min(1.0, confidence))
            else:
                confidence = 0.0
            return (full_text, confidence)
        except Exception:
            return ("", 0.0)
```

- [ ] **Step 3: 建立 asr_base.py 工廠**

```python
"""ASR 後端工廠：依 config.ASR_BACKEND 選擇引擎類別。

共同契約（CONTRACTS.md）：所有引擎皆提供
- available() -> bool
- transcribe(wav_path: str) -> tuple[str, float]
- _ensure_model()  # app.py 預熱會呼叫
"""

from __future__ import annotations


def get_asr_engine_class(backend: str | None = None) -> type:
    """回傳 ASR 引擎類別。

    - backend 未給 → 讀 config.ASR_BACKEND（讀取失敗保底 "sensevoice"）。
    - "whisper" → WhisperASREngine；其餘（含 "sensevoice" 與未知值）→ SenseVoiceASREngine。
    - 依「不自動 fallback」原則：未知值走主力 sensevoice，不猜測退回 whisper。
    """
    if backend is None:
        try:
            from server.config import ASR_BACKEND
            backend = ASR_BACKEND
        except Exception:
            backend = "sensevoice"
    if backend == "whisper":
        from server.asr_whisper import WhisperASREngine
        return WhisperASREngine
    from server.asr_sensevoice import SenseVoiceASREngine
    return SenseVoiceASREngine
```

> 註：`asr_sensevoice` 於 Task 2 建立；Task 1 中 `ASR_BACKEND="whisper"`，此 import 分支不會被執行，故 Task 1 測試不受影響。

- [ ] **Step 4: 把 asr.py 改成薄 shim**

覆寫 `server/asr.py` 全部內容：

```python
"""ASR 引擎對外入口（薄 shim）。

維持 `from server.asr import ASREngine` 相容：ASREngine 綁定為工廠依
config.ASR_BACKEND 選定的引擎類別。實際實作見 asr_whisper.py / asr_sensevoice.py。
"""

from __future__ import annotations

from server.asr_base import get_asr_engine_class

# 於 import 時依 config.ASR_BACKEND 綁定選定類別；app.py 以 ASREngine() 實例化。
ASREngine = get_asr_engine_class()
```

- [ ] **Step 5: 寫工廠測試（先失敗）**

建立 `tests/test_asr_backend.py`：

```python
# -*- coding: utf-8 -*-
"""ASR 後端工廠與引擎的單元測試（不載入真模型）。"""

from __future__ import annotations


def test_factory_returns_whisper_class():
    from server.asr_base import get_asr_engine_class
    from server.asr_whisper import WhisperASREngine
    assert get_asr_engine_class("whisper") is WhisperASREngine


def test_asr_shim_exposes_engine_class():
    # shim 需可實例化，且具契約方法
    from server.asr import ASREngine
    eng = ASREngine()
    assert hasattr(eng, "available")
    assert hasattr(eng, "transcribe")
    assert hasattr(eng, "_ensure_model")
```

- [ ] **Step 6: 執行測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_asr_backend.py -v`
Expected: FAIL（`asr_whisper` / `asr_base` 尚未被正確 import 或前面步驟未存檔前）。若步驟 1-5 皆已存檔，改為驗證通過（見 Step 7）。

- [ ] **Step 7: 執行全部測試確認綠**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS（既有 41 + 新 2 = 43 passed；`ASR_BACKEND="whisper"` 下行為與重構前一致）。

- [ ] **Step 8: Commit**

```bash
git add server/asr.py server/asr_whisper.py server/asr_base.py server/config.py tests/test_asr_backend.py
git commit -m "refactor: split ASR into pluggable backend with whisper preserved"
```

---

### Task 2: SenseVoice 引擎 + OpenCC 簡轉繁

新增 `asr_sensevoice.py`，以 sherpa-onnx OfflineRecognizer 載入 SenseVoice-Small，
輸出經 OpenCC `s2twp` 簡轉繁。單元測試以 fake recognizer + monkeypatch，不載入真模型。

**Files:**
- Create: `server/asr_sensevoice.py`
- Modify: `tests/test_asr_backend.py`（追加 SenseVoice 測試）

**Interfaces:**
- Consumes: `config.SENSEVOICE_DIR`、`config.OPENCC_CONFIG`。
- Produces: `server.asr_sensevoice.SenseVoiceASREngine`（`available()`/`transcribe()`/`_ensure_model()`），
  及 module-level `_read_wav(path) -> tuple[samples, sample_rate]`（供測試 monkeypatch）。

- [ ] **Step 1: 先裝 opencc（測試需要）**

Run: `.venv/bin/pip install opencc`
Expected: 成功安裝 opencc 1.4.0。驗證：`.venv/bin/python -c "import opencc; print(opencc.OpenCC('s2twp').convert('我爱学习'))"` → 印出 `我愛學習`。

- [ ] **Step 2: 寫 SenseVoice 測試（先失敗）**

在 `tests/test_asr_backend.py` 追加：

```python
# --- SenseVoice 引擎測試（fake recognizer，不載入真模型）---

class _FakeResult:
    def __init__(self, text): self.text = text


class _FakeStream:
    def __init__(self, text): self.result = _FakeResult(text)
    def accept_waveform(self, sample_rate, samples): pass


class _FakeRecognizer:
    def __init__(self, text): self._text = text
    def create_stream(self): return _FakeStream(self._text)
    def decode_stream(self, stream): pass


def test_factory_returns_sensevoice_class():
    from server.asr_base import get_asr_engine_class
    from server.asr_sensevoice import SenseVoiceASREngine
    assert get_asr_engine_class("sensevoice") is SenseVoiceASREngine


def test_sensevoice_available_false_when_model_missing(monkeypatch, tmp_path):
    from server import config
    from server.asr_sensevoice import SenseVoiceASREngine
    monkeypatch.setattr(config, "SENSEVOICE_DIR", tmp_path / "nope")
    eng = SenseVoiceASREngine()
    assert eng.available() is False


def test_sensevoice_opencc_s2twp():
    from server.asr_sensevoice import SenseVoiceASREngine
    eng = SenseVoiceASREngine()
    cc = eng._ensure_opencc()
    assert cc is not None
    assert cc.convert("我爱学习") == "我愛學習"


def test_sensevoice_transcribe_converts_to_traditional(monkeypatch):
    import numpy as np
    from server import asr_sensevoice as m
    eng = m.SenseVoiceASREngine()
    eng._recognizer = _FakeRecognizer("我爱学习")  # 簡體 stub
    monkeypatch.setattr(
        m, "_read_wav", lambda p: (np.zeros(16000, dtype="float32"), 16000)
    )
    text, conf = eng.transcribe("dummy.wav")
    assert text == "我愛學習"
    assert conf == 1.0


def test_sensevoice_transcribe_empty_returns_zero(monkeypatch):
    import numpy as np
    from server import asr_sensevoice as m
    eng = m.SenseVoiceASREngine()
    eng._recognizer = _FakeRecognizer("")  # 空辨識
    monkeypatch.setattr(
        m, "_read_wav", lambda p: (np.zeros(16000, dtype="float32"), 16000)
    )
    assert eng.transcribe("dummy.wav") == ("", 0.0)


def test_sensevoice_transcribe_returns_zero_when_model_unavailable():
    from server.asr_sensevoice import SenseVoiceASREngine
    eng = SenseVoiceASREngine()
    eng._load_failed = True  # 模擬載入失敗
    assert eng.transcribe("dummy.wav") == ("", 0.0)
```

- [ ] **Step 3: 執行測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_asr_backend.py -v`
Expected: FAIL（`ModuleNotFoundError: server.asr_sensevoice`）。

- [ ] **Step 4: 建立 asr_sensevoice.py**

```python
"""ASR 引擎（sherpa-onnx + SenseVoice-Small，OpenCC 簡轉繁）。

契約（CONTRACTS.md）：available() / transcribe(wav_path) -> (text, confidence) / _ensure_model()。

規格：
- sherpa_onnx.OfflineRecognizer.from_sense_voice(model, tokens, use_itn=True) 單例懶載入。
- 輸入 16kHz mono wav（pipeline 已用 ffmpeg 轉好）；以 soundfile 讀 float32。
- 信心分數：辨識文字為空 → ("", 0.0)；非空 → (繁體text, 1.0)。SenseVoice 非自回歸，
  無 avg_logprob，改以「空結果」判斷雜音兜底。
- 簡轉繁：OpenCC s2twp，缺失/失敗 → 降級為原文（不 throw）。
- import 期不可炸；任何 transcribe 失敗回 ("", 0.0)。
"""

from __future__ import annotations

import threading


def _read_wav(path: str):
    """讀 wav 為 (samples float32 ndarray, sample_rate)。抽成 module 函式以利測試 monkeypatch。"""
    import soundfile as sf
    samples, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    return samples, sample_rate


class SenseVoiceASREngine:
    """sherpa-onnx SenseVoice 語音辨識引擎（單例懶載入、降級安全）。"""

    def __init__(self) -> None:
        self._recognizer = None
        self._load_failed = False
        self._opencc = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def available(self) -> bool:
        """sherpa_onnx 可 import 且 SenseVoice 模型檔存在 → True；載入失敗過 → False。"""
        if self._recognizer is not None:
            return True
        if self._load_failed:
            return False
        try:
            import sherpa_onnx  # noqa: F401
        except Exception:
            return False
        try:
            from server.config import SENSEVOICE_DIR
            return (SENSEVOICE_DIR / "model.int8.onnx").exists()
        except Exception:
            return False

    # ------------------------------------------------------------------
    def _ensure_model(self):
        """懶載入 OfflineRecognizer 單例；失敗回 None 並記錄失敗狀態。"""
        if self._recognizer is not None:
            return self._recognizer
        if self._load_failed:
            return None
        with self._lock:
            if self._recognizer is not None:
                return self._recognizer
            if self._load_failed:
                return None
            try:
                import sherpa_onnx
                from server.config import SENSEVOICE_DIR
                model = str(SENSEVOICE_DIR / "model.int8.onnx")
                tokens = str(SENSEVOICE_DIR / "tokens.txt")
                self._recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                    model=model,
                    tokens=tokens,
                    use_itn=True,
                    num_threads=2,
                )
            except Exception:
                self._recognizer = None
                self._load_failed = True
        return self._recognizer

    # ------------------------------------------------------------------
    def _ensure_opencc(self):
        """懶載入 OpenCC 轉換器；缺失/失敗回 None（不 throw）。"""
        if self._opencc is not None:
            return self._opencc
        try:
            import opencc
            from server.config import OPENCC_CONFIG
            self._opencc = opencc.OpenCC(OPENCC_CONFIG)
        except Exception:
            self._opencc = None
        return self._opencc

    # ------------------------------------------------------------------
    def transcribe(self, wav_path: str) -> tuple[str, float]:
        """辨識 wav，回傳 (繁體text, confidence)。失敗回 ("", 0.0)，不 throw。"""
        recognizer = self._ensure_model()
        if recognizer is None:
            return ("", 0.0)
        try:
            samples, sample_rate = _read_wav(wav_path)
            if getattr(samples, "ndim", 1) > 1:
                samples = samples[:, 0]  # 多聲道取第一聲道
            stream = recognizer.create_stream()
            stream.accept_waveform(sample_rate, samples)
            recognizer.decode_stream(stream)
            text = (stream.result.text or "").strip()
            if not text:
                return ("", 0.0)
            cc = self._ensure_opencc()
            if cc is not None:
                try:
                    text = cc.convert(text).strip()
                except Exception:
                    pass
            return (text, 1.0) if text else ("", 0.0)
        except Exception:
            return ("", 0.0)
```

- [ ] **Step 5: 執行測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_asr_backend.py -v`
Expected: PASS（工廠、available、opencc、簡轉繁、空辨識、載入失敗共 8 個測試綠）。

- [ ] **Step 6: 執行全部測試確認無回歸**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS（既有 41 + 新測試皆綠；`ASR_BACKEND` 仍為 "whisper"）。

- [ ] **Step 7: Commit**

```bash
git add server/asr_sensevoice.py tests/test_asr_backend.py
git commit -m "feat: add sherpa-onnx SenseVoice ASR engine with OpenCC s2twp"
```

---

### Task 3: 切換預設為 SenseVoice + setup_env.sh 下載 + 文件

把預設 backend 切為 sensevoice，更新安裝腳本（裝 opencc、下載 SenseVoice 模型），
並同步 README / PLAN_ALIGNMENT 說明。

**Files:**
- Modify: `server/config.py:ASR_BACKEND`
- Modify: `scripts/setup_env.sh`
- Modify: `README.md`（ASR 段落 + 降級表 + 已知限制）
- Modify: `docs/superpowers/specs/2026-07-04-asr-sensevoice-migration-design.md`（無需改；僅供對照）
- Test: `tests/test_asr_backend.py`（追加預設值測試）

**Interfaces:**
- Consumes: Task 1/2 全部。
- Produces: 預設執行走 SenseVoice。

- [ ] **Step 1: 追加預設 backend 測試（先失敗）**

在 `tests/test_asr_backend.py` 追加：

```python
def test_factory_default_is_sensevoice():
    from server.asr_base import get_asr_engine_class
    from server.asr_sensevoice import SenseVoiceASREngine
    assert get_asr_engine_class() is SenseVoiceASREngine
```

- [ ] **Step 2: 執行確認失敗**

Run: `.venv/bin/python -m pytest tests/test_asr_backend.py::test_factory_default_is_sensevoice -v`
Expected: FAIL（目前 config 預設 "whisper"，工廠回 WhisperASREngine）。

- [ ] **Step 3: 切換 config 預設**

在 `server/config.py` 把：

```python
ASR_BACKEND = "whisper"  # "sensevoice" | "whisper"（Task 3 會改為預設 sensevoice）
```

改為：

```python
ASR_BACKEND = "sensevoice"  # "sensevoice" | "whisper"；切回 whisper 僅需改此值
```

- [ ] **Step 4: 執行確認通過 + 全套無回歸**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS。注意：e2e 測試會建立 `ASREngine()`（現為 SenseVoice），因無模型檔 `available()=False`，`/api/status` 回 `asr:false`，測試不應斷言 asr 為 true（既有測試以 stub 為主，不受影響）。若有測試斷言真 ASR 可用，改用 stub 或標記 skip 並在 commit 訊息註明。

- [ ] **Step 5: 更新 setup_env.sh —— 裝 opencc**

在 `scripts/setup_env.sh` 的 `=== [2/4] 安裝 ASR / TTS ===` 區塊，`sherpa-onnx` 那行之後加：

```bash
# ASR 主力：sherpa-onnx + SenseVoice-Small（int8）；OpenCC 做簡轉繁（s2twp）
.venv/bin/pip install opencc 2>&1 | tail -2
```

- [ ] **Step 6: 更新 setup_env.sh —— 下載 SenseVoice 模型**

在 `=== [3/4] 下載模型 ===` 的 Python heredoc 之後（`PY` 結束後、`=== [4/4]` 之前）加入：

```bash
echo "=== [3.5/4] 下載 SenseVoice int8 模型（sherpa-onnx 官方 release, ~226MB）==="
SENSEVOICE_TARBALL="sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2"
SENSEVOICE_DIR="models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
if [ ! -f "$SENSEVOICE_DIR/model.int8.onnx" ]; then
  curl -L -o "models/$SENSEVOICE_TARBALL" \
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$SENSEVOICE_TARBALL"
  tar -xjf "models/$SENSEVOICE_TARBALL" -C models/
  rm -f "models/$SENSEVOICE_TARBALL"
  echo "SenseVoice ok"
else
  echo "SenseVoice 已存在，略過下載"
fi
```

- [ ] **Step 7: 更新 README.md**

- 架構圖與內文的 `ASR (faster-whisper small, int8)` 改為
  `ASR (sherpa-onnx + SenseVoice-Small int8, OpenCC s2twp；faster-whisper 為 flag 可切換 fallback)`。
- 降級行為表「ASR 信心過低 / 無模型」列，補：SenseVoice 以「空辨識結果」判斷兜底（非 logprob 門檻）。
- 已知限制補一條：SenseVoice 權重授權 NOASSERTION（近 Apache），黑客松展示無虞、商用前建議法務覆核；OpenCC s2twp 一簡對多繁歧義字為已知極少數風險，僅影響書面逐字稿。
- 安裝段補：需 `opencc`，首次安裝會下載 SenseVoice int8 模型（~226MB）。

- [ ] **Step 8: Commit**

```bash
git add server/config.py scripts/setup_env.sh README.md tests/test_asr_backend.py
git commit -m "feat: switch default ASR backend to SenseVoice; update setup + docs"
```

---

### Task 4: 真模型端到端冒煙驗證（手動）

自動化測試不載入真模型；此任務以真模型手動驗證整條語音管線。需下載 ~226MB 模型。

**Files:** 無（僅驗證與記錄）。

- [ ] **Step 1: 下載 SenseVoice 模型**

Run（若尚未由 setup_env.sh 下載）：

```bash
cd /home/budaedu/hackathon/talkybuddy
bash scripts/setup_env.sh   # 或只手動跑 Task 3 Step 6 的 curl+tar 片段
```

驗證：`ls models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/model.int8.onnx` 存在。

- [ ] **Step 2: 驗證引擎可用**

Run:

```bash
.venv/bin/python -c "from server.asr import ASREngine; e=ASREngine(); print('available:', e.available()); print('class:', type(e).__name__)"
```

Expected: `available: True`、`class: SenseVoiceASREngine`。

- [ ] **Step 3: 直接辨識一個 wav 冒煙**

準備一段 16kHz mono 中英夾雜 wav（或用 ffmpeg 轉任一錄音），Run:

```bash
.venv/bin/python -c "from server.asr import ASREngine; e=ASREngine(); print(e.transcribe('PATH_TO_16k_mono.wav'))"
```

Expected: 回 `(繁體中文字串, 1.0)`；輸出為繁體（非簡體）。

- [ ] **Step 4: 起服務 + WebSocket 端到端**

Run: `bash scripts/run.sh`，瀏覽器開 http://localhost:8787，按住企鵝肚子錄一句中英夾雜。
Expected: 收到 `asr_result` 文字為**繁體**、後續 `reply` + `tts_audio` 正常；`/api/status` 回 `asr:true`。

- [ ] **Step 5: 驗證 flag 可切回 whisper**

暫時把 `config.ASR_BACKEND` 改回 `"whisper"`，重啟服務，確認辨識仍可運作（faster-whisper 路徑），再改回 `"sensevoice"`。記錄兩者單輪延遲對照。

- [ ] **Step 6: 更新 PLAN_ALIGNMENT.md**

在 `PLAN_ALIGNMENT.md` 補一段「ASR 換 SenseVoice」的落地紀錄：實測 available、簡轉繁樣本、單輪延遲、flag 切換驗證結果。Commit：

```bash
git add PLAN_ALIGNMENT.md
git commit -m "docs: record SenseVoice ASR migration verification results"
```

---

## Self-Review

**Spec coverage：**
- 分檔架構（asr_base/whisper/sensevoice/shim）→ Task 1。
- SenseVoice 引擎 + 載入 + available + transcribe → Task 2。
- 信心「空→0.0 / 非空→1.0」→ Task 2 Step 4 + 測試。
- OpenCC s2twp → Task 2。
- feature flag + 不自動 fallback → Task 1（flag）+ Task 3（預設切換）+ 工廠邏輯。
- setup_env.sh（opencc + 模型下載，保留 whisper）→ Task 3。
- 測試 test_asr_backend + test_pipeline 不受影響 → Task 1/2/3。
- 手動端到端冒煙 → Task 4。
- `app.py` `_ensure_model()` 相容 → 兩引擎皆實作，Task 1/2。

**Placeholder scan：** 無 TBD/TODO；每個 code step 附完整程式碼與指令。README 更新（Task 3 Step 7）為文件描述性步驟，非程式碼佔位，可接受。

**Type consistency：** `WhisperASREngine` / `SenseVoiceASREngine` / `get_asr_engine_class` / `_read_wav` / `_ensure_model` / `_ensure_opencc` 命名於各 task 一致；`stream.result.text`、`from_sense_voice(model, tokens, use_itn, num_threads)` 已對 sherpa-onnx 1.13.3 實機確認。

**待實作時覆核（低風險）：** `stream.result.text` 屬性名、SenseVoice tarball 內實際檔名（`model.int8.onnx` / `tokens.txt`）—— 於 Task 4 Step 1 解壓後 `ls` 確認，如有差異調整 config 與 setup 路徑。
