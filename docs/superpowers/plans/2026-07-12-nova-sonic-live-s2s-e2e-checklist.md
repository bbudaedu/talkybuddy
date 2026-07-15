# Nova Sonic 即時 S2S — 手動端到端驗收 checklist

> 定位：**live 為主 + 自動降級**。進頁讀 `/api/status`，`live_s2s=true` 且無 `sessionStorage.forceHalfDuplex`
> → 自動 `enterLiveMode()`（停喚醒常駐麥、不建 `/ws/talk`、連 `/ws/live`、hold-to-talk）；
> 否則走既有半雙工 `/ws/talk`。live 失敗 → 設 `forceHalfDuplex` + reload 自動降級。

## 前置
- [ ] `.venv` 已裝 `aws_sdk_bedrock_runtime` + `smithy_aws_core`
- [ ] 匯出短期 SigV4 憑證：`export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... BEDROCK_REGION=us-east-1`
- [ ] 啟動（麥克風需安全上下文，建議 HTTPS）：
      `cd talkybuddy && .venv/bin/python -m uvicorn server.app:app --host 0.0.0.0 --port <閒置埠>`

## 協定層（先用已證腳本確認帳號/憑證仍通）
- [ ] `AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... .venv/bin/python scripts/verify_nova_sonic_live.py docs/voice-reference/<中文語音>.wav`
      → 見 ASSISTANT 中文回覆 + audioOutput bytes > 0

## 能力旗標
- [ ] `curl -s localhost:<埠>/api/status` → `live_s2s: true`（憑證缺 → false）

## 瀏覽器端到端（live 為主）
- [ ] 開 `https://<host>:<埠>/` → **進頁自動進 live 模式**（toast「🎙️ 即時對話模式已開啟」、hint 顯示「按住企鵝說話」）
- [ ] 允許麥克風權限
- [ ] **hold-to-talk**：按住企鵝（penguin）不放 → 說一句中文 → 放開
      - 按住時：視覺切「我在聽～」（st-listen）、企鵝縮放 pressing
      - 放開時：送 `user_end`、視覺切「換我說囉！」（st-talk）
- [ ] 畫面出現 USER 逐字稿（student 泡泡）+ ASSISTANT 繁中回覆（penguin 泡泡），並**聽到**中文語音（企鵝學伴帶讀英文詞）
- [ ] 一輪結束（turn_end）→ 視覺回「待機中」（st-idle）
- [ ] 教師端 `/teacher` 或 `/api/interactions` 看到本輪 `asr_text`/`reply_text`（`source=live_s2s`）

## 自動降級（live → 半雙工）
- [ ] **live_error 降級**：`TALKYBUDDY_CONSENT_GRANTED=0` 重啟 → 進頁自動進 live → `/ws/live` 立即回 `live_error/consent_required`
      → toast「需要您同意錄音上雲…改用一般模式」→ 約 1.5s 後**自動 reload** → 因 `forceHalfDuplex` 旗標**強制走半雙工**
      → 半雙工正常（點企鵝 tap-to-toggle、喚醒詞、快速語句皆可用）
- [ ] **降級不循環**：上一步 reload 後旗標已清（`sessionStorage` 無 `forceHalfDuplex`），停在半雙工不再嘗試 live
- [ ] **連線中斷降級**：live 模式下中斷網路/伺服器 → `ws.onerror` → 同樣 toast「連線中斷…改用一般模式」+ reload 降半雙工

## 半雙工回歸（live_s2s=false 時）
- [ ] 不匯出 SigV4 憑證啟動 → `/api/status` `live_s2s=false` → 進頁**不進 live**、直接半雙工
- [ ] `/ws/talk` 行為完全不變（點企鵝、喚醒詞、快速語句、飛航模式切換）

## hands-free 全雙工（`?mode=continuous`，Phase 1）
> 進頁 `enterLiveMode` 只建立 `LiveSession({continuous:true})`（WS+getUserMedia+worklet 就緒）**不自動擷取**；點企鵝 toggle 開/關整場對話，turn 邊界交給 Nova server VAD，支援真 barge-in。
- [ ] 進頁自動進 live → hint「👆 點企鵝開始對話，再點一下結束」、toast「即時對話模式已就緒，點企鵝開始」；**麥克風尚未擷取**
- [ ] **點企鵝開始**：狀態切 `listen`（麥常開整場）；再點 → `stopConversation`、狀態回 `idle`、送 `bye`
- [ ] **連續多輪不需再按**：說一句 → 停頓 → Nova VAD 自動判定 turn 結束並回覆；接著再說下一句，無需任何按鍵
- [ ] **真 barge-in**：AI 講話中出聲 → AI **立即停播**（`_flushPlayback`）、狀態切 `listen`（server 送 `{type:"interrupt"}`）
- [ ] **靜默無自問自答**：AI 講長回覆時完全不出聲 → 不冒假 USER 氣泡、不自我打斷（AEC 過關實證）
- [ ] **乾淨關閉**：點結束或關頁 → 無殘留 `/ws/live` 連線、麥克風佔用釋放、伺服器 log 無 teardown InvalidStateError 噴發
- [ ] **降級安全網**：live 失敗 → 自動降半雙工（既有 hold-to-talk 路徑零改動、續用）

## 資源清理
- [ ] 降級 reload 後：live 的麥克風佔用（indicator）已釋放、無殘留 `/ws/live` 連線
- [ ] 驗完撤短期 SigV4 憑證
