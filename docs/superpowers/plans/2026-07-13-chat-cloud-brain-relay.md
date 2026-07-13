# 對話接自架中轉（雲端腦）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓即時對話回覆能走雲端腦（Anthropic 相容 Messages API，可指向自架中轉），失敗降級本地 EdgeLLM 再降級 scaffold。

**Architecture:** 新增 `CloudLLM`（契約同 `EdgeLLM`）比照 `CloudTTS` 由 `app.py` 注入 `VoicePipeline`；relay 端點/認證/model 解析抽成共用模組 `anthropic_relay`，`diagnose` 與 `cloud_llm` 共用同一組 `ANTHROPIC_*` env。上雲前 `deidentify` 學生文字、輸出過 `passes_guardrail`，`consent + cloud` 雙閘。

**Tech Stack:** Python 3、標準函式庫 `urllib`（零新依賴）、pytest（`pytest.mark.anyio`）。

## Global Constraints

- 純標準函式庫；不得新增重依賴；import 期不觸網、不載重依賴（與 `diagnose.py` 一致）。
- 金鑰只讀環境變數，絕不寫入 repo。
- 任何雲端失敗（HTTP/逾時/JSON/schema/護欄）→ `generate` 回 `None`，pipeline 靜默降級，對話不中斷。
- 測試從 `talkybuddy/` 根以 `./run_tests.sh`（即 `.venv/bin/python -m pytest -q`）執行；`import server...` 靠 `python -m pytest` 把 repo 根放上 sys.path。
- 分支：`feat/chat-cloud-relay`（已建）。

---

### Task 1: 抽出 `anthropic_relay.py`，diagnose 改用共用解析

**Files:**
- Create: `talkybuddy/server/anthropic_relay.py`
- Create: `talkybuddy/tests/test_anthropic_relay.py`
- Modify: `talkybuddy/server/diagnose.py`（刪 `_API_URL`/`_API_MODEL`/`_messages_url`/`_resolve_api_config`；`generate_diagnosis` 改呼叫 `anthropic_relay.resolve_config()`）
- Modify: `talkybuddy/tests/test_diagnose_relay.py`（保留 `_call_anthropic_api` 整合測試，cfg 改由 `anthropic_relay` 取得）

**Interfaces:**
- Produces: `anthropic_relay.resolve_config() -> dict | None`（`{"url": str, "model": str, "headers": dict}` 或 `None`）、`anthropic_relay.messages_url(base: str) -> str`、常數 `DEFAULT_URL` / `DEFAULT_MODEL` / `ANTHROPIC_VERSION`。

- [ ] **Step 1: 寫失敗測試 `tests/test_anthropic_relay.py`**

