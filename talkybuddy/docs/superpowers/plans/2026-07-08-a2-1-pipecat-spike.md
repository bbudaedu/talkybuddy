# A2-1 Pipecat 整合 Spike Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用一支拋棄式 spike 回答單一 go/no-go 問句——Pipecat 能否「同時」容納批次 SenseVoice 當 STT、句級可中止 sherpa 當 TTS，被程式化 barge-in 在句界乾淨打斷，且完全不碰 `pipeline._process_text`。

**Architecture:** 落點 `talkybuddy/spike/a2_pipecat/`，用**獨立 venv** 與正式環境隔離。核心「可中止逐句合成」邏輯（sherpa callback 中止＋句界 break）與 Pipecat **解耦成 engine-agnostic 模組**、以真 TDD 驗證；Pipecat 整合層（STT service／TTS 基類／interruption frame）屬 spec 明訂的「待發現未知」，以**探查步驟＋二元觀察判準＋記錄**驅動，非預設答案。全程不 import、不改動 `server/`。

**Tech Stack:** Python 3.12、Pipecat（`pipecat-ai[funasr]`，內建 `FunASRSTTService`＝`iic/SenseVoiceSmall`）、sherpa-onnx 1.13.3（TTS，`OfflineTts.generate(text, callback)`）、pytest。

## 這份 plan 對 spike 的 TDD 調適（先讀）

spec（`docs/superpowers/specs/2026-07-08-a2-1-pipecat-spike-design.md`）定位為**拋棄式 go/no-go、二元淨判、不量延遲**，且明列**三個「待發現未知」**。因此本 plan 分兩種任務型態，兩者都可執行、都有明確判準，**沒有** placeholder：

1. **可先驗證的核心（真 TDD）**：sherpa 逐句合成＋callback 中止＋句界 break——callback 簽章已一手驗證（見 Global Constraints），與 Pipecat 無關，故用 `pytest` 紅→綠→commit 的標準 TDD（Task 3）。
2. **Pipecat 整合層（探查驅動）**：`FunASRSTTService` 行為、TTS 該 subclass 哪個基類、注入 interruption 的 frame 類型——spec 明訂「不預設答案，spike 的價值就是實測出來」。這些任務的「測試」＝**執行探查腳本、比對二元觀察判準、把發現寫進 `SPIKE-RESULT.md`**。plan 提供最可能的 API 骨架作為起點，但每步都要求「以 probe 出的實際 API 為準」。

---

## Global Constraints

> 專案級要求，每個 task 隱含適用；數值逐字取自 spec 與一手查證。

- **落點**：`talkybuddy/spike/a2_pipecat/`（新目錄，拋棄式）。全程**不 import 也不修改** `talkybuddy/server/` 任何模組。
- **依賴隔離**：獨立 venv `talkybuddy/spike/a2_pipecat/.venv`（使用者 2026-07-08 簽核），與正式 `talkybuddy/.venv` 完全隔離。spike venv 與其下載的 `SenseVoiceSmall`（~1GB）**不進 git**。
- **sherpa TTS 中止契約（已一手驗證，sherpa-onnx 1.13.3）**：`OfflineTts.generate(text: str, sid=0, speed=1.0, callback=None) -> GeneratedAudio`；`callback(samples: np.ndarray[float32], progress: float) -> int`，**回非 0 值即提早停止合成**。回傳 `GeneratedAudio` 有 `.samples`（float [-1,1]）與 `.sample_rate`。spike venv 裝好後**須在 Task 1 覆核此版本仍是 1.13.3 / 簽章不變**。
- **sherpa 模型與資料（唯讀，沿用正式環境現成產物，不重算、不引 onnx/piper 依賴）**：
  - patched onnx：`talkybuddy/models/_sherpa_cache/zh_CN-huayan-medium.onnx`
  - tokens：`talkybuddy/models/_sherpa_cache/zh_CN-huayan-medium.tokens.txt`
  - espeak-ng-data：`talkybuddy/.venv/lib/python3.12/site-packages/piper/espeak-ng-data`
- **canned wav（判準 #2 輸入）**：`talkybuddy/models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/test_wavs/zh.wav`（16kHz、mono、16-bit、5.59s 中文語音）。
- **stub LLM 固定回覆（多句、可句界打斷）**：`"你好呀，我是企鵝。今天天氣真好。我們一起來玩遊戲吧。你想先做什麼呢？"`（4 句）。
- **二元 Pass 判準（全中才 PASS，出自 spec）**：①Pipeline 跑得起來不 crash；②`FunASRSTTService` 把 canned wav 當一整段轉出文字；③stub LLM 多句回覆被 sherpa 逐句合成；④第 1 句播放中注入 interruption → 下一句 callback 回中止值、在句界乾淨停住（非等整段合成完）；⑤全程未 import／未呼叫 `_process_text`。
- **`_process_text` 真實簽章（判準 #5 grep 基準）**：`server/pipeline.py:188` `async def _process_text(self, result: TurnResult, emit, t0: float)`。spike 不得出現此符號。
- **交付物**：`talkybuddy/spike/a2_pipecat/SPIKE-RESULT.md`，含 PASS/FAIL、三個未知的實測答案、任何 workaround、以及「若留用，STT 換成包 `asr.py`／TTS 介面如何長成 `StreamingTurnManager` 一部分」的一句話銜接。
- **失敗止血**：Pipecat 裝不起來／import 就炸＝no-go 訊號，記錄後停手（不強推）；備援路線＝改驗 LiveKit `StreamAdapter`（另案，不在本 plan）。

---

## File Structure

所有路徑相對 `talkybuddy/`（執行目錄）。

