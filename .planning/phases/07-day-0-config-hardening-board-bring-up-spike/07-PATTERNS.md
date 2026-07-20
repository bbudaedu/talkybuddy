# Phase 7: Day-0 Config Hardening & Board Bring-Up Spike - Pattern Map

**Mapped:** 2026-07-19
**Files analyzed:** 8（2 修改 + 6 新增/骨架）
**Analogs found:** 6 / 8（2 為純骨架 README，無程式碼分析對象）

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `server/llm.py`（`_get_model()` ~L72-102） | service | CRUD（設定讀取→物件建構） | `server/config.py:132-139`（`PIPELINE_PROFILE` 讀取與 `default_network_mode()`） | role-match（config 消費模式，非同檔案角色） |
| `server/config.py`（新增 `LLM_N_CTX` 常數） | config | request-response（無，純常數） | `server/config.py:104-129`（既有 env 覆寫常數群，如 `CLOUD_TTS_TIMEOUT_S`） | exact |
| `server/pipeline.py`（`_webm_to_wav()` 加 RIFF-sniff，~L57-108） | utility | transform（bytes→wav 路徑，含 fast/fallback 雙路徑） | `server/asr_sensevoice.py:19-23`（`_read_wav`，soundfile 直讀） | role-match（同為音訊 I/O transform，資料流相同） |
| `edge/deploy/*.sh` | utility（部署腳本） | batch（build→push→run） | `scripts/setup_env.sh` | role-match（同為 bash 部署腳本，非同資料流但同角色） |
| `edge/runtime/run_edge.sh` | config/launcher | request-response（啟動 server process） | `scripts/run.sh` | exact |
| `edge/models/README.md` | config（文件） | — | 無程式碼分析對象（純文件骨架） | N/A |
| `docs/DEPLOY_EDGE.md` | config（文件） | — | `docs/DEPLOY_CLOUD.md`（全文） | exact |
| `tests/test_llm_n_ctx_profile.py`（新增，建議命名） | test | CRUD | `tests/test_pipeline_profile.py`（全文） | exact |

## Pattern Assignments

### `server/config.py`（新增 `LLM_N_CTX`）

**Analog：** `server/config.py:104-139`（既有 env 覆寫常數 + profile 常數）

**既有 profile 常數**（L131-134）：
```python
# ---------------------------------------------------------------------------
# 佈署 profile：edge（玩偶本地）/ cloud（瀏覽器終端，AI 在雲端）
# ---------------------------------------------------------------------------
PIPELINE_PROFILE: str = os.environ.get("TALKYBUDDY_PIPELINE_PROFILE", "edge")
```

**既有 env-覆寫數值常數慣例**（L111-115，可直接套用同款式定義 `LLM_N_CTX`）：
```python
CLOUD_TTS_TIMEOUT_S: float = float(os.environ.get("CLOUD_TTS_TIMEOUT_S", "6.0"))
PRON_SCORE_TIMEOUT_S: float = float(os.environ.get("PRON_SCORE_TIMEOUT_S", "15.0"))
```

**建議新增位置：** 緊接在 `PIPELINE_PROFILE` 定義之後（L134 後），因為 D-10 明確要求 n_ctx 依 `PIPELINE_PROFILE` 決定預設值，同時允許 env 覆寫（雙層預設）：
```python
# LLM_N_CTX：依 profile 決定 llama.cpp context 視窗，避免 edge CPU OOM
# （見 CONCERNS.md：server/llm.py 舊硬編 n_ctx=1024）。
# edge 預設 512、cloud/PC 維持 1024；TALKYBUDDY_LLM_N_CTX 可強制覆寫。
_LLM_N_CTX_DEFAULT = 512 if PIPELINE_PROFILE == "edge" else 1024
LLM_N_CTX: int = int(os.environ.get("TALKYBUDDY_LLM_N_CTX", str(_LLM_N_CTX_DEFAULT)))
```
注意：`PIPELINE_PROFILE` 必須先定義才能引用；`_LLM_N_CTX_DEFAULT` 命名與底線前綴不影響 `from server import config` 的既有匯入慣例（其餘皆用大寫常數，無底線私有慣例衝突）。