```python
# -*- coding: utf-8 -*-
"""test_anthropic_relay.py — 雲端腦端點/認證/model 由環境變數解析（純函式，不觸網）。

diagnose 與 cloud_llm 共用此模組。涵蓋：無憑證→None、x-api-key vs Bearer、
AUTH_TOKEN 優先、base_url 四型正規化、model override。
"""
from __future__ import annotations

import pytest

from server import anthropic_relay

_ALL_ENV = [
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_MODEL",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ALL_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_no_credential_returns_none(_clean_env):
    assert anthropic_relay.resolve_config() is None


def test_api_key_uses_official_endpoint_and_x_api_key(_clean_env):
    _clean_env.setenv("ANTHROPIC_API_KEY", "sk-official")
    cfg = anthropic_relay.resolve_config()
    assert cfg is not None
    assert cfg["url"] == anthropic_relay.DEFAULT_URL
    assert cfg["model"] == anthropic_relay.DEFAULT_MODEL
    assert cfg["headers"].get("x-api-key") == "sk-official"
    assert "Authorization" not in cfg["headers"]
    assert cfg["headers"].get("anthropic-version") == "2023-06-01"


def test_auth_token_uses_bearer(_clean_env):
    _clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "sk-relay-token")
    cfg = anthropic_relay.resolve_config()
    assert cfg["headers"].get("Authorization") == "Bearer sk-relay-token"
    assert "x-api-key" not in cfg["headers"]


def test_auth_token_wins_over_api_key(_clean_env):
    _clean_env.setenv("ANTHROPIC_API_KEY", "sk-official")
    _clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "sk-relay-token")
    cfg = anthropic_relay.resolve_config()
    assert cfg["headers"].get("Authorization") == "Bearer sk-relay-token"
    assert "x-api-key" not in cfg["headers"]


def test_base_url_override_appends_messages_path(_clean_env):
    _clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "t")
    _clean_env.setenv("ANTHROPIC_BASE_URL", "http://192.168.100.200:8317")
    cfg = anthropic_relay.resolve_config()
    assert cfg["url"] == "http://192.168.100.200:8317/v1/messages"


def test_base_url_trailing_slash(_clean_env):
    _clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "t")
    _clean_env.setenv("ANTHROPIC_BASE_URL", "http://relay:8317/")
    cfg = anthropic_relay.resolve_config()
    assert cfg["url"] == "http://relay:8317/v1/messages"


def test_base_url_already_has_v1(_clean_env):
    _clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "t")
    _clean_env.setenv("ANTHROPIC_BASE_URL", "http://relay:8317/v1")
    cfg = anthropic_relay.resolve_config()
    assert cfg["url"] == "http://relay:8317/v1/messages"


def test_base_url_full_messages_path_used_as_is(_clean_env):
    _clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "t")
    _clean_env.setenv("ANTHROPIC_BASE_URL", "http://relay:8317/v1/messages")
    cfg = anthropic_relay.resolve_config()
    assert cfg["url"] == "http://relay:8317/v1/messages"


def test_model_override(_clean_env):
    _clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "t")
    _clean_env.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-4-8")
    cfg = anthropic_relay.resolve_config()
    assert cfg["model"] == "claude-opus-4-8"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_anthropic_relay.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'server.anthropic_relay'`）

- [ ] **Step 3: 建立 `server/anthropic_relay.py`**

```python
# -*- coding: utf-8 -*-
"""Anthropic 相容 Messages API 的端點/認證/model 解析（純函式、只讀 env、不觸網）。

diagnose 與 cloud_llm 共用同一組環境變數，支援官方金鑰或自架中轉（relay）。
認證順序與官方 SDK 一致：ANTHROPIC_AUTH_TOKEN（→ Authorization: Bearer）優先於
ANTHROPIC_API_KEY（→ x-api-key）。端點由 ANTHROPIC_BASE_URL 覆蓋（自架中轉）；
model 由 ANTHROPIC_DEFAULT_OPUS_MODEL 或 ANTHROPIC_MODEL 覆蓋。金鑰只讀 env、絕不寫進 repo。
"""

from __future__ import annotations

import os

DEFAULT_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5"
ANTHROPIC_VERSION = "2023-06-01"


def messages_url(base: str) -> str:
    """把 ANTHROPIC_BASE_URL（root、含 /v1、或完整端點）正規化成 Messages API 端點。

    相容官方 SDK base_url 慣例（root → 補 /v1/messages）。
    """
    base = base.strip().rstrip("/")
    if base.endswith("/messages"):
        return base
    if base.endswith("/v1"):
        return base + "/messages"
    return base + "/v1/messages"


def resolve_config() -> dict | None:
    """由環境變數解析雲端腦呼叫設定；回 {"url","model","headers"} 或無憑證時 None。"""
    auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not (auth_token or api_key):
        return None

    headers = {
        "Content-Type": "application/json",
        "anthropic-version": ANTHROPIC_VERSION,
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    else:
        headers["x-api-key"] = api_key

    base = os.environ.get("ANTHROPIC_BASE_URL")
    url = messages_url(base) if base else DEFAULT_URL
    model = (
        os.environ.get("ANTHROPIC_DEFAULT_OPUS_MODEL")
        or os.environ.get("ANTHROPIC_MODEL")
        or DEFAULT_MODEL
    )
    return {"url": url, "model": model, "headers": headers}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_anthropic_relay.py -q`
Expected: PASS（9 passed）

- [ ] **Step 5: 改 `diagnose.py` 用共用模組**

在 `diagnose.py` 的 import 區加入 `anthropic_relay`（與其他 `from server import ...` 同處）：

```python
from server import anthropic_relay, guardrails
```

