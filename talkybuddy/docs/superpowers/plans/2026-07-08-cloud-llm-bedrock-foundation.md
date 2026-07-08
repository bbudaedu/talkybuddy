# cloud_llm Bedrock FM 地基（子專案 A）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 線上場景（`network_mode == "cloud"`）時，陪聊 Agent 與導師 Agent 的雲端推理都經 Amazon Bedrock Converse API，滿足決賽「大腦 100% 在 Bedrock」合規，斷網/逾時無縫降級回本地。

**Architecture:** 新增薄 provider `server/cloud_llm.py`（boto3 Bedrock Converse），對外契約與 `EdgeLLM.generate` 完全一致（drop-in）。陪聊：`pipeline._process_text` 依 `network_mode` 先試雲端、None 降級回本地 `EdgeLLM` → `scaffold`。導師：`diagnose.generate_diagnosis` 依 `LLM_CLOUD_PROVIDER` 路由 bedrock→anthropic→mock。全程可回退，與 A/B 軸其他部分正交。

**Tech Stack:** Python 3.12、boto3（Bedrock Converse）、pytest、既有 `server/guardrails.py`（deidentify/passes_guardrail/consent_granted 已存在）。

## Global Constraints

- **三鐵律**：不引 Hermes/LangGraph/AutoGen；一切走 `network_mode == "cloud"` 分支；唯一交界 `pipeline._process_text`，不碰 A 軸音訊/串流/離線 `run_turn_audio`。
- **韌性契約**：所有新行為僅在 `LLM_CLOUD_PROVIDER != "off"` 且 provider 可用時啟動；任何 None/例外/逾時/斷網 → 與今天完全相同的降級。`network_mode == "edge"` 行為零改動。
- **契約一致**：`CloudLLM.generate(student_text, scaffold, directive=None) -> str | None`，逾時上限 8.0s、輸出必過 `guardrails.passes_guardrail`、帶讀 target 未出現則補「跟我說一遍：<target>」句——**逐字對齊 `EdgeLLM.generate`（`server/llm.py:104-161`）**。
- **測試不打真 Bedrock**：boto3 client 一律以 monkeypatch 注入 fake，斷言 fake 收到的參數。
- **provider flag 語義**：`LLM_CLOUD_PROVIDER ∈ {"bedrock","anthropic","off"}`。陪聊雲端只在 `"bedrock"` 時啟用（`anthropic`/`off` → 陪聊走本地 EdgeLLM）；導師三值都路由（bedrock→anthropic→mock）。
- **既有測試基準**：實作前 `119 passed`，每次 commit 後全套須維持綠。
- 所有使用者可見文字用繁體中文（台灣用語）。

---

## File Structure

- **新增 `server/cloud_llm.py`**：Bedrock Converse provider。`CloudLLM` 類（`available`/`generate`＝陪聊 drop-in）＋ 模組級 `diagnose_via_bedrock`（導師結構化 JSON）＋ 內部 `_get_client`/`_extract_text`/`_extract_tool_input`。
- **改 `server/config.py`**：4 個 config 常數。
- **改 `server/pipeline.py`**：`VoicePipeline.__init__` 加 `cloud_llm=None`；`_process_text` 陪聊分支。
- **改 `server/diagnose.py`**：抽 `_build_diagnosis_prompt`/`_DIAGNOSIS_SCHEMA` 共用；`generate_diagnosis` 加 provider 路由。
- **改 `server/app.py`**：建 `CloudLLM` 注入 `VoicePipeline`。
- **新增 `tests/test_cloud_llm.py`、`tests/test_pipeline_cloud.py`**。

---

## Task 1: cloud_llm 陪聊 provider（`CloudLLM.generate`）+ config 常數

**Files:**
- Create: `talkybuddy/server/cloud_llm.py`
- Modify: `talkybuddy/server/config.py`（檔尾新增常數）
- Test: `talkybuddy/tests/test_cloud_llm.py`

**Interfaces:**
- Consumes: `server.guardrails.deidentify(str)->str`、`server.guardrails.passes_guardrail(str)->bool`、`server.llm.EdgeLLM._SYSTEM_PROMPT`（str 常數，直接 import 重用，不重構 llm.py）。
- Produces:
  - `config.LLM_CLOUD_PROVIDER: str`、`config.BEDROCK_REGION: str`、`config.COMPANION_MODEL_ID: str`、`config.TUTOR_MODEL_ID: str`
  - `cloud_llm.CloudLLM().available() -> bool`
  - `cloud_llm.CloudLLM().generate(student_text: str, scaffold, directive: str | None = None) -> str | None`
  - `cloud_llm._get_client() -> Any | None`（測試 monkeypatch 目標）