---

### `server/llm.py`（`_get_model()`，改用 `config.LLM_N_CTX`）

**Analog：** 同檔案內既有 `_get_gguf_path()` 的 lazy-import 保護模式（L28-34）＋ `_get_model()` 本體（L72-102）

**目前硬編位置**（L90-97，待修改）：
```python
EdgeLLM._model = Llama(
    model_path=str(gguf),
    # PC 原型記憶體充足，維持 1024；PLAN.md 要求 Genio 520 板上
    # 移植時應降為 512 tokens，避免 CPU 端 LLM context 過大導致 OOM 崩潰。
    n_ctx=1024,
    n_threads=4,
    verbose=False,
)
```

**lazy import 保護慣例**（L28-34，讀 config 要包 try/except，避免 config 尚未就緒時炸掉降級路徑）：
```python
def _get_gguf_path():
    """取得 GGUF 模型路徑；config 尚未就緒時回 None（lazy import 保護）。"""
    try:
        from server import config
        return config.LLM_GGUF
    except Exception:
        return None
```

**建議改法：** 在 `_get_model()` 內既有 `from server import config` 呼叫處（用於 `_get_gguf_path`）一併取 `config.LLM_N_CTX`，`n_ctx=1024` 改成 `n_ctx=config.LLM_N_CTX`，並更新既有註解（不再提「PLAN.md 要求」，改記錄已完成 profile 化）。`_get_model()` 已在 `_get_gguf_path()` 內部有 `from server import config` 的 try/except 包裹模式，同一函式體可重複沿用同一 import。

**契約備註：** `EdgeLLM._model` 為類別層級單例（L41），一旦載入不會因 `LLM_N_CTX` 之後變動而重建；與既有『importlib.reload(config)』測試模式一致（見 `tests/test_pipeline_profile.py`），測試需在載入模型前 reload config 或直接注入常數。

---

### `server/pipeline.py`（`_webm_to_wav()` 加 RIFF-sniff fast path）

**Analog：** `server/asr_sensevoice.py:19-23`（`_read_wav`，抽成 module 函式便於測試 monkeypatch 的慣例）＋ 既有 `_webm_to_wav` 本體的 fallback 結構

**soundfile 直讀慣例**（`server/asr_sensevoice.py:19-23`）：
```python
def _read_wav(path: str):
    """讀 wav 為 (samples float32 ndarray, sample_rate)。抽成 module 函式以利測試 monkeypatch。"""
    import soundfile as sf
    samples, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    return samples, sample_rate
```

**既有 `_webm_to_wav` 簽名與呼叫慣例**（`server/pipeline.py:57-108`，`await asyncio.to_thread(_webm_to_wav, webm_bytes)` 呼叫見 L159）：
```python
def _webm_to_wav(webm_bytes: bytes) -> str | None:
    """...失敗（ffmpeg 不存在 / 轉檔錯誤 / 逾時）回 None，由呼叫端走兜底路徑。
    此函式為同步阻塞，呼叫端應以 asyncio.to_thread 執行。
    """
    webm_path = None
    wav_path = None
    try:
        ...
        proc = subprocess.run([...], capture_output=True, timeout=FFMPEG_TIMEOUT_S)
        if proc.returncode != 0 or not os.path.getsize(wav_path):
            raise RuntimeError(...)
        return wav_path
    except Exception:
        if wav_path:
            try:
                os.unlink(wav_path)
            except OSError:
                pass
        return None
    finally:
        if webm_path:
            try:
                os.unlink(webm_path)
            except OSError:
                pass
```

**建議新增：** 在 `_webm_to_wav` 函式最前面（或新增 `_is_wav_riff(header: bytes) -> bool` helper）做前 12 bytes RIFF/WAVE magic 偵測；命中且 16k mono → soundfile 直讀寫暫存 wav 路徑並回傳（維持既有回傳型別 `str | None`，呼叫端 `pipeline.py:159` 不需改動）；規格不符（非 16k mono）時依 D-09：PC 有 ffmpeg → 走既有 fallback subprocess 分支；edge 環境（可用 `config.PIPELINE_PROFILE == "edge"` 或偵測 ffmpeg 不存在）→ 明確 raise，不吞例外偽裝成功。維持既有 try/except/finally 暫存檔清理結構（webm_path 需視輸入是否已是 wav bytes 決定是否建立）。

