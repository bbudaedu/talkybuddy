---
inclusion: always
---

# 檔案邊界（最高優先）

**2026-07-31 更新。** 先前這份文件寫的是「三條線在同一個 worktree、同一個分支上
工作」，因此把 `server/cloud_llm.py`、`server/config.py`、`deploy/**`、`scripts/**`
全列為禁區。**那個架構已經不存在了**——三條線（`gsd/3-offline-spelling-drill`、
`feat/pipecat-edge`、`gsd/path1-realwire`）已合併成單一主線。

禁區隨之縮小到只剩一個，而它的理由跟以前不同：不是「別人正在改」，是
「只有真機驗得出對錯」。

## 你可以寫的檔案

| 路徑 | 說明 |
|---|---|
| `server/**`（除下表外） | 雲端側全部開放：`cloud_llm.py`、`config.py`、`bedrock_converse.py`、`agents/**`、`pipeline.py`、`app.py` |
| `deploy/**`、`scripts/**` | AWS 部署與 preflight |
| `web/teacher.html` | 教師端（2026-07-31 使用者開放） |
| `tests/**` | 對應的測試 |
| `.kiro/**` | 你自己的 steering / spec / hooks |

## 你絕對不能碰的檔案

| 路徑 | 為什麼 |
|---|---|
| `edge/**` | **只有真機驗得出對錯的東西。** Genio 520 上的 pipecat pipeline、S2S client、ALSA 播放閘門、取樣率、播放時長計算——單元測試全綠也可能是壞的。`48cdff3` 是「aplay 取樣率寫死，真機一測就現形，單元測試永遠抓不到」；`1e04ea1` 是「玩偶會聽到自己」；`2294758` 是「aplay 在等待空檔 underrun 導致破音」。這三個沒有一個是測試抓到的。改它等於把唯一經過真機驗證的資產拿去賭。 |

需要改 `edge/**` 時：**不要自己改**。在交付說明裡寫清楚「需要在哪個檔的哪一行
改什麼、為什麼」，交給人處理。

## 硬約束：執行期不得有非 AWS 出境

決賽走「AWS 完整開發環境」路線，規範原文：

> 競賽僅限使用 Amazon Bedrock、SageMaker AI 所提供之基礎模型、Kiro，
> 及 AWS 相關雲端服務進行系統與功能建置

專案裡有三個**非 AWS** 後端，都能跑、也都驗過真機，但競賽期間一律不准用：

    server/gemini_llm.py       → generativelanguage.googleapis.com
    server/anthropic_relay.py  → api.anthropic.com
    server/cloud_tts.py        → api.elevenlabs.io

它們由 `server/aws_only.py` 的閘門擋著（**預設開啟**）。要新增任何雲端能力，
一律走 `server/bedrock_converse.py`。

**不要為了讓某條路徑跑起來就繞過那個閘門。** 那是失格風險，不是技術債。
閘門刻意擋在兩個地方（`configured_backend()` 與 `generate_from_prompt()`），
因為兩者各自獨立解析設定——只繞過其中一個，畫面會顯示合規而封包照樣出境。

## Git 規則

先前禁止 `git add -A` 是因為三條線共用工作區，該限制隨合併解除。但仍然：

1. commit 前跑 `git status --short` 看一眼暫存了什麼。
2. 不要 rebase 或 force push 已推送的分支。
3. 一個 commit 一件事，訊息寫「為什麼」不是「做了什麼」。