- [ ] **Step 1: 先加 config 常數**

在 `talkybuddy/server/config.py` 檔尾（`WAKE_SENSITIVITY` 之後）新增：

```python

# ---------------------------------------------------------------------------
# 雲端 LLM 大腦（子專案 A）：network_mode=cloud 時，陪聊/導師改經 Bedrock Converse。
# provider ∈ {"bedrock","anthropic","off"}；憑證走 boto3 credential chain，不寫進 repo。
# ---------------------------------------------------------------------------
LLM_CLOUD_PROVIDER: str = os.environ.get("LLM_CLOUD_PROVIDER", "bedrock")
BEDROCK_REGION: str = os.environ.get("BEDROCK_REGION", "us-east-1")
# 陪聊模型（便宜快的 Nova Lite 起步；換 Claude Haiku 僅需改此值或環境變數）。
COMPANION_MODEL_ID: str = os.environ.get("COMPANION_MODEL_ID", "amazon.nova-lite-v1:0")
# 導師模型（結構化診斷；量小、每 5 輪一次，可用較強模型）。
TUTOR_MODEL_ID: str = os.environ.get("TUTOR_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
```

（註：model id 為 Bedrock 慣用格式範例，實機以 AWS console「Model access」頁實際可用 id 為準；不影響本計畫測試，因測試 mock client。）

- [ ] **Step 2: 寫失敗測試 `tests/test_cloud_llm.py`（generate 快樂路徑 + 降級）**

```python
# -*- coding: utf-8 -*-
"""CloudLLM（Bedrock Converse 陪聊 provider）單元測試；boto3 全 mock。"""
import types
import pytest
from server import cloud_llm, config


class _FakeScaffold:
    def __init__(self, target):
        self.target_sentence = target


def _fake_converse_response(text):
    return {"output": {"message": {"content": [{"text": text}]}}}


class _FakeClient:
    def __init__(self, response=None, raises=None):
        self._response = response
        self._raises = raises
        self.last_kwargs = None

    def converse(self, **kwargs):
        self.last_kwargs = kwargs
        if self._raises is not None:
            raise self._raises
        return self._response


@pytest.fixture(autouse=True)
def _force_bedrock(monkeypatch):
    monkeypatch.setattr(config, "LLM_CLOUD_PROVIDER", "bedrock", raising=False)
    monkeypatch.setattr(config, "COMPANION_MODEL_ID", "test-model", raising=False)


def test_generate_returns_filtered_text(monkeypatch):
    fake = _FakeClient(_fake_converse_response("你好棒！跟我說一遍：I see a cat"))
    monkeypatch.setattr(cloud_llm, "_get_client", lambda: fake)
    out = cloud_llm.CloudLLM().generate("我看到貓", _FakeScaffold("I see a cat"))
    assert out == "你好棒！跟我說一遍：I see a cat"
    # 送出的 modelId 來自 config、且學生文字有進 prompt
    assert fake.last_kwargs["modelId"] == "test-model"


def test_generate_appends_target_when_missing(monkeypatch):
    fake = _FakeClient(_fake_converse_response("你今天很棒喔！"))
    monkeypatch.setattr(cloud_llm, "_get_client", lambda: fake)
    out = cloud_llm.CloudLLM().generate("嗨", _FakeScaffold("I see a dog"))
    assert "跟我說一遍：I see a dog" in out


def test_generate_none_on_exception(monkeypatch):
    fake = _FakeClient(raises=RuntimeError("boom"))
    monkeypatch.setattr(cloud_llm, "_get_client", lambda: fake)
    assert cloud_llm.CloudLLM().generate("嗨", _FakeScaffold("hi")) is None


def test_generate_none_when_guardrail_blocks(monkeypatch):
    fake = _FakeClient(_fake_converse_response("我們來聊暴力和血腥的東西"))
    monkeypatch.setattr(cloud_llm, "_get_client", lambda: fake)
    assert cloud_llm.CloudLLM().generate("嗨", _FakeScaffold("hi")) is None


def test_generate_deidentifies_student_text(monkeypatch):
    fake = _FakeClient(_fake_converse_response("很好！"))
    monkeypatch.setattr(cloud_llm, "_get_client", lambda: fake)
    cloud_llm.CloudLLM().generate("我的電話號碼是1234", _FakeScaffold("hi"))
    sent = fake.last_kwargs["messages"][0]["content"][0]["text"]
    assert "電話號碼" not in sent or "1234" not in sent  # deidentify 已遮罩


def test_available_false_when_provider_off(monkeypatch):
    monkeypatch.setattr(config, "LLM_CLOUD_PROVIDER", "off", raising=False)
    monkeypatch.setattr(cloud_llm, "_get_client", lambda: object())
    assert cloud_llm.CloudLLM().available() is False


def test_available_false_when_no_client(monkeypatch):
    monkeypatch.setattr(cloud_llm, "_get_client", lambda: None)
    assert cloud_llm.CloudLLM().available() is False


def test_available_true_when_bedrock_and_client(monkeypatch):
    monkeypatch.setattr(cloud_llm, "_get_client", lambda: object())
    assert cloud_llm.CloudLLM().available() is True
```