**函式名可能需要調整：** 若輸入不再保證是 webm，函式改名或保留 `_webm_to_wav` 但擴充 docstring 說明「亦接受原生 WAV bytes」——由 executor 決定，需與 L159 呼叫處變數命名一致性檢查。

---

### `edge/runtime/run_edge.sh`

**Analog：** `scripts/run.sh`（全文）
```bash
#!/usr/bin/env bash
# 啟動 TalkyBuddy 伺服器（從 repo 根目錄以 venv python 執行 uvicorn）
set -euo pipefail

cd /home/budaedu/hackathon/talkybuddy
exec .venv/bin/python -m uvicorn server.app:app --host 0.0.0.0 --port 8787
```

**套用要點（D-01/D-02/D-05）：**
- 沿用 `set -euo pipefail` + `exec uvicorn` 的簡潔慣例。
- 新增 `TALKYBUDDY_PIPELINE_PROFILE=edge` 環境變數注入（對照 `docs/DEPLOY_CLOUD.md` §2 的 `TALKYBUDDY_PIPELINE_PROFILE=cloud` 啟動慣例）。
- 路徑改為 proot-distro Debian 內的相對／絕對路徑（不可硬編 `/home/budaedu/hackathon/talkybuddy`，需由 executor 依實際 push 目標路徑決定，或改用 `cd "$(dirname "$0")/.."` 之類的相對定位）。
- 只針對 Android 14 proot 這一條路徑，不做 dual-host 抽象（YAGNI，D-02）。

---

### `edge/deploy/*.sh`（build → push → run）

**Analog：** `scripts/setup_env.sh`（開頭風格 + 分階段 echo 慣例）
```bash
#!/usr/bin/env bash
# 說說學伴 PC 原型 — 環境安裝與模型下載（Phase 0：x86 軟體先行）
set -uo pipefail
cd /home/budaedu/hackathon/talkybuddy
...
echo "=== [1/4] 建立 venv 並安裝基礎套件 ==="
```

**套用要點（D-06）：** `edge/deploy` 腳本需可執行（非空殼），採同款「分階段 echo + set -euo pipefail」慣例，例如 `edge/deploy/build.sh`／`push.sh`／`run.sh`（或合一 `deploy.sh` 內部分階段），對應 adb push server 目錄 + `edge/runtime/run_edge.sh` 到裝置 proot rootfs 後執行。腳本應假設在 repo 根目錄執行（沿用 `cd` 慣例）。

---

### `docs/DEPLOY_EDGE.md`

**Analog：** `docs/DEPLOY_CLOUD.md`（全文，結構範本）

**章節結構慣例（需對稱套用）：**
1. 標題 + 一段情境說明（`docs/DEPLOY_CLOUD.md:1-7`）
2. 環境變數表格（`docs/DEPLOY_CLOUD.md:9-18`，含 `| 變數 | 用途 | 範例／預設 |` 表頭）
3. 啟動指令區塊（`docs/DEPLOY_CLOUD.md:25-33`，bash code block + `TALKYBUDDY_PIPELINE_PROFILE=xxx` 前綴）
4. 額外章節（cloud 版是 TLS/WSS 與帳號種子；edge 版應換成 adb 部署流程、proot-distro provisioning、health check 驗證步驟）

**建議 edge 版對應章節：**
- §1 環境變數：`TALKYBUDDY_PIPELINE_PROFILE=edge`（對照 cloud 版表格格式）
- §2 啟動指令：呼叫 `edge/runtime/run_edge.sh`（而非直接 uvicorn 指令，因需先進 proot）
- §3 adb 部署迴圈：對應 `edge/deploy/` 腳本用法
- §4 驗證：D-03 範圍——只驗 server 起來 + health check（不含完整聲音迴路）

---

### `tests/test_llm_n_ctx_profile.py`（建議新增測試檔）

