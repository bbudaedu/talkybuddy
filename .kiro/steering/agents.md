---
inclusion: fileMatch
fileMatchPattern: ["server/agents/**", "tests/test_agent_*.py"]
---

# Agent 模組設計契約

`server/agents/` 底下每個模組都必須遵守以下契約。這些不是風格偏好，
每一條都對應一個真實的失敗模式。

## 1. 推論一律經既有 provider，不直接碰 boto3

```python
from server import cloud_llm, bedrock_converse   # ✅
import boto3                                      # ❌ 絕對不要
```

理由：`cloud_llm` / `bedrock_converse` 已經處理好 region、model 分流、
逾時上界、Bedrock→relay 降級鏈。你自己開 client 會繞過全部這些，
並且在斷網橋段當場破功。

需要不同的 model 時用 `bedrock_converse.resolve_config(role=...)`，
不要硬編 model ID。

## 2. 絕不把例外拋進呼叫端

任何失敗（逾時、護欄命中、格式不符、無憑證）一律回 `None` 或規則式降級值。
比照 `server/cloud_llm.py::CloudLLM.generate` 的 `except Exception: return None`。

理由：pipeline 是即時語音迴圈，一個未捕捉的例外就是孩子面前的沉默。

## 3. 隱私閘門，三道都要

```python
safe = guardrails.deidentify(student_text)        # 上雲前去識別化
if not guardrails.consent_granted(): ...          # 家長同意
if not guardrails.passes_guardrail(text): ...     # 輸出後置護欄
```

## 4. 尊重 kill-switch

任何會出境的函式都要有 `allow_cloud: bool = True` 參數。
`network_mode == "edge"` 時呼叫端會傳 `False`，此時**連 resolve_config 都不要呼叫**，
直接走本地路徑。比照 `server/diagnose.py::generate_diagnosis` 的寫法。

這是斷網橋段的核心，漏一個就是現場出糗。

## 5. 每個 agent 都要有離線版本

雲端路徑失敗時要有規則式 fallback，且 fallback 的輸出格式必須與雲端版一致，
呼叫端不需要知道走的是哪條。比照 `diagnose._rule_based_diagnosis`。

## 6. 純函式優先，不要建繼承體系

一個 agent = 一個模組 + 幾個函式。不要 `BaseAgent` 抽象類別。
理由：鐵律 1 —— 保持 in-process 輕量，agent 數量還沒多到需要框架。

## 測試要求

- 檔名 `tests/test_agent_<名稱>.py`
- 用 monkeypatch 取代 `converse_text`，**不觸網、不需 AWS 憑證**
- 每個 agent 至少要有：正常路徑、雲端失敗降級、`allow_cloud=False` 不出境、
  護欄命中、去識別化生效 —— 五類測試