- [ ] **Step 3: 執行測試確認失敗**

Run: `cd talkybuddy && .venv/bin/pytest tests/test_cloud_llm.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'server.cloud_llm'`）

- [ ] **Step 4: 實作 `server/cloud_llm.py`（先只做 CloudLLM）**

```python
# -*- coding: utf-8 -*-
"""cloud_llm：雲端 LLM 大腦（Bedrock Converse, boto3）。

契約刻意對齊 server.llm.EdgeLLM：
- ``CloudLLM.available() -> bool``：provider=="bedrock" 且 boto3 client 可建。
- ``CloudLLM.generate(student_text, scaffold, directive=None) -> str | None``：
  逾時（>8s）、例外、空輸出、命中 guardrail 一律回 None → pipeline 降級回本地。

重依賴（boto3）lazy import；import 本模組絕不可炸（無 boto3 時仍要能啟動）。
"""
from __future__ import annotations

import logging
import time

from server import guardrails

_log = logging.getLogger(__name__)

_GENERATE_TIMEOUT_S = 8.0


def _cfg():
    from server import config
    return config


def _system_prompt() -> str:
    """重用 EdgeLLM 的鷹架 system prompt，避免兩份漂移（不重構 llm.py）。"""
    from server.llm import EdgeLLM
    return EdgeLLM._SYSTEM_PROMPT


def _get_client():
    """lazy 建 bedrock-runtime client；boto3 缺失/憑證問題一律回 None。"""
    try:
        import boto3  # lazy 重依賴
        cfg = _cfg()
        return boto3.client("bedrock-runtime", region_name=cfg.BEDROCK_REGION)
    except Exception:
        return None


def _extract_text(resp) -> str:
    """從 Converse 回應取第一個 text block；結構不符回空字串。"""
    try:
        for block in resp["output"]["message"]["content"]:
            if "text" in block:
                return block["text"] or ""
    except Exception:
        pass
    return ""


class CloudLLM:
    """Bedrock Converse 陪聊 provider；失敗一律優雅降級（回 None）。"""

    def available(self) -> bool:
        cfg = _cfg()
        if getattr(cfg, "LLM_CLOUD_PROVIDER", "off") != "bedrock":
            return False
        return _get_client() is not None

    def generate(self, student_text, scaffold, directive=None):
        start = time.monotonic()
        try:
            client = _get_client()
            if client is None:
                return None
            cfg = _cfg()
            target = getattr(scaffold, "target_sentence", None)
            safe_student = guardrails.deidentify(str(student_text))
            directive_block = (
                f"\n{directive.strip()}\n" if directive and directive.strip() else ""
            )
            user_prompt = (
                f"學生剛剛說：「{safe_student}」\n"
                f"目標英文句：{target or ''}\n"
                f"{directive_block}"
                "請照規則回覆：先一句繁體中文稱讚鼓勵，"
                "再用「跟我說一遍：<英文句>」帶讀目標英文句。"
            )
            resp = client.converse(
                modelId=cfg.COMPANION_MODEL_ID,
                system=[{"text": _system_prompt()}],
                messages=[{"role": "user", "content": [{"text": user_prompt}]}],
                inferenceConfig={"maxTokens": 300, "temperature": 0.7},
            )
            if time.monotonic() - start > _GENERATE_TIMEOUT_S:
                return None
            text = _extract_text(resp).strip()
            if not text:
                return None
            if not guardrails.passes_guardrail(text):
                return None
            if target and target not in text:
                text = f"{text} 跟我說一遍：{target}"
            return text
        except Exception:
            _log.exception("CloudLLM.generate 失敗，降級回 scaffold/本地")
            return None
```

- [ ] **Step 5: 執行測試確認通過**

Run: `cd talkybuddy && .venv/bin/pytest tests/test_cloud_llm.py -q`
Expected: PASS（8 passed）

- [ ] **Step 6: 全套回歸**

Run: `cd talkybuddy && .venv/bin/pytest -q`
Expected: PASS（既有全綠 + 新 8 = 127 passed 附近；數字以實跑為準）

- [ ] **Step 7: Commit**