- `spike/a2_pipecat/.gitignore` — 忽略 `.venv/` 與模型快取，避免 1GB 產物進 git。
- `spike/a2_pipecat/requirements.txt` — spike venv 依賴清單（可重建）。
- `spike/a2_pipecat/README.md` — 如何建 venv、跑 spike、判準對照。
- `spike/a2_pipecat/sherpa_voice.py` — 自帶最小 sherpa 載入（複製 `tts.py` 精神、指向現成快取），`load_zh_voice()`。
- `spike/a2_pipecat/interruptible_synth.py` — **engine-agnostic 核心**：`InterruptibleSynth`（callback 中止＋逐句 break）＋`split_sentences()`。不 import Pipecat。
- `spike/a2_pipecat/tests/test_interruptible_synth.py` — 核心的真 TDD 單元測試（用 spike venv 的真 sherpa）。
- `spike/a2_pipecat/probe_pipecat.py` — 探查已裝 Pipecat 版本的 API（STT／TTS 基類／interruption frame／pipeline 執行方式），把發現印出並附記錄範本。
- `spike/a2_pipecat/interruptible_tts.py` — `SherpaInterruptibleTTSService`：把 `InterruptibleSynth` 包成 probe 出的 Pipecat TTS 基類子類。
- `spike/a2_pipecat/spike_parts.py` — `StubLLMService`、`InterruptDriver`、`OutputSink`（三個小殼，依 probe 出的 Pipecat frame API）。
- `spike/a2_pipecat/run_spike.py` — 組 pipeline、依序跑判準 #1~#4、印出觀察結果。
- `spike/a2_pipecat/SPIKE-RESULT.md` — 最終交付物。

---

### Task 1: 建獨立 venv 並裝 Pipecat（Step 0，go/no-go 閘＋判準 #1 前置）

**Files:**
- Create: `spike/a2_pipecat/.gitignore`
- Create: `spike/a2_pipecat/requirements.txt`
- Create: `spike/a2_pipecat/README.md`

**Interfaces:**
- Consumes: 無。
- Produces: 可用的 spike venv（`spike/a2_pipecat/.venv/bin/python`）、已記錄的 Pipecat 版本、已覆核的 sherpa-onnx 版本/簽章。後續所有 task 用此 venv 執行。

- [ ] **Step 1: 建 spike 目錄與 .gitignore**

```bash
mkdir -p spike/a2_pipecat/tests
cat > spike/a2_pipecat/.gitignore <<'EOF'
# 拋棄式 spike：venv 與下載的模型快取不進 git
.venv/
__pycache__/
*.pyc
.pytest_cache/
EOF
```

- [ ] **Step 2: 寫 requirements.txt**

```bash
cat > spike/a2_pipecat/requirements.txt <<'EOF'
# A2-1 spike 獨立 venv 依賴（與正式 talkybuddy/.venv 隔離）
pipecat-ai[funasr]
sherpa-onnx
numpy
pytest
EOF
```

- [ ] **Step 3: 建獨立 venv 並安裝（本身即 go/no-go 訊號）**

Run（從 `talkybuddy/` 執行）：
```bash
python3 -m venv spike/a2_pipecat/.venv
spike/a2_pipecat/.venv/bin/pip install --upgrade pip
spike/a2_pipecat/.venv/bin/pip install -r spike/a2_pipecat/requirements.txt
```
Expected: 安裝完成、無編譯級錯誤。**若 `pipecat-ai[funasr]` 在此 Python 裝不起來或相依衝突 → 這就是 no-go 訊號**：記錄完整錯誤到 `SPIKE-RESULT.md`（Task 8 建立，可先手記），停手並回報使用者，不強推。

- [ ] **Step 4: 覆核版本與乾淨 import（判準 #1 前置）**

Run：
```bash
spike/a2_pipecat/.venv/bin/python - <<'PY'
import pipecat, sherpa_onnx
print("pipecat:", getattr(pipecat, "__version__", "?"))
print("sherpa_onnx:", getattr(sherpa_onnx, "__version__", "?"))
# 覆核 TTS callback 簽章仍如 Global Constraints 所述
print(sherpa_onnx.OfflineTts.generate.__doc__.splitlines()[3])
PY
```
Expected: 印出 pipecat 版本、`sherpa_onnx: 1.13.3`（或更新且簽章相容）、generate docstring 含 `callback`。若 sherpa 版本不同，**記錄新簽章**供 Task 3 對照。

- [ ] **Step 5: 寫 README（如何重跑）**

```bash
cat > spike/a2_pipecat/README.md <<'EOF'
# A2-1 Pipecat 整合 Spike（拋棄式 go/no-go）

隔離於獨立 venv，驗證「批次 SenseVoice + 句級可中止 sherpa」能否共存於 Pipecat 並被程式化 barge-in 句界打斷，全程不碰 pipeline._process_text。

## 建 venv（於 talkybuddy/ 執行）
    python3 -m venv spike/a2_pipecat/.venv
    spike/a2_pipecat/.venv/bin/pip install -r spike/a2_pipecat/requirements.txt

## 跑核心單元測試（真 TDD 部分）
    spike/a2_pipecat/.venv/bin/python -m pytest spike/a2_pipecat/tests -v

## 探查 Pipecat API
    spike/a2_pipecat/.venv/bin/python spike/a2_pipecat/probe_pipecat.py

## 跑整合 spike（判準 #1~#4）
    spike/a2_pipecat/.venv/bin/python spike/a2_pipecat/run_spike.py

結論見 SPIKE-RESULT.md。首次跑會下載 iic/SenseVoiceSmall（~1GB）。
EOF
```

- [ ] **Step 6: Commit**

```bash
git add spike/a2_pipecat/.gitignore spike/a2_pipecat/requirements.txt spike/a2_pipecat/README.md
git commit -m "chore(a2-1 spike): scaffold isolated venv + deps for pipecat go/no-go"
```