**Analog：** `tests/test_pipeline_profile.py`（全文）
```python
# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib

from server import config


def test_default_profile_is_edge(monkeypatch):
    monkeypatch.delenv("TALKYBUDDY_PIPELINE_PROFILE", raising=False)
    importlib.reload(config)
    assert config.PIPELINE_PROFILE == "edge"
    assert config.default_network_mode() == "edge"


def test_cloud_profile_sets_cloud_mode(monkeypatch):
    monkeypatch.setenv("TALKYBUDDY_PIPELINE_PROFILE", "cloud")
    importlib.reload(config)
    assert config.PIPELINE_PROFILE == "cloud"
    assert config.default_network_mode() == "cloud"
    monkeypatch.delenv("TALKYBUDDY_PIPELINE_PROFILE", raising=False)
    importlib.reload(config)
```

**套用要點：** 用 `monkeypatch.setenv/delenv` + `importlib.reload(config)` 驗證 `LLM_N_CTX` 在 `edge`（512）/`cloud`（1024）/明確 `TALKYBUDDY_LLM_N_CTX` 覆寫三種情境下的值；務必在測試結尾 reload 回預設狀態（同既有慣例最後一行 `importlib.reload(config)` 清理，避免污染其他測試模組層級 import 的 `config` 值）。

**RIFF-sniff 測試** 可另建 `tests/test_pipeline_wav_fastpath.py`，比照 `tests/test_pipeline.py` 的 Stub 注入慣例（`StubASR`/`StubLLM`/`StubTTS`，L28-59）驗證：WAV bytes 命中 fast path 不呼叫 subprocess、非 WAV 走原 ffmpeg 路徑、WAV 但非 16k mono 在 edge profile 下明確拋錯。

## Shared Patterns

### Lazy import + try/except 保護（config 消費）
**Source:** `server/llm.py:28-34`（`_get_gguf_path`）
**Apply to:** `server/config.py` 新增常數的所有消費點（`server/llm.py` 的 `_get_model`）— 確保 import 期不炸、降級路徑不受影響。
```python
def _get_gguf_path():
    try:
        from server import config
        return config.LLM_GGUF
    except Exception:
        return None
```

### env 覆寫數值常數慣例
**Source:** `server/config.py:104-129`
**Apply to:** `LLM_N_CTX` 新增定義
```python
CLOUD_TTS_TIMEOUT_S: float = float(os.environ.get("CLOUD_TTS_TIMEOUT_S", "6.0"))
```

### bash 部署腳本慣例（`set -euo pipefail` + 絕對路徑 cd + exec）
**Source:** `scripts/run.sh`, `scripts/setup_env.sh`
**Apply to:** `edge/runtime/run_edge.sh`, `edge/deploy/*.sh`

### 文件對稱範本（環境變數表 + 啟動指令 code block）
**Source:** `docs/DEPLOY_CLOUD.md`
**Apply to:** `docs/DEPLOY_EDGE.md`

### 不靜默偽成功原則（D-09 呼應 NPU-02）
**Source:** `server/pipeline.py:_webm_to_wav` 既有 except-then-None 降級慣例，但 D-09 要求 edge 端規格不符時**明確 raise**（例外）
**Apply to:** RIFF-sniff fast path 的取樣率/聲道檢查分支——edge 無 ffmpeg 時不可回退為靜默失敗（None），需拋出可辨識例外供上層記錄。

## No Analog Found

| File | Role | Data Flow | Reason |
|---|---|---|---|
| `edge/models/README.md` | config（文件） | — | 純文件 placeholder，無對應程式碼分析對象；內容依 D-04 直接描述「未來放 INT8 tflite / GGUF」即可，無需抓既有程式碼片段。 |

## Metadata

**Analog search scope:** `server/`（config.py, llm.py, pipeline.py, asr_sensevoice.py）、`scripts/`（run.sh, setup_env.sh）、`docs/`（DEPLOY_CLOUD.md）、`tests/`（test_pipeline_profile.py, test_llm.py, test_pipeline.py）
**Files scanned:** 10
**Pattern extraction date:** 2026-07-19