```bash
cd talkybuddy && git add server/cloud_llm.py server/config.py tests/test_cloud_llm.py
git commit -m "feat(A): cloud_llm CloudLLM 陪聊 Bedrock Converse provider + config

與 EdgeLLM.generate 同契約 drop-in；deidentify 上雲、passes_guardrail 過濾、
帶讀補句、逾時/例外降級。boto3 全 mock 測試。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: 陪聊接線 pipeline（cloud → edge → scaffold 降級鏈）

**Files:**
- Modify: `talkybuddy/server/pipeline.py`（`VoicePipeline.__init__` 約 `:114`；`_process_text` LLM 區塊 `:209-225`）
- Test: `talkybuddy/tests/test_pipeline_cloud.py`

**Interfaces:**
- Consumes: `cloud_llm.CloudLLM().generate(...)`（Task 1）、既有 `self.llm.generate(...)`、`self.network_mode`。
- Produces: `VoicePipeline.__init__(self, asr, llm, tts, cloud_llm=None)`（新增選填參數，預設 None → 向後相容）；`self.cloud_llm` 屬性。

- [ ] **Step 1: 寫失敗測試 `tests/test_pipeline_cloud.py`**

```python
# -*- coding: utf-8 -*-
"""pipeline 陪聊雲端分支：cloud 模式先試 cloud_llm、None 降級回本地 llm。"""
import asyncio
import pytest
from server.pipeline import VoicePipeline


class _StubLLM:
    """本地 EdgeLLM 替身。"""
    def __init__(self, text):
        self._text = text
        self.called = False

    def available(self):
        return True

    def generate(self, student_text, scaffold, directive=None):
        self.called = True
        return self._text


class _StubCloud:
    def __init__(self, text):
        self._text = text
        self.called = False

    def available(self):
        return True

    def generate(self, student_text, scaffold, directive=None):
        self.called = True
        return self._text


class _StubTTS:
    def available(self):
        return False


async def _run(pipe):
    events = []
    return await pipe.run_turn_text("我看到一隻貓", events.append)


def _make(llm, cloud, mode):
    pipe = VoicePipeline(asr=None, llm=llm, tts=_StubTTS(), cloud_llm=cloud)
    pipe.network_mode = mode
    return pipe


def test_cloud_mode_uses_cloud_llm():
    local = _StubLLM("本地回覆")
    cloud = _StubCloud("雲端回覆 跟我說一遍：I see a cat")
    pipe = _make(local, cloud, "cloud")
    result = asyncio.run(_run(pipe))
    assert cloud.called is True
    assert local.called is False
    assert result.reply_text == "雲端回覆 跟我說一遍：I see a cat"


def test_cloud_none_falls_back_to_local():
    local = _StubLLM("本地回覆 跟我說一遍：I see a cat")
    cloud = _StubCloud(None)  # 雲端逾時/失敗
    pipe = _make(local, cloud, "cloud")
    result = asyncio.run(_run(pipe))
    assert cloud.called is True
    assert local.called is True
    assert result.reply_text == "本地回覆 跟我說一遍：I see a cat"


def test_edge_mode_never_calls_cloud():
    local = _StubLLM("本地回覆 跟我說一遍：I see a cat")
    cloud = _StubCloud("雲端回覆")
    pipe = _make(local, cloud, "edge")
    result = asyncio.run(_run(pipe))
    assert cloud.called is False
    assert local.called is True


def test_no_cloud_injected_is_backward_compatible():
    local = _StubLLM("本地回覆 跟我說一遍：I see a cat")
    pipe = VoicePipeline(asr=None, llm=local, tts=_StubTTS())  # 不傳 cloud_llm
    pipe.network_mode = "cloud"
    result = asyncio.run(_run(pipe))
    assert local.called is True
    assert result.reply_text == "本地回覆 跟我說一遍：I see a cat"
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `cd talkybuddy && .venv/bin/pytest tests/test_pipeline_cloud.py -q`
Expected: FAIL（`__init__() got an unexpected keyword argument 'cloud_llm'`）

- [ ] **Step 3: 改 `VoicePipeline.__init__`（`server/pipeline.py:114`）**

把
```python
    def __init__(self, asr, llm, tts):
        """依賴注入 ASR / LLM / TTS 引擎實例（測試可傳 stub）。"""
        self.asr = asr
        self.llm = llm
        self.tts = tts
```
改為
```python
    def __init__(self, asr, llm, tts, cloud_llm=None):
        """依賴注入 ASR / LLM / TTS 引擎實例（測試可傳 stub）。

        cloud_llm（可選）：雲端 LLM provider，network_mode=cloud 時優先於本地
        llm；預設 None → 純本地，行為與現況一致（向後相容）。
        """
        self.asr = asr
        self.llm = llm
        self.tts = tts
        self.cloud_llm = cloud_llm
```