---

### Task 2: 探查已裝 Pipecat 的 API（記錄未知 #2、#3 的骨架）

**Files:**
- Create: `spike/a2_pipecat/probe_pipecat.py`

**Interfaces:**
- Consumes: Task 1 的 spike venv。
- Produces: 三項待發現事實的實測值，供 Task 4/6 引用——(a)`FunASRSTTService` 的正確匯入路徑與建構參數；(b)可中止 TTS 該 subclass 的基類名（`InterruptibleTTSService` 或 `TTSService`）與其須實作的方法（如 `run_tts`）；(c)注入程式化 interruption 的 frame 類別名（`StartInterruptionFrame` / `BotInterruptionFrame` / `UserStartedSpeakingFrame` 之一或組合）；(d)最小 pipeline 執行 API（`Pipeline` / `PipelineTask` / `PipelineRunner` 的匯入與跑法）。

> 這是探查任務，非 TDD。目標是「把 spec 未知 #2/#3 的實際 API 打撈出來並記錄」。以下腳本用 `importlib`/`inspect` 反射，不預設答案。

- [ ] **Step 1: 寫 probe 腳本**

```python
# spike/a2_pipecat/probe_pipecat.py
"""探查已裝 Pipecat 版本的實際 API，供 spike 其餘部分對接。不預設答案。"""
import importlib
import inspect
import pkgutil


def show(label, obj):
    print(f"\n=== {label} ===")
    print(obj)


def try_import(path):
    try:
        mod = importlib.import_module(path)
        return mod
    except Exception as e:  # noqa: BLE001
        print(f"[import FAIL] {path}: {e}")
        return None


def main():
    import pipecat
    show("pipecat version", getattr(pipecat, "__version__", "?"))

    # (a) FunASRSTTService 匯入路徑
    for path in ("pipecat.services.funasr.stt", "pipecat.services.funasr"):
        mod = try_import(path)
        if mod and hasattr(mod, "FunASRSTTService"):
            cls = mod.FunASRSTTService
            show(f"FunASRSTTService @ {path}", cls)
            show("  __init__ signature", inspect.signature(cls.__init__))
            show("  MRO", [c.__name__ for c in cls.__mro__])
            break

    # (b) 可中止 TTS 基類候選
    for path, name in (
        ("pipecat.services.tts_service", "InterruptibleTTSService"),
        ("pipecat.services.tts_service", "TTSService"),
        ("pipecat.services.ai_services", "TTSService"),
    ):
        mod = try_import(path)
        if mod and hasattr(mod, name):
            cls = getattr(mod, name)
            show(f"TTS base {name} @ {path}", cls)
            methods = [m for m in dir(cls) if "tts" in m.lower() or m in ("run_tts", "flush")]
            show("  tts-ish methods", methods)

    # (c) interruption frame 候選
    frames = try_import("pipecat.frames.frames")
    if frames:
        cands = [n for n in dir(frames) if "Interrupt" in n or "StartedSpeaking" in n or "StoppedSpeaking" in n]
        show("interruption-related frames", cands)

    # (d) pipeline 執行 API
    for path, names in (
        ("pipecat.pipeline.pipeline", ["Pipeline"]),
        ("pipecat.pipeline.task", ["PipelineTask"]),
        ("pipecat.pipeline.runner", ["PipelineRunner"]),
    ):
        mod = try_import(path)
        if mod:
            for n in names:
                if hasattr(mod, n):
                    show(f"{n} @ {path}", getattr(mod, n))

    print("\n>>> 把上面結果謄進 SPIKE-RESULT.md 的『未知 #2/#3 實測』。")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 執行並閱讀輸出**

Run：
```bash
spike/a2_pipecat/.venv/bin/python spike/a2_pipecat/probe_pipecat.py 2>&1 | tee spike/a2_pipecat/_probe_out.txt
```
Expected: 印出 FunASRSTTService 的匯入路徑與 `__init__` 參數、TTS 基類的 MRO 與須覆寫方法、interruption frame 候選清單、Pipeline/Task/Runner 匯入位置。**記下**：TTS 要 subclass 哪個類、要覆寫哪個方法（多半是 `run_tts`）、interruption 用哪個 frame。若某項 import 全 FAIL，記錄為該版本缺該 API（影響後續策略）。

- [ ] **Step 3: Commit**

```bash
git add spike/a2_pipecat/probe_pipecat.py
git commit -m "feat(a2-1 spike): add pipecat API probe (STT/TTS base/interruption frame)"
```

> `_probe_out.txt` 被 .gitignore 排除（`*.txt` 非忽略項——若不想追蹤，執行時改導到 `/tmp` 或手動不 add）。本 plan 不 commit 它。

---

### Task 3: engine-agnostic 可中止逐句合成核心（真 TDD）

**Files:**
- Create: `spike/a2_pipecat/sherpa_voice.py`
- Create: `spike/a2_pipecat/interruptible_synth.py`
- Test: `spike/a2_pipecat/tests/test_interruptible_synth.py`

**Interfaces:**
- Consumes: Global Constraints 的 sherpa 模型/espeak 路徑、callback 契約。
- Produces:
  - `sherpa_voice.load_zh_voice() -> sherpa_onnx.OfflineTts`（載入 huayan zh voice；模型缺則 raise `FileNotFoundError`）。
  - `interruptible_synth.split_sentences(text: str) -> list[str]`（按 `。！？!?` 切句、去空白、保留非空句）。
  - `interruptible_synth.InterruptibleSynth`：
    - `__init__(self, voice)`
    - `interrupt(self) -> None` / `reset(self) -> None` / `is_interrupted(self) -> bool`
    - `_callback(self, samples, progress) -> int`（interrupted 時回 `1`，否則 `0`）
    - `synth_sentences(self, sentences: list[str]) -> Iterator[tuple[int, object]]`（逐句 `voice.generate(s, callback=self._callback)`；每句前檢查 interrupted、被打斷則 break；yield `(index, GeneratedAudio)`）

- [ ] **Step 1: 寫 sherpa_voice.py（自帶最小載入，不 import server）**

```python
# spike/a2_pipecat/sherpa_voice.py
"""自帶最小 sherpa-onnx zh voice 載入（複製 server/tts.py 精神，指向現成快取）。

刻意不 import server/：spike 拋棄式、與正式碼解耦。沿用 _sherpa_cache 現成的
patched onnx + tokens.txt，故不需 onnx/piper 依賴，也不重算 metadata。
"""
from __future__ import annotations