刪除 `diagnose.py` 中這幾個定義（改由 `anthropic_relay` 提供）：
- 常數 `_API_URL = "https://api.anthropic.com/v1/messages"`
- 常數 `_API_MODEL = "claude-sonnet-5"`
- 函式 `def _messages_url(base: str) -> str: ...`（整段）
- 函式 `def _resolve_api_config() -> dict | None: ...`（整段）

（保留 `_API_TIMEOUT_SEC = 12`，`_call_anthropic_api` 仍用它。）

在 `generate_diagnosis` 內，把：

```python
    cfg = _resolve_api_config()
```

改為：

```python
    cfg = anthropic_relay.resolve_config()
```

- [ ] **Step 6: 改 `tests/test_diagnose_relay.py` 只留整合測試**

把整個檔案覆寫為（resolver 單元測試已移到 test_anthropic_relay.py）：

```python
# -*- coding: utf-8 -*-
"""test_diagnose_relay.py — diagnose 的雲端呼叫整合測試（cfg 由 anthropic_relay 提供）。

驗證抽出共用解析後，_call_anthropic_api 仍依 cfg 命中中轉 url、Bearer、覆蓋 model。
"""
from __future__ import annotations

import pytest

from server import anthropic_relay, diagnose

_ALL_ENV = [
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_MODEL",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ALL_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_call_anthropic_api_uses_resolved_config(_clean_env, monkeypatch):
    _clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "sk-relay-token")
    _clean_env.setenv("ANTHROPIC_BASE_URL", "http://192.168.100.200:8317")
    _clean_env.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-4-8")

    captured = {}

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            import json

            valid = {
                "scores": {"pronunciation": 60, "fluency": 60,
                           "vocabulary": 60, "grammar": 60},
                "strengths": ["願意開口"],
                "weaknesses": ["句子偏短"],
                "emotional_status": "穩定",
                "instructions": {"classroom": "a", "device": "b", "peer": "c"},
            }
            return json.dumps(
                {"content": [{"type": "text", "text": json.dumps(valid)}]}
            ).encode("utf-8")

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        import json as _j
        captured["body"] = _j.loads(req.data.decode("utf-8"))
        return _FakeResp()

    monkeypatch.setattr(diagnose.urllib.request, "urlopen", _fake_urlopen)

    cfg = anthropic_relay.resolve_config()
    out = diagnose._call_anthropic_api([], None, cfg)

    assert captured["url"] == "http://192.168.100.200:8317/v1/messages"
    hdrs = {k.lower(): v for k, v in captured["headers"].items()}
    assert hdrs.get("authorization") == "Bearer sk-relay-token"
    assert "x-api-key" not in hdrs
    assert captured["body"]["model"] == "claude-opus-4-8"
    assert out["scores"]["fluency"] == 60
```

- [ ] **Step 7: 跑相關測試確認全綠**

Run: `.venv/bin/python -m pytest tests/test_anthropic_relay.py tests/test_diagnose_relay.py tests/test_diagnose.py tests/test_diagnose_level.py -q`
Expected: PASS（全部通過，diagnose 行為不變）

- [ ] **Step 8: Commit**

```bash
git add talkybuddy/server/anthropic_relay.py talkybuddy/server/diagnose.py \
        talkybuddy/tests/test_anthropic_relay.py talkybuddy/tests/test_diagnose_relay.py
git commit -m "refactor(cloud): 抽出 anthropic_relay 共用端點/認證/model 解析

diagnose 改用 anthropic_relay.resolve_config()；resolver 單元測試移到
test_anthropic_relay，diagnose_relay 只留 _call_anthropic_api 整合測試。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `CloudLLM`（雲端腦對話加值層）

**Files:**
- Create: `talkybuddy/server/cloud_llm.py`
- Create: `talkybuddy/tests/test_cloud_llm.py`

**Interfaces:**
- Consumes: `anthropic_relay.resolve_config()`、`guardrails.deidentify()` / `passes_guardrail()` / `CHILD_SAFETY_CLAUSE`。
- Produces: `CloudLLM.available() -> bool`、`CloudLLM.generate(student_text: str, scaffold, directive: str | None = None) -> str | None`（契約同 `EdgeLLM`，供 pipeline 注入）。

- [ ] **Step 1: 寫失敗測試 `tests/test_cloud_llm.py`**

```python
# -*- coding: utf-8 -*-
"""test_cloud_llm.py — CloudLLM 雲端腦對話加值層（契約同 EdgeLLM）。

涵蓋：available 有無憑證；generate 成功命中 relay；上雲前去識別化；護欄命中→None；
目標句缺漏→自動補帶讀句；urlopen 例外→None。全程 monkeypatch urlopen、不觸網。
"""
from __future__ import annotations