- [ ] **Step 4: 改 `_process_text` 的 LLM 加值區塊（`server/pipeline.py:209-225`）**

把現有
```python
        # LLM 加值：available 時以 thread 生成，逾時/例外/None 一律降級回 scaffold
        t_llm = time.monotonic()
        llm_text: str | None = None
        try:
            if self.llm is not None and self.llm.available():
                llm_text = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.llm.generate, result.asr_text, sc, self._directive
                    ),
                    timeout=LLM_TIMEOUT_S,
                )
        except Exception:
            llm_text = None
```
改為
```python
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
```

- [ ] **Step 5: 執行測試確認通過**

Run: `cd talkybuddy && .venv/bin/pytest tests/test_pipeline_cloud.py -q`
Expected: PASS（4 passed）

- [ ] **Step 6: 全套回歸**

Run: `cd talkybuddy && .venv/bin/pytest -q`
Expected: PASS（既有全綠不受影響）

- [ ] **Step 7: Commit**

```bash
cd talkybuddy && git add server/pipeline.py tests/test_pipeline_cloud.py
git commit -m "feat(A): pipeline 陪聊雲端分支 cloud→edge→scaffold 降級鏈

VoicePipeline 加選填 cloud_llm；network_mode=cloud 先試雲端、None 降級本地。
edge 模式/未注入 cloud_llm 行為零改動（向後相容）。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: 導師接 Bedrock（`diagnose_via_bedrock` + generate_diagnosis 路由）

**Files:**
- Modify: `talkybuddy/server/diagnose.py`（抽共用 prompt/schema；`generate_diagnosis` `:602-624` 加路由）
- Modify: `talkybuddy/server/cloud_llm.py`（新增 `diagnose_via_bedrock`）
- Test: `talkybuddy/tests/test_cloud_llm.py`（追加）、`talkybuddy/tests/test_diagnose.py`（追加路由測試）

**Interfaces:**
- Consumes: `diagnose._build_diagnosis_prompt(interactions, prev) -> str`、`diagnose._DIAGNOSIS_SCHEMA: dict`、`diagnose._validate_diagnosis(dict) -> dict`。
- Produces: `cloud_llm.diagnose_via_bedrock(interactions, prev) -> dict | None`；`generate_diagnosis` 依 `config.LLM_CLOUD_PROVIDER` 路由。

- [ ] **Step 1: 抽 diagnose 的 prompt/schema 為共用（重構，先讓既有測試綠）**

在 `server/diagnose.py` 的 `_call_anthropic_api` **之前**新增模組級常數與 helper（把現有 `schema_example` 與 `prompt` 組法抽出）：

```python
# JSON schema（Bedrock toolChoice 用）：對齊 _validate_diagnosis 的必填欄位
_DIAGNOSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "object",
            "properties": {k: {"type": "integer"} for k in _DIM_KEYS},
            "required": list(_DIM_KEYS),
        },
        "strengths": {"type": "array", "items": {"type": "string"}},
        "weaknesses": {"type": "array", "items": {"type": "string"}},
        "emotional_status": {"type": "string"},
        "instructions": {
            "type": "object",
            "properties": {k: {"type": "string"} for k in ("classroom", "device", "peer")},
            "required": ["classroom", "device", "peer"],
        },
    },
    "required": ["scores", "strengths", "weaknesses", "emotional_status", "instructions"],
}


def _build_diagnosis_prompt(interactions: list[dict], prev: dict | None) -> str:
    """組診斷 prompt（anthropic/bedrock 共用；student_text 已去識別化）。"""
    brief = [
        {
            "student_text": guardrails.deidentify(str(it.get("student_text", ""))[:200]),
            "ai_response_text": str(it.get("ai_response_text", ""))[:200],
            "asr_confidence": it.get("asr_confidence"),
            "scores": it.get("scores"),
        }
        for it in (interactions or [])[-10:]
    ]
    schema_example = {
        "date": _today(),
        "scores": {"pronunciation": 62, "fluency": 58, "vocabulary": 65, "grammar": 54},
        "strengths": ["..."],
        "weaknesses": ["..."],
        "emotional_status": "...",
        "instructions": {"classroom": "...", "device": "...", "peer": "..."},
        "companion_directive": {
            "level": "L2",
            "difficulty": "up | hold | down",
            "next_goal": "下一步教學目標（繁體中文）",
            "topic": "本輪要帶的話題",
            "example_questions": ["What color is the pig?", "Do you see a cat?"],
            "fallback_hint": "學生沉默或困惑時的降級指引",
        },
    }
    return (
        "你是台灣國小英語學習診斷專家。以下是一位學生近期的口說互動紀錄"
        "（student_text 為學生原話、ai_response_text 為 AI 學伴回應、"
        "asr_confidence 為語音辨識信心 0-1、scores 為單次互動三維評分 0-100）：\n"
        + json.dumps(brief, ensure_ascii=False)
        + "\n\n前次診斷（可能為 null）：\n"
        + json.dumps(prev, ensure_ascii=False)
        + "\n\n請產出今日學習診斷，僅輸出一個 JSON 物件（不得有 markdown 圍欄或其他文字），"
        "所有文字內容用繁體中文（台灣用語），schema 與此範例完全一致：\n"
        + json.dumps(schema_example, ensure_ascii=False)
        + "\n\n注意：weaknesses 需具體（例如冠詞 a/an 誤用、中英夾雜比例、句長）；"
        "instructions 三欄分別給老師課堂活動、裝置端練習主題、同儕配對方式的具體建議；"
        "companion_directive 是給即時陪聊玩偶的下一輪策略（difficulty 依趨勢、"
        "next_goal/topic/example_questions 具體可帶入對話），example_questions 用英文、其餘用繁體中文。"
    )