from pathlib import Path

# spike/a2_pipecat/ 往上兩層 = talkybuddy/
_TB_ROOT = Path(__file__).resolve().parents[2]
_CACHE = _TB_ROOT / "models" / "_sherpa_cache"
_ONNX = _CACHE / "zh_CN-huayan-medium.onnx"
_TOKENS = _CACHE / "zh_CN-huayan-medium.tokens.txt"
_ESPEAK = _TB_ROOT / ".venv" / "lib" / "python3.12" / "site-packages" / "piper" / "espeak-ng-data"


def load_zh_voice():
    """載入 huayan 中文 sherpa OfflineTts；任何檔缺則 raise FileNotFoundError。"""
    import sherpa_onnx

    for p in (_ONNX, _TOKENS, _ESPEAK):
        if not p.exists():
            raise FileNotFoundError(f"spike sherpa asset missing: {p}")

    vits = sherpa_onnx.OfflineTtsVitsModelConfig(
        model=str(_ONNX), tokens=str(_TOKENS), data_dir=str(_ESPEAK), lexicon=""
    )
    model_cfg = sherpa_onnx.OfflineTtsModelConfig(vits=vits, num_threads=1, provider="cpu")
    tts_cfg = sherpa_onnx.OfflineTtsConfig(model=model_cfg)
    if not tts_cfg.validate():
        raise RuntimeError("invalid sherpa-onnx tts config")
    return sherpa_onnx.OfflineTts(tts_cfg)
```

- [ ] **Step 2: 寫失敗測試（先紅）**

```python
# spike/a2_pipecat/tests/test_interruptible_synth.py
"""可中止逐句合成核心的單元測試（真 sherpa，不依賴 Pipecat）。"""
import numpy as np
import pytest

from spike.a2_pipecat.interruptible_synth import InterruptibleSynth, split_sentences
from spike.a2_pipecat.sherpa_voice import load_zh_voice

_REPLY = "你好呀，我是企鵝。今天天氣真好。我們一起來玩遊戲吧。你想先做什麼呢？"


def test_split_sentences_by_punct():
    out = split_sentences(_REPLY)
    assert out == ["你好呀，我是企鵝。", "今天天氣真好。", "我們一起來玩遊戲吧。", "你想先做什麼呢？"]


def test_split_sentences_drops_empty():
    assert split_sentences("  。！  ？ ") == []
    assert split_sentences("") == []


@pytest.fixture(scope="module")
def voice():
    return load_zh_voice()


def test_synth_all_when_not_interrupted(voice):
    synth = InterruptibleSynth(voice)
    produced = list(synth.synth_sentences(split_sentences(_REPLY)))
    assert len(produced) == 4
    for _, audio in produced:
        assert np.asarray(audio.samples).size > 0


def test_callback_returns_nonzero_only_when_interrupted(voice):
    synth = InterruptibleSynth(voice)
    dummy = np.zeros(4, dtype=np.float32)
    assert synth._callback(dummy, 0.5) == 0
    synth.interrupt()
    assert synth._callback(dummy, 0.5) != 0


def test_stops_at_sentence_boundary_after_interrupt(voice):
    """第 1 句合成後 interrupt → 不再合成後續句（句界乾淨停）。"""
    synth = InterruptibleSynth(voice)
    sents = split_sentences(_REPLY)
    seen = []
    for idx, _audio in synth.synth_sentences(sents):
        seen.append(idx)
        if idx == 0:
            synth.interrupt()  # 模擬第 1 句播放中被打斷
    assert seen == [0]  # 只產出第 1 句，之後在句界 break
```

- [ ] **Step 3: 跑測試確認失敗**

Run：
```bash
spike/a2_pipecat/.venv/bin/python -m pytest spike/a2_pipecat/tests/test_interruptible_synth.py -v
```
Expected: FAIL，`ModuleNotFoundError: No module named 'spike.a2_pipecat.interruptible_synth'`（尚未實作）。

- [ ] **Step 4: 寫 interruptible_synth.py（最小實作轉綠）**

```python
# spike/a2_pipecat/interruptible_synth.py
"""engine-agnostic 可中止逐句合成核心：sherpa callback 中止 + 句界 break。

與 Pipecat 完全解耦，故可獨立單元測試。Pipecat TTS service（interruptible_tts.py）
只是把本類包一層。
"""
from __future__ import annotations

import re
import threading
from typing import Iterator

_SENT_SPLIT = re.compile(r"[^。！？!?]*[。！？!?]")


def split_sentences(text: str) -> list[str]:
    """按中英標點切句，去除首尾空白，丟棄純空白句。"""
    out = []
    for m in _SENT_SPLIT.findall(text or ""):
        s = m.strip()
        if s and re.sub(r"[。！？!?\s]", "", s):
            out.append(s)
    return out


