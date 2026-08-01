# 派工提示詞：把 edge（Genio 520 / pipecat）接上 AWS 雲端

（整段貼給負責 edge 的 session）

---

專案 `/home/budaedu/talkybuddy-pipecat`（分支 `feat/pipecat-edge`），今天 2026-08-01 決賽。
edge 端 pipecat 已部署完成。任務：**把它接上今天已經建好並驗證過的 AWS 雲端資源**。

雲端那半（`/home/budaedu/talkybuddy`）今天已經全部打通並部署在 Fargate，
網址 `https://d1lh9vytcx1utq.cloudfront.net`。你要做的是讓 edge 用同一組 AWS 資源，
**不是重建一套**。

## 先讀這些記憶（同一台機器，auto-memory 已寫好）

`/home/budaedu/.claude/projects/-home-budaedu-talkybuddy/memory/`

- `project_agentcore_state_2026-08-01.md` — AgentCore 已 provision，四個 harness ARN、三個坑
- `project_aws_web_deploy_2026-08-01.md` — 雲端部署、espeak 啞掉、CloudFront 快取
- `project_tts_polly_findings.md` — Polly 合規判斷、中文無 zh-TW、英文童聲
- `project_two_prompt_contracts.md` — **兩套對話契約，接錯會讓玩偶鬼打牆**
- `project_edge_s2s_tuning.md`、`project_edge_deploy.md` — edge 既有的坑

## 憑證（會過期，過期就回報，不要自己想辦法繞）

`/home/budaedu/talkybuddy/.env.aws`（600 權限，被 .gitignore 擋住，**不要複製到 pipecat repo**）

```bash
set -a && . /home/budaedu/talkybuddy/.env.aws && set +a
aws sts get-caller-identity   # 應回 arn:aws:sts::953089054952:assumed-role/WSParticipantRole/Participant
```

帳號 `953089054952`、region **us-west-2**（規範第 6 條指定 us-east-1／us-west-2）。

## 已經建好、直接用的資源

**Bedrock**（對話已實測 1.1–1.6s）
- 對話：`global.anthropic.claude-haiku-4-5-20251001-v1:0`
- 診斷：`global.anthropic.claude-sonnet-5`
- ⚠️ **`sonnet-4-5` 兩個前綴都是 AccessDenied**（缺 marketplace 訂閱）。`list-foundation-models`
  列的是「存在」不是「已開通」——唯一可信的驗法是真的 `converse` 一次。`sonnet-4-6` 可用。

**AgentCore**（四個 harness 全 READY，已收到真實 InvokeHarness 回應）
ARN 都在 `.env.aws` 裡（`AGENTCORE_HARNESS_*`、`AGENTCORE_MEMORY_ARN`）。
`allowedTools` 已設空——不設的話預設 `["*"]`，模型會拿到微 VM 內建工具、跑去呼叫
`file_operations` 寫檔而不吐 JSON。

**Polly**（英文 `Ivy` 女童、中文 `Zhiyu`，都 neural）
`pcm` 只支援 8000/16000，**mp3/ogg_vorbis 才有 22050**。edge 全鏈 22050Hz，
`server/polly_tts.py` 的做法是取 pcm 16000 再線性重取樣，可以直接參考或重用。

## 一定要設的環境變數（少了會靜默壞掉）

```bash
export TALKYBUDDY_CLOUD_PROVIDER=bedrock   # 少了它 bedrock_converse.resolve_config() 一律回 None
export BEDROCK_REGION=us-west-2
export TALKYBUDDY_CONSENT_GRANTED=true
```

⚠️ **`scripts/aws_preflight.py` 的降級鏈顯示不可信**：它第 90 行
`os.environ.setdefault("TALKYBUDDY_CLOUD_PROVIDER","bedrock")` 之後才檢查，等於檢查自己剛塞的值，
所以永遠顯示 `agentcore → bedrock → rule`。真實環境沒設這個變數時鏈其實是
`agentcore → rule`，AgentCore 一掛就直接摔到規則式。**驗法是看
`agent_backends.chain(role)` 在該環境的實際輸出，不要看 preflight。**

## edge 特有的三件事，不要照抄雲端

**1. 孩子的語音不可以上雲。** 環境規範第 2 條第 9 類禁止把生物識別資料匯入 AWS。
edge 的 ASR **必須在板子本地做**，只把辨識後的**文字**送上雲。
Polly 同理——送文字、回音訊，不涉及生物識別，是合規的。
（也因此 **Nova Sonic S2S 不能用**：它要送孩子的原始語音上去。）

**2. 對話契約用哪一套要分清楚**（見 `project_two_prompt_contracts.md`）
- edge 的 llama-server 只有 512 ctx → 維持**回合式**單輪契約，塞歷史會擠掉 system prompt
- 走雲端（context 無 512 限制）→ 用 `generate_chat` + `scaffold.build_live_system_prompt`，
  帶對話歷史、`enforce_readalong=False`。今天雲端就是接錯這個才鬼打牆
  （「你喜歡什麼動物」問完下一輪又問一次），修法可參考 `talkybuddy/server/pipeline.py`。
- 半雙工玩偶要傳 `max_chars`（40）——玩偶講話時孩子麥克風是關的。

**3. 網路會斷。** 現場網路走上游手機熱點，斷線是常態（見 `project_genio520_hardware.md`）。
接雲端之後**本地降級鏈一條都不能拆**，而且要實際拔網測一次，不要只看設定。

## 驗收標準（做完請逐條回報實測結果，不要只說「已接上」）

1. `agent_backends.chain(role)` 對四個 role 都回 `['agentcore','bedrock','rule']`
2. 板子上真的收到一次 AgentCore 回應（不是設定讀數，是實際 invoke）
3. 一輪完整對話的延遲數字（ASR／LLM／TTS／round_total）
4. **拔網後仍能對話**，且降級是可觀察的
5. 記憶體：接雲端後的 RSS 增量（板子可用約 1797MB，llama-server 已佔 1290MB）

## 不要做的事

- 不要重建 AgentCore 資源（已經有了，重建會多一組孤兒資源）
- 不要把 `.env.aws` 複製進 pipecat repo（憑證會進版控風險）
- 不要為了接雲端而拆掉任何本地降級路徑
- 不要用 Nova Sonic S2S（見上）
- 決賽剩不到一天，**任何改動都要能快速回退**