```

然後把 `_call_anthropic_api` 內組 `brief`/`schema_example`/`prompt` 的段落，替換為單行：
```python
    prompt = _build_diagnosis_prompt(interactions, prev)
```
（刪除原本 `brief = [...]`、`schema_example = {...}`、`prompt = (...)` 三段；其餘 `body`/`req`/`urlopen`/解析不動。）

- [ ] **Step 2: 跑既有 diagnose 測試確認重構未破壞**

Run: `cd talkybuddy && .venv/bin/pytest tests/test_diagnose.py tests/test_diagnose_level.py -q`
Expected: PASS（既有全綠，重構等價）

- [ ] **Step 3: 寫失敗測試（`diagnose_via_bedrock` + generate_diagnosis 路由）**

在 `tests/test_cloud_llm.py` 追加：
```python
def _fake_tool_response(tool_input):
    return {"output": {"message": {"content": [{"toolUse": {"name": "diagnose", "input": tool_input}}]}}}


_VALID_DIAG = {
    "scores": {"pronunciation": 60, "fluency": 55, "vocabulary": 62, "grammar": 50},
    "strengths": ["願意開口"],
    "weaknesses": ["冠詞 a/an 誤用"],
    "emotional_status": "平穩",
    "instructions": {"classroom": "分組朗讀", "device": "顏色主題", "peer": "兩兩配對"},
}


def test_diagnose_via_bedrock_parses_tooluse(monkeypatch):
    fake = _FakeClient(_fake_tool_response(_VALID_DIAG))
    monkeypatch.setattr(cloud_llm, "_get_client", lambda: fake)
    out = cloud_llm.diagnose_via_bedrock([{"student_text": "cat"}], None)
    assert out["scores"]["pronunciation"] == 60
    assert out["instructions"]["classroom"] == "分組朗讀"


def test_diagnose_via_bedrock_none_on_bad_schema(monkeypatch):
    fake = _FakeClient(_fake_tool_response({"scores": {}}))  # 缺欄位
    monkeypatch.setattr(cloud_llm, "_get_client", lambda: fake)
    assert cloud_llm.diagnose_via_bedrock([{"student_text": "cat"}], None) is None
```

在 `tests/test_diagnose.py` 追加路由測試：
```python
def test_generate_diagnosis_routes_to_bedrock(monkeypatch):
    from server import diagnose, cloud_llm, config, guardrails
    monkeypatch.setattr(config, "LLM_CLOUD_PROVIDER", "bedrock", raising=False)
    monkeypatch.setattr(guardrails, "consent_granted", lambda: True)
    called = {"n": 0}

    def _fake_bedrock(interactions, prev):
        called["n"] += 1
        return {
            "date": diagnose._today(),
            "scores": {"pronunciation": 61, "fluency": 57, "vocabulary": 63, "grammar": 52},
            "strengths": ["願意開口"], "weaknesses": ["句長偏短"],
            "emotional_status": "平穩",
            "instructions": {"classroom": "a", "device": "b", "peer": "c"},
            "companion_directive": None,
        }

    monkeypatch.setattr(cloud_llm, "diagnose_via_bedrock", _fake_bedrock)
    out = diagnose.generate_diagnosis([{"student_text": "cat", "scores": {}}], None)
    assert called["n"] == 1
    assert out["scores"]["pronunciation"] == 61
    assert out.get("companion_directive")  # 收斂保底一定補上