class InterruptibleSynth:
    """逐句呼叫 sherpa OfflineTts.generate(text, callback)，被打斷時句界乾淨停止。"""

    def __init__(self, voice) -> None:
        self._voice = voice
        self._interrupted = threading.Event()

    def interrupt(self) -> None:
        self._interrupted.set()

    def reset(self) -> None:
        self._interrupted.clear()

    def is_interrupted(self) -> bool:
        return self._interrupted.is_set()

    def _callback(self, samples, progress) -> int:  # sherpa 契約：回非 0 即中止當前 generate
        return 1 if self._interrupted.is_set() else 0

    def synth_sentences(self, sentences) -> Iterator[tuple[int, object]]:
        for idx, sentence in enumerate(sentences):
            if self._interrupted.is_set():
                break  # 句界：不再開始下一句
            audio = self._voice.generate(sentence, callback=self._callback)
            yield idx, audio
            if self._interrupted.is_set():
                break
```

- [ ] **Step 5: 跑測試確認通過**

Run（需從 `talkybuddy/` 執行，讓 `spike.a2_pipecat.*` 匯入可解析）：
```bash
cd_guard=$(pwd); spike/a2_pipecat/.venv/bin/python -m pytest spike/a2_pipecat/tests/test_interruptible_synth.py -v
```
Expected: 5 個測試全 PASS。若 `test_stops_at_sentence_boundary_after_interrupt` 因「第 1 句 generate 內 callback 尚未觸發即完成」而仍產出 idx 0（預期會），關鍵斷言是 `seen == [0]`（不含 idx 1+），即句界 break 成立——此為判準 #4 的核心機制先在單元層驗過。

- [ ] **Step 6: Commit**

```bash
git add spike/a2_pipecat/sherpa_voice.py spike/a2_pipecat/interruptible_synth.py spike/a2_pipecat/tests/test_interruptible_synth.py
git commit -m "feat(a2-1 spike): interruptible sentence-wise sherpa synth core + TDD (unknown #1 verified)"
```

---

### Task 4: 把核心包成 Pipecat TTS service（依 probe 出的基類）

**Files:**
- Create: `spike/a2_pipecat/interruptible_tts.py`

**Interfaces:**
- Consumes: Task 2 probe 出的 TTS 基類名與須覆寫方法；Task 3 的 `InterruptibleSynth`、`split_sentences`、`sherpa_voice.load_zh_voice`。
- Produces: `SherpaInterruptibleTTSService`（Pipecat TTS service 子類，內部持有 `InterruptibleSynth`，收到 text frame → 逐句合成 → push audio frames；收到 interruption → 令 `InterruptibleSynth.interrupt()`）。

> 探查驅動：以下骨架基於 Pipecat 慣用 `TTSService.run_tts(self, text) -> AsyncGenerator[Frame]` 與 `TTSAudioRawFrame`。**務必用 Task 2 probe 的實際基類/方法/frame 名替換**，不可照抄未經 probe 確認的符號。

- [ ] **Step 1: 依 probe 結果寫 TTS service**

```python
# spike/a2_pipecat/interruptible_tts.py
"""把 InterruptibleSynth 包成 Pipecat TTS service。

⚠ 下列 import 與基類/方法/frame 名為『最可能骨架』；實作時以 probe_pipecat.py 的
輸出為準逐一替換（spec 未知 #2）。
"""
from __future__ import annotations

# --- 依 probe 替換 START ---
from pipecat.services.tts_service import TTSService          # 或 InterruptibleTTSService
from pipecat.frames.frames import (
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    StartInterruptionFrame,
)
# --- 依 probe 替換 END ---

import numpy as np

from spike.a2_pipecat.interruptible_synth import InterruptibleSynth, split_sentences
from spike.a2_pipecat.sherpa_voice import load_zh_voice


