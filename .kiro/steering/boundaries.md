---
inclusion: always
---

# 檔案邊界（最高優先，違反會毀掉別人的工作）

這個 repo **同時有三條線在同一個 worktree、同一個分支 `gsd/2-genio-520-edge-mvp`
上工作**。你（Kiro）是第三條。沒有分支隔離，只有檔案邊界。

## 你可以寫的檔案

| 路徑 | 說明 |
|---|---|
| `server/agents/**` | 你的主場。所有新 agent 模組都放這裡 |
| `tests/test_agent_*.py` | 你的測試，檔名一律 `test_agent_` 開頭 |
| `.kiro/**` | 你自己的 steering / spec / hooks |

## 你絕對不能碰的檔案

| 路徑 | 誰的 | 碰了會怎樣 |
|---|---|---|
| `edge/**`、`.planning/**` | 真機 edge 線 | 對方正在 Genio 520 上做 NPU 與斷網演練，改了會讓他的驗證作廢 |
| `server/pipeline.py`、`server/app.py` | 雲端線（Claude Code） | 這是整合點，由對方在你交付後接線 |
| `server/cloud_llm.py`、`server/diagnose.py`、`server/bedrock_converse.py`、`server/config.py` | 雲端線 | 正在做 Bedrock region/model 切換 |
| `deploy/**`、`scripts/**` | 雲端線 | AWS 部署與 preflight |

需要改上表任何一個檔案時：**不要自己改**。在你的交付說明裡寫清楚
「需要在 `server/pipeline.py` 的哪一行接上什麼」，由雲端線的人來接。

## Git 規則（沒有例外）

1. **絕對不可以 `git add -A` 或 `git add .`** —— 會掃到另外兩條線未完成的產出。
   一律逐檔 `git add <path>`。
2. **不可以切換分支、不可以 rebase、不可以 force push**。另外兩條線的 commit
   會落到錯的地方。
3. commit 前先 `git status --short` 確認暫存區只有你自己的檔案。