def test_generate_diagnosis_bedrock_fail_falls_back_to_mock(monkeypatch):
    from server import diagnose, cloud_llm, config, guardrails
    monkeypatch.setattr(config, "LLM_CLOUD_PROVIDER", "bedrock", raising=False)
    monkeypatch.setattr(guardrails, "consent_granted", lambda: True)
    monkeypatch.setattr(cloud_llm, "diagnose_via_bedrock", lambda i, p: None)
    out = diagnose.generate_diagnosis([{"student_text": "貓", "scores": {}}], None)
    assert "scores" in out and out.get("companion_directive")  # mock 兜底
```

- [ ] **Step 4: 執行測試確認失敗**

Run: `cd talkybuddy && .venv/bin/pytest tests/test_cloud_llm.py tests/test_diagnose.py -q`
Expected: FAIL（`module 'server.cloud_llm' has no attribute 'diagnose_via_bedrock'`）

- [ ] **Step 5: 實作 `diagnose_via_bedrock`（`server/cloud_llm.py` 檔尾）**

```python
def _extract_tool_input(resp, tool_name):
    """從 Converse 回應取指定 tool 的 input dict；找不到回 None。"""
    try:
        for block in resp["output"]["message"]["content"]:
            tu = block.get("toolUse")
            if tu and tu.get("name") == tool_name:
                return tu.get("input")
    except Exception:
        pass
    return None


def diagnose_via_bedrock(interactions, prev):
    """導師：Bedrock Converse + toolChoice 強制診斷 JSON；失敗回 None。"""
    from server import diagnose
    try:
        client = _get_client()
        if client is None:
            return None
        cfg = _cfg()
        prompt = diagnose._build_diagnosis_prompt(interactions, prev)
        resp = client.converse(
            modelId=cfg.TUTOR_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            toolConfig={
                "tools": [{
                    "toolSpec": {
                        "name": "diagnose",
                        "description": "輸出學生今日學習診斷結構化 JSON",
                        "inputSchema": {"json": diagnose._DIAGNOSIS_SCHEMA},
                    }
                }],
                "toolChoice": {"tool": {"name": "diagnose"}},
            },
        )
        tool_input = _extract_tool_input(resp, "diagnose")
        if tool_input is None:
            return None
        return diagnose._validate_diagnosis(tool_input)
    except Exception:
        _log.exception("diagnose_via_bedrock 失敗，呼叫端 fallback mock")
        return None
```

- [ ] **Step 6: 在 `generate_diagnosis` 加 provider 路由（`server/diagnose.py:609-620`）**

把
```python
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    result = None
    # B4-5 consent gate：真正的雲端資料出境 chokepoint。未取得家長同意時，
    # 即使有金鑰也不上雲，改走本地規則式（背景 _refresh_directive 亦涵蓋）。
    if api_key and guardrails.consent_granted():
        try:
            result = _call_anthropic_api(interactions, prev, api_key)
        except Exception:
            # 雲端層失敗：不拋錯、不阻塞，改用邊緣端規則式診斷
            result = None
    if result is None:
        result = _rule_based_diagnosis(interactions, prev)
```
改為
```python
    from server import config
    provider = getattr(config, "LLM_CLOUD_PROVIDER", "off")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    result = None
    # B4-5 consent gate：真正的雲端資料出境 chokepoint。未取得家長同意時，
    # 即使 provider 開著也不上雲，改走本地規則式（背景 _refresh_directive 亦涵蓋）。
    if guardrails.consent_granted():
        try:
            if provider == "bedrock":
                from server import cloud_llm
                result = cloud_llm.diagnose_via_bedrock(interactions, prev)
            elif provider == "anthropic" and api_key:
                result = _call_anthropic_api(interactions, prev, api_key)
        except Exception:
            # 雲端層失敗：不拋錯、不阻塞，改用邊緣端規則式診斷
            result = None
    if result is None:
        result = _rule_based_diagnosis(interactions, prev)
```

- [ ] **Step 7: 執行測試確認通過**

Run: `cd talkybuddy && .venv/bin/pytest tests/test_cloud_llm.py tests/test_diagnose.py -q`
Expected: PASS

- [ ] **Step 8: 全套回歸**

Run: `cd talkybuddy && .venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
cd talkybuddy && git add server/diagnose.py server/cloud_llm.py tests/test_cloud_llm.py tests/test_diagnose.py
git commit -m "feat(A): 導師接 Bedrock Converse (toolChoice 強制 JSON) + provider 路由

抽 _build_diagnosis_prompt/_DIAGNOSIS_SCHEMA 共用；generate_diagnosis 依
LLM_CLOUD_PROVIDER 路由 bedrock→anthropic→mock，consent gate 不變。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: app.py 注入啟用 + 端到端回歸

