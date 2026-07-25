# Phase 9: Network-Cut Demo Hardening - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-07-25
**Phase:** 9-network-cut-demo-hardening
**Areas discussed:** 斷網觸發方式, 主持人操作介面

---

## 斷網觸發方式

### Q1: 現場「主持人 kill-switch」實際上指的是什麼動作？

| Option | Description | Selected |
|--------|-------------|----------|
| 純軟體 toggle | 沿用/改造現有 /api/network_mode 飛航模式開關，主持人點一下即切雲端。舞台上最穩定、不依賴現場 Wi-Fi 硬體，不會發生「拔了線網路不回來」的風險。 | ✓ |
| 真實實體斷網 | 主持人現場真的關 Wi-Fi AP 或拔網路線，軟體主動偵測這件事並降級。對評審最真實，但現場風險高。 | |
| 兩者並存 | 軟體開關為主控、現場也真的斷網，兩邏輯並行。實作量最大。 | |

**User's choice:** 純軟體 toggle（Recommended）
**Notes:** 舞台穩定性優先於「真實斷網」的戲劇效果。

### Q2: 除了主持人手動按鈕外，要不要再加一層「現場 Wi-Fi 真的斷線時自動降級」的保險網？

| Option | Description | Selected |
|--------|-------------|----------|
| 不做，純信任手動 | 既然已選純軟體 toggle，邏輯已完全確定（開關=一定不打雲端），不需要額外偵測邏輯，實作量最小、行為最可預測。 | ✓ |
| 加一層輕量保險 | 雲端呼叫真的連不上時也能自動降級不卡住，但不改變顯示的 network_mode 狀態。多一層保險但也多一塊邏輯要實作。 | |

**User's choice:** 不做，純信任手動（Recommended）
**Notes:** 第一次提問回應被取消（cancelled），重試一次後取得回答，結果不變。

### Q3: 若主持人恰好在雲端 LLM/TTS 請求進行到一半時按下斷網開關，這輪要怎麼續接？

| Option | Description | Selected |
|--------|-------------|----------|
| 縮短逾時自然降級 | 不做取消機制，只把雲端 LLM/TTS 逾時從目前 8s/6s 大幅縮短（如 1–2s），遇斷線很快就逾時降級到 edge。實作簡單，不碰 asyncio 取消競態風險。 | ✓ |
| 立即中斷重跑 | 主持人一按開關，立即 cancel 那筆雲端請求並馬上改走 edge 重跑這輪。反應最快，但需新寫 asyncio 取消/重跑邏輯。 | |

**User's choice:** 縮短逾時自然降級（Recommended）
**Notes:** 與 ROADMAP 既有「恢復時間 <1–2 秒」門檻對齊，避免新增取消/重跑機制的風險。

---

## 主持人操作介面

### Q1: 既有飛航模式按鈕（web/index.html 的 airplaneSwitch）學生也能點。現場主持人要怎麼操作這個開關？

| Option | Description | Selected |
|--------|-------------|----------|
| 直接沿用現有按鈕 | 主持人就是在學生畫面上點那顆飛航模式開關（或站在孩子旁邊代為操作）。不新增 UI，實作量最小。 | ✓ |
| 另開主持人專用頁面/區塊 | 做一個小朋友平常看不到、摸不到的別路徑來操作斷網，防誤觸，但需新寫頁面/路由。 | |
| 鍵盤快捷鍵 | 主持人的裝置上用固定快捷鍵觸發斷網，完全不在學生畫面上顯示控制項，但需新寫鍵盤事件監聽。 | |

**User's choice:** 直接沿用現有按鈕（Recommended）
**Notes:** 第一次提問回應被取消（cancelled），重試一次後取得回答，結果不變。

### Q2: 當場小朋友也可能碰到這顆按鈕。需不需要額外防誤觸機制？

| Option | Description | Selected |
|--------|-------------|----------|
| 不需要，保持單點即切 | 維持現狀行為（單點即切，不彈確認框）。現場主持人全程控場，不需額外實作；錯點也只是再點一次切回來。 | ✓ |
| 加一次確認/長按才觸發 | 防小朋友不小心單點切到雲端（或回到雲端），需新寫確認 UI 邏輯，但多一道手續可能影響舞台節奏。 | |

**User's choice:** 不需要，保持單點即切（Recommended）
**Notes:** 無

---

## Claude's Discretion

- 縮短後的具體逾時數字（1s / 1.5s / 2s 等）留給 planner/executor 依實測結果決定。
- `/api/status` 與教師儀表板 5 秒輪詢是否屬於 NETCUT-02「背景輪詢暫停」範圍，留給 executor 依程式碼確認（掃描本輪未發現額外背景雲端輪詢器）。
- 飛航模式既有 toast 文案/徽章視覺的微調幅度，交由 executor 依既有風格判斷。

## Deferred Ideas

- 真實實體斷網（拔線/關 AP）作為 kill-switch — 若彩排發現純軟體 toggle 說服力不足可回頭評估。
- 主持人專用操作介面（獨立頁面/路由或鍵盤快捷鍵）— 若彩排時小朋友頻繁誤觸可回頭補強。
- 斷網視覺呈現的戲劇化改版（大徽章/全螢幕狀態轉場）— 使用者本輪未選擇討論此主題，維持現況小徽章。
- 自動網路偵測安全網 — 若觸發方式決策未來改為實體斷網，需重新評估此項。
