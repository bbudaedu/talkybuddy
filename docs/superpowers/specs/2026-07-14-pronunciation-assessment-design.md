# 發音評測（真聲學）設計

日期 2026-07-14。分支 `feat/cloud-llm-bedrock`。前置＝`spike/pron_assess/SPIKE-RESULT.md`（路 A GO）。

## 問題

`diagnose.py:141` 的 pronunciation 是**假分數**（asr_confidence 平均 × 100），不是真發音品質。要換成本地聲學評測，定位 B 軸背景診斷層（不進即時路徑）。

## Spike 結論（已定案）

**選路 A**：`vitouphy/wav2vec2-xls-r-300m-timit-phoneme`（vocab＝IPA）+ g2p_en（ARPAbet）→ 一張 39 條 `ARPA_TO_IPA` 靜態映射同源比對 + CTC 逐 id 解碼 + edit-distance 命中率。純 Python、零系統二進位。實測正確 ref 80 / 全錯 11、差距 69.7、且分數隨語音距離單調遞減。路 B（espeak-ng，GPL-3.0）鑑別力並列（68.1）但依賴更重，留後備。

## 模組契約：`server/pronunciation.py`

降級安全、import 期不載重依賴、絕不拋例外進呼叫端。

```
available() -> bool
    # torch/transformers/g2p_en/nltk 可 import 才 True；lazy 探測、任何 Exception → False

score(wav_path: str, reference_text: str) -> float | None
    # 回 0-100 命中率；模型不可用/音檔壞/reference 空 → None（呼叫端保留舊行為）
    # 模型單例懶載入（首次 ~數秒），之後重用；CPU
```

內部純函式（可獨立單元測試、免模型）：
- `_g2p_to_ipa(text) -> list[str]`：g2p_en → ARPAbet 去 stress → `ARPA_TO_IPA`。
- `_ctc_collapse(ids, id2tok, pad_id) -> list[str]`：連續同 id 折疊、去 PAD、濾非音素 token。
- `_align_score(ref, hyp) -> float`：edit-distance 命中率 0-100。

## 接線設計（決策：`/ws/live` Nova Sonic ＝主線，2026-07-14 用戶拍板）

**主線／回退（用戶 2026-07-14 確認）**：**主線＝`/ws/live` 的 turn-based 子模式**（`drain_events`，有明確 turn 邊界 / `user_end`）；**回退＝(a) continuous barge-in 全雙工 `_downlink`、(b) 傳統 `VoicePipeline` 半雙工 `/ws/`**。兩 live 子模式都觸發 `turn_end` → `_store_live_turn`，設計可共用；但**主線 turn-based 有乾淨 turn 邊界，PCM 緩衝實作先以它為準，不需處理 barge-in 打斷的半句**（continuous 回退再議）。

**路徑事實**：
- `VoicePipeline`（`/ws/`，app.py:308）：每輪有本地 wav 檔，但＝回退路徑。
- `/ws/live`（app.py:380）＝ `NovaSonicSession` S2S 串流，主線。原始 PCM 經 `_uplink`（app.py:449 `msg["bytes"]` → `session.send_audio`）串上雲，本地**無逐輪 wav 檔** → 需 tee 緩衝。

**reference 來源**：live session 啟動時的 `target`（app.py:419 `build_live_system_prompt(target, directive)`）＝該場跟讀目標句，全程已知。

**全雙工接線方案（tee PCM + 背景評分，不擋串流）**：
1. `/ws/live` handler 內維護 per-utterance PCM 緩衝 `pcm_buf: bytearray`。`_uplink`/`_downlink` 收到 `msg["bytes"]` 時，送 Nova 的同時 tee 一份 append 進 `pcm_buf`（純記憶體、O(1)，不擋 `send_audio`）。
2. Nova 上行音訊格式需確認（預期 16kHz mono PCM16）；緩衝即原始 PCM frames。
3. `turn_end`（Nova VAD 判定）時：若 `target` 存在且 `pronunciation.available()` → `asyncio.create_task` 背景把 `pcm_buf` 用 `wave` 組成 16kHz mono 暫存 wav → `pronunciation.score(wav, target)` → 分數帶進該輪 `_store_live_turn` → 評完刪 wav。清空 `pcm_buf`。
4. `_store_live_turn`（app.py:561）加選填 `pron: float | None`；有值時寫 `scores["pronunciation"] = pron`（目前 `scores` 是 `{}`）。
5. `diagnose._compute_scores`：per-interaction `scores` 內有真 `pronunciation` 時，pronunciation 維度改用這些真分數平均，取代 asr_confidence 映射（無則維持現行 fallback、向後相容）。

**時序坑**：`turn_end` 事件是 `_store_live_turn` 同步寫入點，但評分是背景 async；需讓評分在寫 interaction 前完成，或改成「評分完成後再寫該輪 interaction」。實作時擇一（建議 turn_end 收集完 transcript 後 `await` 一個有逾時上限的評分、逾時則 pron=None 照寫），避免 interaction 與分數脫鉤。

**降級**：`pronunciation.available()` False → 完全不 tee、不評分，行為與現行全雙工一致。任何評分例外吞掉、pron=None、照常寫 transcript。

**隱私**：本地評分、PCM 緩衝與暫存 wav 評完即刪、只留 0-100 分數（PRIVACY.md、B4 用後即刪）。

**備選（未採用）**：半雙工 `VoicePipeline` 接線（`_pending_reference` + `run_turn_audio` 背景評分後才 unlink + `_recent_pron` deque）。若日後回合制/降級模式也要發音評測再補。

## TDD 測試計畫

`tests/test_pronunciation.py`（純函式，快、免模型）：
1. `_align_score`：全等→100、全異→低、部分命中單調。
2. `_g2p_to_ipa`："I want to eat an apple." → 含 `æ p l` 等、全在模型 vocab 內。
3. `_ctc_collapse`：假 ids（含連續重複 + PAD）→ 正確折疊去重。
4. `available()` 回 bool 不拋。
5. （選、標 slow）整合：`score(clean_en_apple.wav, 正確)` ≥ `score(..., 全錯)` + 20。用 spike fixture。

`tests/test_pipeline.py` 增修（stub pronunciation，免模型）：
6. 有 pending_reference + available stub 回固定分 → `_recent_pron` 有值、wav 最終被 unlink。
7. available False → 行為與現況一致（立即 unlink、無背景任務）。

`tests/test_diagnose.py` 增修：
8. `generate_diagnosis(..., pron_scores=[90,90])` → pronunciation 反映真分數而非 asr_confidence。

## 分階段（可回退）

- **階段 1（本次）**：`server/pronunciation.py` + 純函式單元測試。**不動既有 runtime**。
- **階段 2（需審核）**：pipeline 接線 + diagnose 參數 + 對應測試（碰即時路徑，獨立 commit）。