**Files:**
- Modify: `talkybuddy/server/app.py`（建 pipeline 處注入 `CloudLLM`）
- Test: 沿用全套；手動驗證步驟見下

**Interfaces:**
- Consumes: `cloud_llm.CloudLLM`（Task 1）、`VoicePipeline(..., cloud_llm=...)`（Task 2）。
- Produces: 執行期 pipeline 具備雲端 provider（provider=off 時 available()=False → 行為不變）。

- [ ] **Step 1: 找到 app.py 建 VoicePipeline 之處**

Run: `cd talkybuddy && grep -n "VoicePipeline(" server/app.py`
Expected: 一行形如 `pipeline = VoicePipeline(asr=..., llm=..., tts=...)`

- [ ] **Step 2: 注入 CloudLLM**

在 `server/app.py` 建立 pipeline 的區塊，import 區加 `from server import cloud_llm`，並把
```python
pipeline = VoicePipeline(asr=..., llm=..., tts=...)
```
改為（保留原有 asr/llm/tts 實參不動，僅追加 `cloud_llm=`）
```python
pipeline = VoicePipeline(asr=..., llm=..., tts=..., cloud_llm=cloud_llm.CloudLLM())
```

- [ ] **Step 3: 全套回歸（provider 預設 bedrock，但無憑證 → available() False → 行為不變）**

Run: `cd talkybuddy && LLM_CLOUD_PROVIDER=off .venv/bin/pytest -q`
Expected: PASS（全綠）
Run: `cd talkybuddy && .venv/bin/pytest -q`
Expected: PASS（無 AWS 憑證時 `_get_client` 回 None → 降級，測試不打真 Bedrock）

- [ ] **Step 4: 手動驗證（離線降級，無需 Bedrock）**

```bash
cd talkybuddy && LLM_CLOUD_PROVIDER=off bash scripts/run.sh
# 瀏覽器開 http://localhost:8787，切 cloud 模式講一句 → 應正常回覆（走本地 EdgeLLM/scaffold）
# 觀察 logs 無例外；確認 network_mode=off 時完全等同現況
```

- [ ] **Step 5: 手動驗證（有 Bedrock 憑證時，可選）**

```bash
cd talkybuddy && LLM_CLOUD_PROVIDER=bedrock BEDROCK_REGION=us-east-1 \
  AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... bash scripts/run.sh
# 切 cloud 模式講一句 → 陪聊回覆由 Bedrock 生成、仍守「中文稱讚→帶讀→護欄」
# 切 network_mode=cloud 觸發 /api/network_mode → 導師診斷經 Bedrock、companion_directive 正常
# 拔網 → 應無縫降級回本地，demo 不中斷
```

- [ ] **Step 6: Commit**

```bash
cd talkybuddy && git add server/app.py
git commit -m "feat(A): app.py 注入 CloudLLM 啟用雲端大腦

provider=off/無憑證時 available()=False → 降級回本地，行為零改動。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review（對照 spec）

**Spec coverage：**
- §4.1 陪聊即時路徑 cloud→edge→scaffold → Task 2 ✓
- §4.2 導師 provider 路由 bedrock→anthropic→mock → Task 3 ✓
- §5.1 cloud_llm.py（generate + diagnose_via_bedrock）→ Task 1、Task 3 ✓
- §5.2 config 4 常數 → Task 1 ✓
- §5.3 llm.py 抽共用 → 採「退而 import 常數」（`_system_prompt` 重用 `EdgeLLM._SYSTEM_PROMPT`），不重構 llm.py（低風險，spec 允許）✓
- §5.4 接線（pipeline/diagnose/app）→ Task 2/3/4 ✓
- §6 韌性契約 → 每 Task 測降級 + Task 4 provider=off 回歸 ✓
- §7 測試（5 類）→ Task 1/2/3 測試涵蓋 generate 過濾、off 降級、cloud→edge、bedrock JSON、deidentify ✓
- §5 B4 安全地板：deidentify 上雲（陪聊 Task 1、導師 `_build_diagnosis_prompt`）、passes_guardrail 雲端輸出（Task 1）、consent gate（既有，Task 3 保留）✓

**Placeholder scan：** model id 為範例值（實機以 console 為準），已註明，非 plan placeholder；其餘無 TBD/TODO。

**Type consistency：** `CloudLLM.generate(student_text, scaffold, directive=None)->str|None` 於 Task 1 定義、Task 2 呼用一致；`diagnose_via_bedrock(interactions, prev)->dict|None`、`_build_diagnosis_prompt`/`_DIAGNOSIS_SCHEMA`/`_validate_diagnosis` 於 Task 3 定義並互用一致；`VoicePipeline(..., cloud_llm=None)` Task 2 定義、Task 4 注入一致。
