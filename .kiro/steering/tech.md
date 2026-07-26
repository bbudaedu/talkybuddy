---
inclusion: always
---

# 技術棧與工作方式

## 棧

- Python 3.12，FastAPI + uvicorn，SQLite（`server/store.py`）
- 測試：pytest。跑法 `.venv/bin/python -m pytest tests/ -q`
- 目前基準：**431 passed**。你的變更不可以讓這個數字往下掉。

## 三鐵律（2026-07-08 使用者拍板，延續至今）

1. **不引入 Hermes / LangGraph / AutoGen 等外部 agent 框架。**
   編排一律用 in-process 輕量控制器，延伸既有 pattern。
2. **所有雲端行為都走 `network_mode == "cloud"` 分支**，斷網或逾時一律降級回
   本地路徑。這是決賽現場的保命線。
3. **唯一交界是 `pipeline._process_text`。** 音訊層（全雙工、喚醒詞）完全不碰。

## 寫程式的規矩

- **TDD，沒有例外**：先寫失敗測試 → 看它以正確理由失敗 → 才寫實作。
- **註解用繁體中文（台灣用語）**，程式碼識別字、CLI、套件名保留英文。
  註解要寫「為什麼」而不是「做了什麼」，比照 `server/cloud_llm.py` 的密度。
- 小而可回退的變更，不做無關重構。
- 行為有變更就補測試；無法補測試至少要寫出手動驗證步驟。

## 誠實回報

不要宣稱沒驗證過的事。測試沒跑就說沒跑，跑失敗就貼輸出。
現在帳號的 Bedrock 還在 AWS 驗證中，**任何真打雲端的驗證都做不了** ——
遇到這種情況就明確標示「未驗證」，不要假裝通過。