class SherpaInterruptibleTTSService(TTSService):
    """句級可中止 sherpa TTS。engine-agnostic 中止邏輯委派 InterruptibleSynth。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._synth = InterruptibleSynth(load_zh_voice())

    async def process_frame(self, frame, direction):
        # interruption frame 進來 → 令核心中止（frame 類名以 probe 為準）
        if isinstance(frame, StartInterruptionFrame):
            self._synth.interrupt()
        await super().process_frame(frame, direction)

    async def run_tts(self, text: str):
        """收到一段文字 → 逐句合成 → yield 音訊 frame；被打斷則句界停。"""
        self._synth.reset()
        yield TTSStartedFrame()
        for _idx, audio in self._synth.synth_sentences(split_sentences(text)):
            pcm = np.clip(np.rint(np.asarray(audio.samples) * 32767.0), -32768, 32767).astype(np.int16)
            yield TTSAudioRawFrame(
                audio=pcm.tobytes(),
                sample_rate=int(audio.sample_rate),
                num_channels=1,
            )
        yield TTSStoppedFrame()
```

- [ ] **Step 2: import 冒煙驗證（可被 pipeline 引用）**

Run：
```bash
spike/a2_pipecat/.venv/bin/python -c "from spike.a2_pipecat.interruptible_tts import SherpaInterruptibleTTSService; print('import OK', SherpaInterruptibleTTSService.__mro__[1].__name__)"
```
Expected: 印出 `import OK <基類名>`。若 import 炸（frame/基類名不符），回 Task 2 的 probe 輸出對照修正符號，直到 import 乾淨。**把最終正確的基類/方法/frame 名記進 `SPIKE-RESULT.md` 未知 #2。**

- [ ] **Step 3: Commit**

```bash
git add spike/a2_pipecat/interruptible_tts.py
git commit -m "feat(a2-1 spike): wrap interruptible synth as pipecat TTS service (unknown #2)"
```

---

### Task 5: StubLLM、InterruptDriver、OutputSink（三個 pipeline 小殼）

**Files:**
- Create: `spike/a2_pipecat/spike_parts.py`

**Interfaces:**
- Consumes: Task 2 probe 出的 Pipecat frame 類（transcription/text frame、interruption frame）與 `FrameProcessor` 基類。
- Produces:
  - `StubLLMService`：收到 STT transcription frame → push 固定多句中文 text frame（Global Constraints 的 `_REPLY`）。
  - `InterruptDriver`：pipeline 啟動後計時 `delay_s`（預設 2.0）→ push 一個 interruption frame，模擬使用者開口打斷（不接真 VAD）。
  - `OutputSink`：收到 TTS audio frame → 累計 byte 數並 log，不需真喇叭。

> 探查驅動：`FrameProcessor` / frame 類名以 Task 2 probe 為準替換。

- [ ] **Step 1: 依 probe 結果寫三個殼**

```python
# spike/a2_pipecat/spike_parts.py
"""spike 專用的三個最小 pipeline 元件。frame/基類名以 probe_pipecat.py 為準替換。"""
from __future__ import annotations

import asyncio

# --- 依 probe 替換 START ---
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.frames.frames import (
    TranscriptionFrame,
    TextFrame,
    TTSAudioRawFrame,
    StartInterruptionFrame,
)
# --- 依 probe 替換 END ---

_REPLY = "你好呀，我是企鵝。今天天氣真好。我們一起來玩遊戲吧。你想先做什麼呢？"


class StubLLMService(FrameProcessor):
    """把任何 STT 轉錄映成固定多句中文回覆（確保有句界可打斷）。"""

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            print(f"[StubLLM] got transcript: {getattr(frame, 'text', '')!r} -> fixed reply")
            await self.push_frame(TextFrame(_REPLY), FrameDirection.DOWNSTREAM)
        else:
            await self.push_frame(frame, direction)


class InterruptDriver(FrameProcessor):
    """啟動後計時 delay_s，push interruption frame 模擬 barge-in。"""

    def __init__(self, delay_s: float = 2.0, **kwargs):
        super().__init__(**kwargs)
        self._delay_s = delay_s
        self._task = None

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if self._task is None:
            self._task = asyncio.create_task(self._fire())
        await self.push_frame(frame, direction)

    async def _fire(self):
        await asyncio.sleep(self._delay_s)
        print(f"[InterruptDriver] firing interruption after {self._delay_s}s")
        await self.push_frame(StartInterruptionFrame(), FrameDirection.DOWNSTREAM)


class OutputSink(FrameProcessor):
    """吞掉 TTS 音訊 frame、累計 byte 數，不需真喇叭。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.total_bytes = 0
        self.frame_count = 0

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSAudioRawFrame):
            self.total_bytes += len(frame.audio)
            self.frame_count += 1
        await self.push_frame(frame, direction)
```

- [ ] **Step 2: import 冒煙驗證**

Run：
```bash
spike/a2_pipecat/.venv/bin/python -c "from spike.a2_pipecat.spike_parts import StubLLMService, InterruptDriver, OutputSink; print('parts OK')"
```
Expected: 印出 `parts OK`。frame/基類名不符則對照 probe 輸出修正。

- [ ] **Step 3: Commit**

```bash
git add spike/a2_pipecat/spike_parts.py
git commit -m "feat(a2-1 spike): add stub LLM / interrupt driver / output sink shells"
```

---

### Task 6: 組 pipeline 跑判準 #1~#3（install / 批次 STT 整段轉錄 / 多句逐句合成）

**Files:**
- Create: `spike/a2_pipecat/run_spike.py`

**Interfaces:**
- Consumes: Task 2 probe 出的 `Pipeline`/`PipelineTask`/`PipelineRunner` API 與 `FunASRSTTService` 匯入；Task 4 的 `SherpaInterruptibleTTSService`；Task 5 的三個殼；canned wav 路徑。
- Produces: 可執行的 `run_spike.py`，跑完印出判準 #1/#2/#3 的觀察結果（是否 crash、STT 轉出的文字、`OutputSink.frame_count`）。**Task 7 會加判準 #4。**

> 探查驅動：pipeline 組法與「如何把 wav 餵成 audio frames、如何框定一整段給 SegmentedSTTService」是 spec 未知 #3。骨架如下，餵段方式以 probe/實跑為準。

- [ ] **Step 1: 寫 run_spike.py（先只到判準 #3）**

```python
# spike/a2_pipecat/run_spike.py
"""組 Pipecat pipeline 跑 A2-1 spike 判準。

未知 #3：批次 SenseVoice（SegmentedSTTService）在 frame 拉取下如何吃『一整段』。
下方以 UserStartedSpeaking/StoppedSpeaking 手動框段為首選嘗試；若該版本 API 不同，
依 probe 輸出調整，並把實際可行的框段方式記進 SPIKE-RESULT.md。
"""
from __future__ import annotations

import asyncio
import wave
from pathlib import Path

# --- 依 probe 替換 START ---
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask
from pipecat.pipeline.runner import PipelineRunner
from pipecat.services.funasr.stt import FunASRSTTService
from pipecat.frames.frames import (
    InputAudioRawFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    EndFrame,
)
# --- 依 probe 替換 END ---

from spike.a2_pipecat.interruptible_tts import SherpaInterruptibleTTSService
from spike.a2_pipecat.spike_parts import StubLLMService, OutputSink

_TB_ROOT = Path(__file__).resolve().parents[2]
_WAV = _TB_ROOT / "models" / "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17" / "test_wavs" / "zh.wav"


def _load_wav_frames(chunk_ms: int = 200):
    with wave.open(str(_WAV), "rb") as wf:
        rate = wf.getframerate()
        ch = wf.getnchannels()
        pcm = wf.readframes(wf.getnframes())
    step = int(rate * chunk_ms / 1000) * 2 * ch
    for i in range(0, len(pcm), step):
        yield InputAudioRawFrame(audio=pcm[i:i + step], sample_rate=rate, num_channels=ch)


