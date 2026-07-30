# 調查：雲端大腦是死的，而 `/api/status` 說它好好的

**日期**：2026-07-30
**發現途徑**：重測 `round_total` 時的異常數字，不是主動去查的
**影響**：直接打到「AWS 雲端多 agent 生態」這個決賽主軸

---

## 一句話

裝置在 `cloud` 模式下跑的**一直是邊緣 Qwen 1.5B**。雲端 LLM 每一輪都
`ConnectionRefusedError` 後靜默降級，而 `/api/status` 一路回 `cloud_llm: true`、
`cloud_provider: "relay"`。

---

## 怎麼發現的（方法比結論重要）

原本只是要重測 `round_total`（交接文件記 4881ms、超過 D-05 的 3–4 秒門檻）。
為了分辨「改善是雲端 TTS 的功勞還是別的變因」，加了 edge 模式當對照組
（`edge/probes/probe_latency_cloud_vs_edge.py`）。

裝置實測，暖機後中位數（已排除第 1 輪冷啟動），n=4：

| 模式 | round_total | llm | tts_first |
|---|---|---|---|
| edge | 5025ms | 3859ms | 1163ms |
| cloud | 4685ms | **3860ms** | 782ms |

**兩種模式的 `llm` 是 3859 vs 3860ms，而且第 3 輪都剛好 4178ms，一毫秒不差。**

一毫秒不差不可能是巧合——如果 cloud 模式真的走了不同的後端，數字不可能這樣重合。
這是唯一的線索，數字本身看起來完全正常。

> 教訓：**對照組不只是為了證明改善，也是為了讓異常現形。** 如果只量 cloud
> 模式，4685ms 看起來就只是「還是有點慢」，不會有人去查。

## 根因

```
journalctl -u talkybuddy-server | grep -A 12 "CloudLLM generate 失敗"

CloudLLM generate 失敗，降級回 edge/scaffold
ConnectionRefusedError: [Errno 111] Connection refused
```

```
ANTHROPIC_BASE_URL = http://127.0.0.1:8317
解析出的端點      : http://127.0.0.1:8317/v1/messages
model             : claude-sonnet-5
```

```
ss -ltn | grep 8317   → 沒有任何行程在聽 8317
```

端點指向**裝置本機**的一個中轉服務，而裝置上根本沒有那個服務。
`server/cloud_llm.py::_TIMEOUT_S` 是 1.5s，但連線是**立即被拒**（不是逾時），
所以每輪只多花幾毫秒就降級 —— 從延遲數字上完全看不出異常。

## 為什麼沒有人發現

`/api/status` 的 `cloud_llm` 來自 `available()`，而 `available()` 只檢查
**設定齊不齊全**，不檢查**跑不跑得動**。

這與同日修掉的 `cloud_tts` 假綠燈是**同一個病**（commit `6fc96d7`）：

- 金鑰/端點設好 → `available()` 回 true → status 綠燈
- 實際每次呼叫都失敗 → 靜默降級 → 使用者聽到的是邊緣品質
- 自檢說綠燈，所以沒有人去查

`cloud_tts` 的修法可以直接照抄（`server/cloud_tts.py`）：
`available()` 維持便宜的設定閘門（它在 `pipeline._synth_tts` 熱路徑上），
另外用 `verified()` / `status_detail()` 依**最近一次實際呼叫的結果**回報。

> 註：`server/cloud_llm.py` 與 `server/app.py` 在 2026-07-30 已有未 commit 的
> 修改朝這個方向做（`verified()` / `verified_backend()` / `status_detail()` /
> `configured_backend()` 已存在）。接手前先 `git status` 確認狀態。

## 對 demo 的殺傷力

決賽當天若現場開 `/api/status` 佐證「大腦在雲端」，它會回 `cloud_llm: true`、
`cloud_provider: "relay"` —— 而實際上每一輪都跑本機的 Qwen 1.5B。
**說詞會被自己的 API 打臉。**

`server/app.py` 原本的註解寫得很清楚，這個欄位的用途就是
「現場靠這個欄位當場佐證『大腦在 Bedrock』」。

## 要決定的事

雲端大腦走哪條路：

1. **接 Bedrock** —— `server/bedrock_converse.py` 已存在，最符合 AWS 主軸
2. **真的把 relay 架起來** —— 但那是本機中轉，現場說服力等於零
3. **直連 Anthropic API** —— 需要 `ANTHROPIC_API_KEY`，但與 AWS 主軸不符

如果主軸是 AWS，(1) 是唯一說得通的選項。

## 附帶：這也是 round_total 的主要槓桿

`round_total` 由 `llm`（約 3.9s）主宰，`tts_first` 只佔約 0.8–1.2s。
2026-07-30 修好雲端 TTS 只省下約 380ms（4881ms → 4685ms）。

**D-05 的 3–4 秒門檻在邊緣 Qwen 1.5B 的速度下不可能達到。**
雲端 LLM 一旦真的接通，這個數字才有機會進門檻——所以這不只是「說詞問題」，
也是延遲問題。

## 還有一個相關疑點（未查）

同一批 log 裡出現過一次：

```
EdgeLLM generate 失敗，降級回 scaffold 回覆
```

也就是連邊緣 LLM 都失敗過一次，回了罐頭句。發生頻率與原因未查。
如果現場中獎，玩偶會講出一句與情境無關的預設回覆。

## 重現方式

```bash
# 開發機，對著裝置跑（會自動把 network_mode 還原成 edge）
.venv/bin/python -m edge.probes.probe_latency_cloud_vs_edge
```
