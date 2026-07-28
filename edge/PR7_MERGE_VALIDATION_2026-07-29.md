# PR #7 合併驗證（2026-07-29，Genio 520 真機）

合併 commit：`e3ba31d`。決策依據不是推測，是下面這幾組實測。

## 一、為什麼決定合併（推翻了「決賽前不宜引入」）

HANDOFF 的待確認第 2 項——「雲端模式下同一句話回什麼」——是最便宜、資訊量最大的
測試，這次做了：

| network_mode | 對「可以教我一些動物用法嗎」的回覆 | round_total |
|---|---|---|
| cloud | 「剛才你說：「可以教我一些動物用法嗎」<br>**跟我說一遍：How are you today?**」 | 11,203 ms |
| edge | 「你可以教我一些動物用法嗎？<br>**跟我說一遍：How are you today?**」 | 3,460 ms |

**兩個模式都被固定尾巴綁住**，證實與 `network_mode` 無關 → PR #7 的
`lesson_target_sentence` 是唯一修法，必要性成立。

而且回覆品質比 HANDOFF 記載的更差：HANDOFF 記的三個來源至少還回應了「動物」
（「我們可以學動物的用法哦」），這次兩個模式都只是**鸚鵡學舌複誦提問**。

## 二、修復不依賴診斷資料（HANDOFF 待確認第 3 項，就此結案）

先前未確認的關鍵前提：PR #7 靠 `_ensure_lesson()` 讀今日課程，**裝置若沒有診斷
資料會不會退回原本那句？**

```
build_lesson([])  →  topic='animal'  target_sentence='I see a dog.'
```

`build_lesson` 在無診斷時退回 `curriculum.TOPIC_ORDER[0]`（= animal），再從
`scaffold.VOCAB` 挑該主題的句子。**不需要任何診斷資料就能生效。**

同一句話的 scaffold 回覆：

| | 回覆 |
|---|---|
| 合併前 | 我聽到了！我們一起用英文說說看，跟我唸：**How are you today?** |
| 合併後 | 我聽到了！我們一起用英文說說看，跟我唸：**I see a dog.** |

## 三、產品規則合規率（`PROMPT_ORDERING_FINDING.md` 要求的驗收）

那份文件的教訓：prompt/scaffold 一改動就必須逐條檢查三條核心規則，因為快取面的
改善可能悄悄破壞行為面（該次稱讚整句消失，5/5 掉到 0/5）。

5 組輸入 × 2 輪，edge 模式真機：

| 輪次 | 規則一 中文稱讚 | 規則二 帶讀格式 | 規則三 目標句完整 |
|---|---|---|---|
| 第 1 輪 | 5/5 | 4/5 | 4/5 |
| 第 2 輪 | 5/5 | 5/5 | 5/5 |
| **合計** | **10/10** | **9/10** | **9/10** |

第 1 輪的兩個失分是同一則回覆（純英文輸入 "I like apples"）：

```text
很好！我們來嘗試說一遍：<What animal do you like?>
```

第 2 輪同一句輸入正常。**這是 temperature 0.7 的隨機性，不是系統性破壞**——與
PROMPT_ORDERING 那次「5/5 穩定掉到 0/5」性質完全不同，故判定通過。

### 順帶抓到兩個既有的護欄漏洞（非 PR #7 引入，`llm.py:152` 的比對邏輯）

1. **`<>` 包裹逃過比對**：護欄用 `target not in text` 判斷，而
   `"What animal do you like?" in "<What animal do you like?>"` 為真，
   所以帶讀格式跑掉了也不會被補正
2. **中文句號造成重複帶讀**：LLM 回「I want to eat an apple**。**」（中文句號），
   護欄認為 target（英文句點）不存在而再補一次，回覆變成
   「跟我說一遍：I want to eat an apple。 跟我說一遍：I want to eat an apple.」

兩者都建議用正規化後再比對修掉，**但不在本次合併範圍**，列為候選。

## 四、延遲對照：PR #7 不是延遲的原因

合併後量到 `llm` 4.4–5.1 秒，比先前記錄的 2,291ms 慢一倍，因此把合併前的
`server/` 推回裝置跑同樣 4 輪做乾淨對照（同一條連線、同樣輸入、同樣 edge 模式）：

| 輪次 | 1 | 2 | 3 | 4 |
|---|---|---|---|---|
| 合併**後** `llm` | 4,442 | 4,396 | 4,891 | 5,056 ms |
| 合併**前** `llm` | 4,740 | 4,185 | 4,095 | 4,001 ms |

**同一區間，PR #7 沒有造成退步。** `_ensure_lesson()` 也不是每輪成本（第 2 輪
之後沒有變快，代表慢的是 LLM 本身而不是課程載入）。

### ⚠️ 但這組對照暴露一個要上台的數字對不上

**edge 回合實測 `round_total` 4.8–6.3 秒，而 Phase 8 記載的是 2.96–2.99s。**
合併前後都一樣慢，所以與 PR #7 無關，但**簡報若引用 2.96s 會與現場實況不符**。

一個未驗證的線索：目前裝置上的 `llama-server` 是 `--threads 4`，而
`PROMPT_ORDERING_FINDING.md` 的量測是 `threads=6`。**未經實測，不得當結論。**

## 五、四處合併衝突的解法

| 檔案 | 解法 | 理由 |
|---|---|---|
| `llm.py` | 取 HEAD（HTTP 架構） | PR #7 的鎖是為 in-process llama.cpp 寫的；Phase 8 後 EdgeLLM 改走 HTTP 打獨立 `llama-server` 行程，本行程內沒有共用 native context。PR #7 對 ASR/TTS 單例的同類修復**仍然適用並保留** |
| `pipeline.py` 遊戲 vs 課程 | 兩者都保留 | 遊戲進行中提前 return，與 `scaffold.respond` 的課程參數不互相干擾 |
| `pipeline.py` `_webm_to_wav` 區 | 兩邊都保留 | 各自新增（HEAD 的 WAV fast path + PR #7 的 `_extract_fallback_prompt`） |
| `pipeline.py` 背景刷新例外 | 取 HEAD 的 `_log.exception` | 不取 PR #7 的無聲 `pass`——導師層在現場悄悄停更、畫面照跑、沒人會發現 |

## 六、測試

全套 **999 passed**（合併前 983，PR #7 帶入 16 條新測試全過）＋ streaming 18 passed。