async def main():
    stt = FunASRSTTService()  # 預設 iic/SenseVoiceSmall；首次跑下載 ~1GB
    sink = OutputSink()
    pipeline = Pipeline([stt, StubLLMService(), SherpaInterruptibleTTSService(), sink])
    task = PipelineTask(pipeline)

    async def feed():
        # 手動框一整段：UserStartedSpeaking → audio frames → UserStoppedSpeaking
        await task.queue_frame(UserStartedSpeakingFrame())
        for f in _load_wav_frames():
            await task.queue_frame(f)
        await task.queue_frame(UserStoppedSpeakingFrame())
        await asyncio.sleep(8)  # 等 STT→LLM→TTS 走完
        await task.queue_frame(EndFrame())

    runner = PipelineRunner()
    await asyncio.gather(runner.run(task), feed())

    print("\n===== 判準觀察 =====")
    print(f"[#1 pipeline 未 crash] 走到這行即成立")
    print(f"[#3 TTS 逐句合成] OutputSink frames={sink.frame_count}, bytes={sink.total_bytes}")
    print("[#2 STT 整段轉錄] 見上方 [StubLLM] got transcript 那行的文字是否非空")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: 執行並觀察判準 #1/#2/#3**

Run：
```bash
spike/a2_pipecat/.venv/bin/python spike/a2_pipecat/run_spike.py 2>&1 | tee spike/a2_pipecat/_run_out.txt
```
Expected（首次含 ~1GB 下載，需時）：
- 判準 #1：程式跑到 `===== 判準觀察 =====` 不 crash。
- 判準 #2：出現 `[StubLLM] got transcript: '...'`，文字**非空**（證明 `FunASRSTTService` 把整段 wav 轉出文字）。
- 判準 #3：`OutputSink frames=` **> 0**（證明 sherpa 逐句合成有音訊 frame 流出）。

若 STT 收不到「一整段」（未知 #3）：嘗試 (a)調 `chunk_ms`；(b)確認框段 frame 名（probe）；(c)查 `FunASRSTTService` 是否需自帶 VAD 參數。把**實際可行的框段方式**記進 SPIKE-RESULT。

- [ ] **Step 3: Commit**

```bash
git add spike/a2_pipecat/run_spike.py
git commit -m "feat(a2-1 spike): run pipeline for criteria #1-3 (batch STT + multi-sentence synth)"
```

---

### Task 7: 判準 #4——程式化 barge-in 句界乾淨中止

**Files:**
- Modify: `spike/a2_pipecat/run_spike.py`

**Interfaces:**
- Consumes: Task 5 的 `InterruptDriver`；Task 6 的 pipeline。
- Produces: 把 `InterruptDriver` 接進 pipeline，跑出「第 1 句播放中注入 interruption → 後續句不再合成」的可觀察證據（中止時的 `OutputSink.frame_count` 明顯少於未中止基準）。

- [ ] **Step 1: 把 InterruptDriver 接進 pipeline 並記錄兩次基準**

在 `run_spike.py` 的 `main()` 加一個 `interrupt: bool` 參數與環境旗標，interrupt 模式時把 `InterruptDriver` 插在 TTS **之前**（讓 interruption frame 能傳到 TTS）：

```python
# run_spike.py 修改重點（替換 main 內 pipeline 組裝）
import os
from spike.a2_pipecat.spike_parts import InterruptDriver

async def main(interrupt: bool = False):
    stt = FunASRSTTService()
    sink = OutputSink()
    procs = [stt, StubLLMService()]
    if interrupt:
        procs.append(InterruptDriver(delay_s=2.0))  # 第 1 句開播後約 2s 打斷
    procs += [SherpaInterruptibleTTSService(), sink]
    pipeline = Pipeline(procs)
    task = PipelineTask(pipeline)
    # ...（feed / runner 同 Task 6）...
    print(f"[#4 interrupt={interrupt}] OutputSink frames={sink.frame_count}, bytes={sink.total_bytes}")

if __name__ == "__main__":
    asyncio.run(main(interrupt=os.environ.get("SPIKE_INTERRUPT") == "1"))
```

- [ ] **Step 2: 跑「未中止」基準**

Run：
```bash
spike/a2_pipecat/.venv/bin/python spike/a2_pipecat/run_spike.py 2>&1 | grep -E "#3|#4|frames="
```
Expected: 記下未中止的 `frames=`（4 句完整合成，數值較大）作為基準 N_full。

- [ ] **Step 3: 跑「中止」模式並比對**

Run：
```bash
SPIKE_INTERRUPT=1 spike/a2_pipecat/.venv/bin/python spike/a2_pipecat/run_spike.py 2>&1 | grep -E "InterruptDriver|#4|frames="
```
Expected:
- 出現 `[InterruptDriver] firing interruption after 2.0s`。
- 中止模式 `frames=` **明顯少於** N_full（後續句未合成 → 音訊 frame 變少），且程式在句界停住、不是等 4 句全合成完才停。**這即判準 #4 成立**（句界乾淨中止）。
- 若中止模式 `frames` 與 N_full 相同 → interruption frame 沒到 TTS 或 `process_frame` 攔截點不對：回 Task 4 確認 interruption frame 類名與攔截位置（probe），並在 SPIKE-RESULT 記錄「句內即停不成立、只能句間停」是否仍達判準（spec 風險段：句界停即 pass）。

- [ ] **Step 4: Commit**

```bash
git add spike/a2_pipecat/run_spike.py
git commit -m "feat(a2-1 spike): programmatic barge-in criterion #4 (clean sentence-boundary stop)"
```

---