import json

import pytest

from server import cloud_llm as cloud_llm_mod
from server.cloud_llm import CloudLLM

_ALL_ENV = [
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_MODEL",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ALL_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class _Sc:
    """假 ScaffoldResult：只需 target_sentence。"""
    target_sentence = "I like apples."


def _fake_resp(text: str):
    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(
                {"content": [{"type": "text", "text": text}]}
            ).encode("utf-8")

    return _R()


def test_available_false_without_credential(_clean_env):
    assert CloudLLM().available() is False


def test_available_true_with_credential(_clean_env):
    _clean_env.setenv("ANTHROPIC_API_KEY", "sk-x")
    assert CloudLLM().available() is True


def test_generate_success_hits_relay(_clean_env, monkeypatch):
    _clean_env.setenv("ANTHROPIC_AUTH_TOKEN", "sk-relay")
    _clean_env.setenv("ANTHROPIC_BASE_URL", "http://relay:8317")
    _clean_env.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-4-8")
    captured = {}

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _fake_resp("你好棒！跟我說一遍：I like apples.")

    monkeypatch.setattr(cloud_llm_mod.urllib.request, "urlopen", _fake_urlopen)

    out = CloudLLM().generate("我喜歡蘋果", _Sc())
    assert out == "你好棒！跟我說一遍：I like apples."
    assert captured["url"] == "http://relay:8317/v1/messages"
    assert captured["headers"].get("authorization") == "Bearer sk-relay"
    assert captured["body"]["model"] == "claude-opus-4-8"


def test_generate_deidentifies_student_text(_clean_env, monkeypatch):
    _clean_env.setenv("ANTHROPIC_API_KEY", "sk-x")
    captured = {}

    def _fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _fake_resp("你好棒！跟我說一遍：I like apples.")

    monkeypatch.setattr(cloud_llm_mod.urllib.request, "urlopen", _fake_urlopen)

    CloudLLM().generate("我叫 Mimi", _Sc())
    user_content = captured["body"]["messages"][0]["content"]
    assert "Mimi" not in user_content
    assert "[名字]" in user_content


def test_generate_guardrail_reject_returns_none(_clean_env, monkeypatch):
    _clean_env.setenv("ANTHROPIC_API_KEY", "sk-x")

    def _fake_urlopen(req, timeout=None):
        return _fake_resp("任意輸出")

    monkeypatch.setattr(cloud_llm_mod.urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(cloud_llm_mod.guardrails, "passes_guardrail", lambda t: False)

    assert CloudLLM().generate("hi", _Sc()) is None


def test_generate_appends_missing_target(_clean_env, monkeypatch):
    _clean_env.setenv("ANTHROPIC_API_KEY", "sk-x")

    def _fake_urlopen(req, timeout=None):
        return _fake_resp("你今天好棒喔")  # 沒有目標英文句

    monkeypatch.setattr(cloud_llm_mod.urllib.request, "urlopen", _fake_urlopen)

    out = CloudLLM().generate("hi", _Sc())
    assert "跟我說一遍：I like apples." in out


def test_generate_urlopen_raises_returns_none(_clean_env, monkeypatch):
    _clean_env.setenv("ANTHROPIC_API_KEY", "sk-x")

    def _boom(req, timeout=None):
        raise TimeoutError("boom")

    monkeypatch.setattr(cloud_llm_mod.urllib.request, "urlopen", _boom)
    assert CloudLLM().generate("hi", _Sc()) is None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_cloud_llm.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'server.cloud_llm'`）

- [ ] **Step 3: 建立 `server/cloud_llm.py`**

```python
# -*- coding: utf-8 -*-
"""CloudLLM：對話回覆的雲端腦加值層（Anthropic 相容 Messages API，可接自架中轉）。

契約與 EdgeLLM 一致（available/generate），任何失敗一律回 None 讓 pipeline 降級。
純標準函式庫（urllib），import 期不觸網、不載重依賴。上雲前對學生文字去識別化，
輸出過 guardrails 後置護欄。端點/認證/model 由 anthropic_relay 解析（與 diagnose 共用 env）。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from server import anthropic_relay, guardrails

_log = logging.getLogger(__name__)

# 雲端呼叫逾時（秒）；與 pipeline 外層 LLM_TIMEOUT_S 對齊，雙保險。
_TIMEOUT_S = 8.0
_MAX_TOKENS = 160

# 台灣國小英語鷹架家教 system prompt（安全條款重用 guardrails 共用常數）
_SYSTEM_PROMPT = (
    "你是台灣國小學生的英語鷹架家教，只用繁體中文和英文回覆。"
    "嚴格遵守以下規則："
    "一、第一句先用繁體中文稱讚或鼓勵學生。"
    "二、接著帶讀指定的目標英文句，格式必須是「跟我說一遍：<英文句>」，"
    "英文句必須逐字使用我提供的目標句，不可改寫。"
    "三、全部回覆總長不超過60個字。"
    "四、禁止使用 markdown 符號、emoji 表情、以及英文以外的其他外語。"
) + guardrails.CHILD_SAFETY_CLAUSE


def _extract_text(payload: dict) -> str:
    """從 Anthropic Messages 回應取第一段 text；格式不符回空字串。"""
    try:
        for block in payload.get("content", []):
            if block.get("type") == "text":
                return block.get("text", "") or ""
    except Exception:
        pass
    return ""


class CloudLLM:
    """雲端腦對話加值；憑證可解析即 available，consent/network 由 pipeline 把關。"""

    def available(self) -> bool:
        """憑證可解析（resolve_config 非 None）即 True；任何失敗回 False。"""
        try:
            return anthropic_relay.resolve_config() is not None
        except Exception:
            return False

    def generate(self, student_text: str, scaffold, directive: str | None = None) -> str | None:
        """以雲端腦生成加值回覆；任何失敗、逾時、護欄命中回 None（絕不拋進 pipeline）。"""
        try:
            cfg = anthropic_relay.resolve_config()
            if cfg is None:
                return None
            target = getattr(scaffold, "target_sentence", None)
            safe_text = guardrails.deidentify(student_text)  # 上雲前去識別化
            directive_block = (
                f"\n{directive.strip()}\n" if directive and directive.strip() else ""
            )
            user_prompt = (
                f"學生剛剛說：「{safe_text}」\n"
                f"目標英文句：{target or ''}\n"
                f"{directive_block}"
                "請照規則回覆：先一句繁體中文稱讚鼓勵，"
                "再用「跟我說一遍：<英文句>」帶讀目標英文句。"
            )
            body = json.dumps(
                {
                    "model": cfg["model"],
                    "max_tokens": _MAX_TOKENS,
                    "system": _SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user_prompt}],
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                cfg["url"], data=body, headers=cfg["headers"], method="POST",
            )
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                payload = json.loads(resp.read().decode("utf-8"))

            text = _extract_text(payload).strip()
            if not text:
                return None
            # 輸出後置護欄（不安全→降級回 edge/scaffold）
            if not guardrails.passes_guardrail(text):
                return None
            # 帶讀不可漏句：目標英文句一定要在回覆中
            if target and target not in text:
                text = f"{text} 跟我說一遍：{target}"
            return text
        except Exception:
            _log.exception("CloudLLM generate 失敗，降級回 edge/scaffold")
            return None
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_cloud_llm.py -q`
Expected: PASS（6 passed）

- [ ] **Step 5: Commit**

```bash
git add talkybuddy/server/cloud_llm.py talkybuddy/tests/test_cloud_llm.py
git commit -m "feat(cloud): CloudLLM 雲端腦對話加值層（契約同 EdgeLLM）

urllib 直呼 Anthropic 相容 Messages API；上雲前 deidentify、輸出過
passes_guardrail、目標句缺漏自動補；任何失敗回 None 讓 pipeline 降級。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: pipeline 注入 CloudLLM + cloud→edge→scaffold 降級

**Files:**
- Modify: `talkybuddy/server/pipeline.py`（`__init__` 加 `cloud_llm=None`；top import 加 `guardrails`；`_process_text` 加值段改降級鏈）
- Modify: `talkybuddy/tests/test_pipeline.py`（新增 4 個降級/閘測試）

**Interfaces:**
- Consumes: `CloudLLM`/`EdgeLLM` 契約（`available()`/`generate(text, sc, directive)`）、`guardrails.consent_granted()`、`self.network_mode`。
- Produces: `VoicePipeline(asr, llm, tts, cloud_tts=None, cloud_llm=None, student_id=None)`（新增 `cloud_llm` kwarg）。

- [ ] **Step 1: 寫失敗測試（append 到 `tests/test_pipeline.py` 末端）**

```python
# ---------------------------------------------------------------------------
# 雲端腦降級鏈：cloud → edge → scaffold（consent + cloud 雙閘）
# ---------------------------------------------------------------------------

async def test_cloud_mode_uses_cloud_reply_when_consent(monkeypatch):
    """cloud 模式 + consent + cloud 有回覆 → reply 用雲端。"""
    monkeypatch.setattr(config, "CONSENT_GRANTED", True)
    events: list[dict] = []
    emit = await _collecting_emit(events)
    vp = VoicePipeline(
        StubASR("hello", 0.9),
        StubLLM(reply="EDGE 回覆"),
        StubTTS(),
        cloud_llm=StubLLM(reply="CLOUD 回覆：Hi there."),
    )
    vp.network_mode = "cloud"
    result = await vp.run_turn_text("hello", emit)
    assert result.reply_text == "CLOUD 回覆：Hi there."


async def test_cloud_none_falls_back_to_edge(monkeypatch):
    """cloud 回 None → 降級 edge。"""
    monkeypatch.setattr(config, "CONSENT_GRANTED", True)
    events: list[dict] = []
    emit = await _collecting_emit(events)
    vp = VoicePipeline(
        StubASR("hello", 0.9),
        StubLLM(reply="EDGE 回覆：Hi."),
        StubTTS(),
        cloud_llm=StubLLM(reply=None),
    )
    vp.network_mode = "cloud"
    result = await vp.run_turn_text("hello", emit)
    assert result.reply_text == "EDGE 回覆：Hi."


async def test_no_consent_skips_cloud(monkeypatch):
    """無 consent → 完全不呼叫雲端（即使 cloud 有回覆），走 edge。"""
    monkeypatch.setattr(config, "CONSENT_GRANTED", False)
    events: list[dict] = []
    emit = await _collecting_emit(events)
    called = {"cloud": False}

    class _SpyCloud(StubLLM):
        def generate(self, student_text, scaffold_result, directive=None):
            called["cloud"] = True
            return "CLOUD 不該被用"

    vp = VoicePipeline(
        StubASR("hello", 0.9),
        StubLLM(reply="EDGE 回覆：Hi."),
        StubTTS(),
        cloud_llm=_SpyCloud(reply="x"),
    )
    vp.network_mode = "cloud"
    result = await vp.run_turn_text("hello", emit)
    assert called["cloud"] is False
    assert result.reply_text == "EDGE 回覆：Hi."


async def test_edge_mode_skips_cloud(monkeypatch):
    """edge 模式 → 不呼叫雲端，即使有 cloud 引擎。"""
    monkeypatch.setattr(config, "CONSENT_GRANTED", True)
    events: list[dict] = []
    emit = await _collecting_emit(events)
    called = {"cloud": False}

    class _SpyCloud(StubLLM):
        def generate(self, student_text, scaffold_result, directive=None):
            called["cloud"] = True
            return "CLOUD 不該被用"

    vp = VoicePipeline(
        StubASR("hello", 0.9),
        StubLLM(reply="EDGE 回覆：Hi."),
        StubTTS(),
        cloud_llm=_SpyCloud(reply="x"),
    )
    vp.network_mode = "edge"
    result = await vp.run_turn_text("hello", emit)
    assert called["cloud"] is False
    assert result.reply_text == "EDGE 回覆：Hi."
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -q -k "cloud or consent or edge_mode"`
Expected: FAIL（`TypeError: __init__() got an unexpected keyword argument 'cloud_llm'`）

- [ ] **Step 3: 改 `pipeline.py` — top import 加 guardrails**

把：

```python
from server import config, scaffold, store
```

改為：

```python
from server import config, guardrails, scaffold, store
```

- [ ] **Step 4: 改 `pipeline.py` — `__init__` 加 `cloud_llm` kwarg**

把 `__init__` 簽章：

```python
    def __init__(self, asr, llm, tts, cloud_tts=None, student_id=None):
```

改為：

```python
    def __init__(self, asr, llm, tts, cloud_tts=None, cloud_llm=None, student_id=None):
```

並在 `self.cloud_tts = cloud_tts` 之後加一行：

```python
        self.cloud_llm = cloud_llm
```

- [ ] **Step 5: 改 `pipeline.py` — `_process_text` 加值段改降級鏈**

把現有這段（LLM 加值）：

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
        result.latency_ms["llm"] = int((time.monotonic() - t_llm) * 1000)
        if llm_text and isinstance(llm_text, str) and llm_text.strip():
            result.reply_text = llm_text.strip()
            segments = scaffold.split_tts_segments(result.reply_text)
```

改為：

```python
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
                break
        result.latency_ms["llm"] = int((time.monotonic() - t_llm) * 1000)
        if llm_text:
            result.reply_text = llm_text.strip()
            segments = scaffold.split_tts_segments(result.reply_text)
```

- [ ] **Step 6: 跑測試確認通過（新測試 + 全 pipeline 迴歸）**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py tests/test_pipeline_cloud_tts.py tests/test_pipeline_directive.py tests/test_pipeline_profile.py -q`
Expected: PASS（含 4 個新測試 + 既有全綠）

- [ ] **Step 7: Commit**

```bash
git add talkybuddy/server/pipeline.py talkybuddy/tests/test_pipeline.py
git commit -m "feat(pipeline): 對話加值改 cloud→edge→scaffold 降級鏈

VoicePipeline 新增 cloud_llm 注入；network_mode==cloud 且 consent 時優先雲端腦，
None/逾時降級 EdgeLLM 再降 scaffold。edge 模式或無 consent 完全不碰雲端。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: app.py 注入 + /api/status + CONTRACTS.md

**Files:**
- Modify: `talkybuddy/server/app.py`（import、單例、注入全域與每連線 pipeline、status）
- Create: `talkybuddy/tests/test_app_cloud_llm.py`
- Modify: `talkybuddy/server/CONTRACTS.md`（補 cloud_llm 契約段）

**Interfaces:**
- Consumes: `CloudLLM`、`VoicePipeline(..., cloud_llm=...)`。
- Produces: `/api/status` 回應多一個 `"cloud_llm": bool` 欄位。

- [ ] **Step 1: 寫失敗測試 `tests/test_app_cloud_llm.py`**

```python
# -*- coding: utf-8 -*-
"""test_app_cloud_llm.py — /api/status 回報 cloud_llm 引擎可用性欄位。"""
from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport

from server.app import app

pytestmark = pytest.mark.anyio


async def test_status_reports_cloud_llm_bool():
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/status")
    assert r.status_code == 200
    data = r.json()
    assert "cloud_llm" in data
    assert isinstance(data["cloud_llm"], bool)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/test_app_cloud_llm.py -q`
Expected: FAIL（`assert "cloud_llm" in data`）

- [ ] **Step 3: 改 `app.py` — import CloudLLM**

在 `from server.cloud_tts import CloudTTS` 下一行加：

```python
from server.cloud_llm import CloudLLM
```

- [ ] **Step 4: 改 `app.py` — 建單例並注入全域 pipeline**

把：

```python
cloud_tts_engine = CloudTTS()
pipeline = VoicePipeline(asr_engine, llm_engine, tts_engine, cloud_tts=cloud_tts_engine)
```

改為：

```python
cloud_tts_engine = CloudTTS()
cloud_llm_engine = CloudLLM()
pipeline = VoicePipeline(
    asr_engine, llm_engine, tts_engine,
    cloud_tts=cloud_tts_engine, cloud_llm=cloud_llm_engine,
)
```

- [ ] **Step 5: 改 `app.py` — 每連線 pipeline 也注入**

把：

```python
    conn_pipe = VoicePipeline(
        asr_engine, llm_engine, tts_engine,
        cloud_tts=cloud_tts_engine, student_id=sid,
    )
```

改為：

```python
    conn_pipe = VoicePipeline(
        asr_engine, llm_engine, tts_engine,
        cloud_tts=cloud_tts_engine, cloud_llm=cloud_llm_engine, student_id=sid,
    )
```

- [ ] **Step 6: 改 `app.py` — /api/status 加欄位**

在 `/api/status` 回傳 dict 的 `"cloud_tts": bool(cloud_tts_engine.available()),` 下一行加：

```python
        "cloud_llm": bool(cloud_llm_engine.available()),
```

- [ ] **Step 7: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/test_app_cloud_llm.py -q`
Expected: PASS

- [ ] **Step 8: 更新 `server/CONTRACTS.md`**

在 `## llm.py` 段之後加入新段（緊接 EdgeLLM 契約描述後）：

```markdown
## cloud_llm.py

class CloudLLM:
    def available(self) -> bool
    def generate(self, student_text: str, scaffold: "ScaffoldResult", directive: str | None = None) -> str | None

雲端腦對話加值層（契約同 EdgeLLM）。端點/認證/model 由 `anthropic_relay.resolve_config()`
（`ANTHROPIC_*` env，與 diagnose 共用）解析，支援官方金鑰或自架中轉（`ANTHROPIC_BASE_URL`）。
`available()`＝憑證可解析；consent/network 由 pipeline 把關。`generate` 上雲前
`guardrails.deidentify` 去識別化學生文字，輸出過 `passes_guardrail`，目標句缺漏自動補；
任何失敗/逾時/護欄命中回 None。純 urllib、import 期不觸網。

pipeline 加值降級鏈：`network_mode=="cloud"` 且 `consent_granted()` → CloudLLM →（None/逾時）
EdgeLLM →（None）scaffold.reply_text。`/api/status` 多回 `"cloud_llm":bool`。
```

- [ ] **Step 9: 跑全套測試確認無退步**

Run: `./run_tests.sh`
Expected: PASS（全套綠，含 streaming/barge-in）

- [ ] **Step 10: Commit**

```bash
git add talkybuddy/server/app.py talkybuddy/server/CONTRACTS.md talkybuddy/tests/test_app_cloud_llm.py
git commit -m "feat(app): 注入 CloudLLM 到 pipeline + /api/status 回報 cloud_llm

全域與每連線 VoicePipeline 皆注入 cloud_llm_engine；CONTRACTS 補 cloud_llm.py 契約。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage：**
- §3 anthropic_relay 抽出 → Task 1 ✓
- §3 diagnose 改用共用 → Task 1 ✓
- §4 CloudLLM 契約（available/generate/deidentify/guardrail/補目標句） → Task 2 ✓
- §5 pipeline cloud→edge→scaffold + consent/network 閘 → Task 3 ✓
- §3 app.py 注入 + /api/status → Task 4 ✓
- §7 測試（relay/cloud_llm/pipeline/diagnose 整合） → Task 1–4 ✓
- §6 去識別化與護欄 → Task 2（deidentify、passes_guardrail）✓
- CONTRACTS.md 更新 → Task 4 ✓

**Placeholder scan：** 無 TBD/TODO；每個 code step 均含完整程式碼。

**Type consistency：**
- `resolve_config()` 回傳 `{"url","model","headers"}` 於 Task 1 定義，Task 2 CloudLLM 消費一致。
- `CloudLLM.generate(student_text, scaffold, directive=None)` 於 Task 2 定義，Task 3 pipeline 以 `engine.generate(result.asr_text, sc, self._directive)` 呼叫一致（EdgeLLM 同簽章）。
- `VoicePipeline(..., cloud_llm=None, ...)` 於 Task 3 定義，Task 4 app.py 注入一致。
- `/api/status` `cloud_llm` 欄位 Task 4 產生、測試一致。
```