### Task 8: 判準 #5 驗證 + 撰寫 SPIKE-RESULT.md（交付物）

**Files:**
- Create: `spike/a2_pipecat/SPIKE-RESULT.md`

**Interfaces:**
- Consumes: Task 2~7 的所有觀察與記錄。
- Produces: 最終交付物 `SPIKE-RESULT.md`（PASS/FAIL、三未知實測答案、workaround、銜接建議）。

- [ ] **Step 1: 判準 #5——證明全程未碰 `_process_text`**

Run：
```bash
grep -rn "_process_text" spike/a2_pipecat/ --include=*.py || echo "CLEAN: no _process_text reference"
grep -rn "from server" spike/a2_pipecat/ --include=*.py || echo "CLEAN: no server import"
grep -rn "import server" spike/a2_pipecat/ --include=*.py || echo "CLEAN: no server import"
```
Expected: 三行都印 `CLEAN: ...`（spike 未 import server、未觸及 `_process_text`）。任一有命中 → 修掉再重跑。

- [ ] **Step 2: 寫 SPIKE-RESULT.md**

依實跑結果填寫（以下為結構範本，方括號處填實測值，勿留方括號）：

```markdown
# A2-1 Pipecat 整合 Spike 結果

- 日期：2026-07-08
- 定位：拋棄式 go/no-go、二元淨判、不量延遲
- 環境：獨立 venv、Pipecat [版本]、sherpa-onnx [版本]、Python 3.12

## 結論：[PASS / FAIL]

| 判準 | 結果 | 證據 |
|---|---|---|
| #1 pipeline 不 crash | [PASS/FAIL] | [run_spike 走到判準觀察行] |
| #2 批次 STT 整段轉錄 | [PASS/FAIL] | transcript=[實際文字] |
| #3 多句逐句合成 | [PASS/FAIL] | OutputSink frames=[N_full] |
| #4 句界乾淨中止 | [PASS/FAIL] | 中止 frames=[N_int] < N_full=[N_full] |
| #5 未碰 _process_text | [PASS/FAIL] | grep CLEAN |

## 三個未知的實測答案

1. **sherpa callback 中止語意**：[已一手驗證：generate(text, callback)，callback(samples, progress)->int，回非 0 中止；spike venv 覆核版本＝___、行為＝___]
2. **Pipecat interruption 機制**：TTS 實際 subclass＝[基類名]，覆寫方法＝[方法名]，interruption frame＝[frame 類名]，攔截點＝[process_frame/其他]。
3. **批次 STT 在 frame 拉取下**：FunASRSTTService＝[SegmentedSTTService?]，可行框段方式＝[UserStarted/StoppedSpeaking / 其他]，是否乾淨吃一整段＝[是/否＋說明]。

## workaround / 卡點
[任何為了讓判準通過而做的偏離，或撞牆點與是否有解]

## 若留用的一句話銜接
STT：把 native FunASRSTTService 換成包 server/asr.py 的 service 子類（同 SegmentedSTTService 介面）。
TTS：SherpaInterruptibleTTSService 的 InterruptibleSynth 中止介面即 StreamingTurnManager（A2-2）在 _process_text 之外組出 TurnResult 後、驅動句級中止所需的核心；barge-in 由 Silero VAD 取代本 spike 的 InterruptDriver。
```

- [ ] **Step 3: Commit**

```bash
git add spike/a2_pipecat/SPIKE-RESULT.md
git commit -m "docs(a2-1 spike): SPIKE-RESULT go/no-go verdict + three unknowns + handoff"
```

---

## Self-Review

**1. Spec coverage：**
- go/no-go 單一問句 → Task 6+7（判準 #1~#4）＋Task 8（#5）。✅
- 非目標（不接真麥/AEC/VAD、不量延遲、不接真 LLM、不改 server）→ Global Constraints ＋各 task（stub LLM、InterruptDriver 取代 VAD、grep 判準 #5）。✅
- 元件與資料流（FunASRSTTService／StubLLMService／SherpaInterruptibleTTSService／InterruptDriver／OutputSink）→ Task 4/5/6 逐一對應。✅
- 三個待發現未知 → Task 2 probe ＋ Task 3（#1 已驗）／Task 4（#2）／Task 6（#3），並在 Task 8 SPIKE-RESULT 記錄。✅
- Step 0 環境（獨立 venv、記錄版本、裝不起來＝no-go）→ Task 1。✅
- 落點 `spike/a2_pipecat/`、複製 tts.py 最小片段不 import server → File Structure ＋ Task 3 sherpa_voice.py。✅
- 交付物 SPIKE-RESULT.md → Task 8。✅
- 風險止血（Pipecat 裝不起來、句內中止無效但句界可停、FunASR 下載過重）→ Task 1 Step 3、Task 7 Step 3、Task 6 Step 2 均有處置。✅

**2. Placeholder scan：** 已知項（sherpa callback、路徑、canned wav、判準、grep）均給精確 code/命令；Pipecat 整合層的「依 probe 替換」區塊是 spec 明訂的待發現未知，plan 提供可執行探查步驟＋二元觀察判準＋記錄去向，非 TODO。無「TBD/實作細節later/類似 Task N」等紅旗。✅

**3. Type consistency：** `InterruptibleSynth`（`interrupt`/`reset`/`is_interrupted`/`_callback`/`synth_sentences`）、`split_sentences`、`load_zh_voice`、`SherpaInterruptibleTTSService`、`StubLLMService`/`InterruptDriver`/`OutputSink` 在 Task 3→4→5→6→7 引用一致；`_REPLY` 4 句在測試、stub、Global Constraints 三處逐字相同。✅

---

## Execution Handoff

Plan 已存至 `talkybuddy/docs/superpowers/plans/2026-07-08-a2-1-pipecat-spike.md`。
