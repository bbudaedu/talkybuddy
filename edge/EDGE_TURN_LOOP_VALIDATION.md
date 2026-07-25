# Edge Turn-Loop Validation — Genio 520 真機實測紀錄

**驗證日期：2026-07-25**
**板卡**：Hti Genio 520 evk（`192.168.31.78`，DHCP，Tailscale-routed SSH root）
**OS/kernel**：`Rity Demo Layer 25.1.1-release (scarthgap)`，`Linux genio-520-evk 6.6.92-mtk+g7a9a94d39e1d-g0f82689e1ac2 aarch64 GNU/Linux`（見 `edge/BOARD_BRINGUP_DECISION.md`）
**CPU**：6× Cortex-A55 + 2× Cortex-A78（big.LITTLE），無 `i8mm`，有 `asimddp`/`dotprod`
**llama.cpp**：交叉編譯工具鏈＝開發機 apt `aarch64-linux-gnu-gcc/g++`（D-03 預設路徑，Yocto SDK fallback 全程未觸發）；build flags `-march=armv8.2-a+dotprod -DGGML_NATIVE=OFF`（`+i8mm` 已於 `7f1fbbc` 拿掉，因真機缺該 ISA extension、曾 SIGILL）；真機 `llama-bench` 輸出的 build 字串：`build: 555881ebc (10121)`
**LLM 模型**：`qwen2.5-1.5b-instruct-q4_k_m.gguf`（1.04 GiB，1.78B 參數，Q4_K quant）
**Pipeline profile**：`TALKYBUDDY_PIPELINE_PROFILE=edge`（ASR：sherpa-onnx SenseVoice；TTS：sherpa-onnx en_US-lessac-medium / zh_CN-huayan-medium；LLM：llama-server 獨立行程）

以下四項全部為真機實際輸出，非模擬、非 PC 開發機數字替代。

---

## A) ELOOP-03：延遲（llama-bench 執行緒掃描 + 真回合端到端計時）

### llama-bench（`-p 128 -n 128 -t 1,2,4,6,8 -r 3`，真機執行）

| threads | pp128 (t/s) | tg128 (t/s) |
|---|---|---|
| 1 | 15.41 ± 0.03 | 7.36 ± 0.00 |
| 2 | 30.70 ± 0.00 | 12.25 ± 0.00 |
| 4 | 26.49 ± 0.00 | 10.50 ± 0.36 |
| **6** | **39.06 ± 0.00** | **12.35 ± 0.00** |
| 8 | 37.95 ± 0.02 | 11.73 ± 0.00 |

pp 與 tg 皆在 `threads=6` 最佳（threads=8 因 core 數超過大核心配置反而略降）。**選定 `--threads 6`**（`TALKYBUDDY_LLM_THREADS=6` env override，`server/config.py:148`），已在真機重啟 llama-server 套用並沿用至後續所有真回合實測。

### 真回合端到端計時（`local_client` → `/ws/talk`，收音結束→可播放回覆總延遲，`latency_ms` 由伺服器端回報）

| 回合 | 條件 | asr | llm | tts_first | round_total |
|---|---|---|---|---|---|
| 冷啟動#1（未暖身） | llama-server 剛重啟，prompt cache 空 | — | 7522ms | — | **10.03s** |
| 熱#2（同一行程，接續冷啟動#1） | prompt cache 已熱（同段對話 slot） | 203ms | 1834ms | 951ms | **2.99s** |
| 熱#3（穩定性複測） | 同上 | 202ms | 1739ms | 1018ms | **2.96s** |
| 熱#4（再複測） | 同上 | — | — | — | **2.96s** |

**根因**：llama-server 對同一 slot 的 prompt 前綴做 KV cache 重用（`cached_tokens` 命中 292/293）。冷啟動時整段 system prompt（≈293 token）要用 pp≈39 t/s 全部重算（293/39≈7.5s，與實測 7522ms 幾乎完全吻合）；一旦同一行程內已跑過一次，後續每輪只需重算新增的少量 token，延遲驟降到門檻內。

### 暖身 mitigation（`edge/runtime/warmup_llama_server.py`，本 plan 新增）

`run_edge.sh` 起 llama-server /health 就緒後，額外送一次假暖身 turn（同款 system prompt）把 KV cache 焐熱，再才 exec uvicorn（開機階段觀眾聽不到）。真機重跑冷開機驗證：

```
warming up llama-server prompt cache...
prompt eval time =  6499.37 ms /   248 tokens (38.16 tokens per second)
WARMUP_OK
INFO: Uvicorn running on http://0.0.0.0:8787
```

開機完成後，**立刻**觸發第一個真實回合（換一句不同於暖身文字的真實語音）：

```
llama-server log: selected slot by LCP similarity, sim_best = 0.771 (> 0.100 thold)
prompt eval time = 2050.32 ms / 67 tokens
eval time        = 2067.97 ms / 22 tokens
latency_ms: {'asr': 405, 'llm': 4170, 'tts_first': 1209, 'round_total': 5852}
```

第一句從 **10.03s → 5.85s**（進步 42%），但仍**超過 D-05 門檻**。

**殘餘缺口根因**：暖身只能把「system prompt」那段固定文字焐熱。但 `server/llm.py::EdgeLLM.generate()` 目前把「請照規則回覆：...」這段固定的回覆規則文字放在使用者訊息的**尾巴**（學生說的話之後）。學生實際講的內容每次不同（必然如此），KV cache 從那個字元起全部失效，不管後面接的是固定規則文字還是真變動內容——這是為什麼真回合仍要重算 67 token（2.05s）+ 生成 22 token（2.07s，此段生成時間跟回覆長度成正比、暖身無法省），加上 ASR 0.4s + TTS 1.2s。

### D-05 go/no-go 判定

- **穩態（同一 llama-server 行程內第二輪起，demo 進行中）**：**GO**（三次樣本 2.96–2.99s，皆 <3–4s 門檻，收斂穩定非單次僥倖）
- **冷開機第一句（暖身前）**：**NO-GO**（10.03s）
- **冷開機第一句（暖身後）**：仍 **NO-GO**（5.85s，較未暖身進步 42%，但未壓進 3–4s 門檻）

**結論／後續**：暫不接受風險——決賽現場流程建議在裝置開機、`run_edge.sh` 暖身跑完之後、正式讓觀眾使用前，**額外由主持人手動先講一輪暖場對話**（非觀眾第一句），把殘餘的 67-token 重算再吃掉一次；觀眾實際聽到的第一句即落在熱穩態 2.96–2.99s 區間內。結構性根治（把固定回覆規則文字挪進 system prompt 而非使用者訊息尾巴，理論可再省 1–1.5s）列為 Phase 9 待辦，不在本輪決賽前搶做（時間有限、需重新真機驗證，屬於錦上添花而非阻斷項）。

---

## B) ELOOP-04：記憶體（跨行程 VmHWM 加總）

量測時機：真回合進行中／剛結束（ASR 解碼＋LLM 生成＋TTS 合成重疊瞬間之後，VmHWM 為累計峰值不會下降），`threads=6` 重啟後的行程。

```
-- uvicorn (pid 3070) --
VmHWM:    673456 kB   (≈ 658 MB)
-- llama-server (pid 3074) --
VmHWM:   2114524 kB   (≈ 2065 MB)
```

**加總 ≈ 2723 MB**，對照 4096 MB（4GB）門檻，**餘裕 ≈ 1373 MB（33.5%）** — **PASS**

三次熱回合（2.99s / 2.96s / 2.96s）之間重複量測，VmHWM 加總維持不變（峰值已在冷啟動當下達到，穩態回合不會再往上推）。加總邏輯由 `edge/runtime/measure_peak_rss.py`（`read_peak_rss_kb` / `sum_peak_rss` / `within_threshold`，Task 1，11 條單元測試守護）提供，非人工心算。

---

## C) ELOOP-01 + success criterion 1：零雲端稽核

一輪真回合對話期間，於另一 SSH session 對裝置執行 25 秒 `tcpdump -nn -i any 'not port 22 and not net 127.0.0.0/8'`（排除 SSH 管理與 loopback）：

```
09:42:10.820662 end0 IP 192.168.31.239.5353 > 224.0.0.251.5353: mDNS _companion-link._tcp.local (58)
09:42:10.820784 end0 IP6 fe80::...5353 > ff02::fb.5353: mDNS _companion-link._tcp.local (58)
09:42:11.828872 end0 IP 192.168.31.239.5353 > 224.0.0.251.5353: mDNS (repeat)
09:42:14.836674 end0 IP 192.168.31.239.5353 > 224.0.0.251.5353: mDNS (repeat)
```

全部封包來源為 `192.168.31.239`（區網內**別的裝置**的 mDNS 廣播，`_companion-link._tcp.local`/`_rdlink._tcp.local`），板卡本身（`192.168.31.78`）在整個 25 秒視窗內**沒有任何對外封包**。

同步對照 `/api/status`：

```json
{"asr":true,"llm":true,"tts":true,"cloud_tts":false,"cloud_llm":false,"network_mode":"edge","pending":4,"live_s2s":false}
```

`network_mode:"edge"`、`cloud_llm:false`、`cloud_tts:false` — **PASS**（完整一輪聽 ASR → 想 LLM → 說 TTS 全在裝置本機完成，零雲端連線，經封包稽核證明，非僅程式碼審閱）

pcap 檔：`/tmp/turn_audit.pcap`（裝置端，本輪驗證證據留存）

---

## D) llama-server 對外綁定驗證（Open Question 2）

從開發端對裝置**外部 IP**（Tailscale-routed，非 loopback）發：

```
$ curl -sS -m 5 http://192.168.31.78:8080/health
curl: (7) Failed to connect to 192.168.31.78 port 8080 after ...ms: Couldn't connect to server
```

對照裝置**本機** loopback：

```
$ ssh root@192.168.31.78 "curl -sf http://127.0.0.1:8080/health"
{"status":"ok"}   (HTTP 200)
```

外部 IP 連線被拒（exit 7）、本機 loopback 回 200 —— **PASS**（llama-server 綁定 `127.0.0.1`，未外洩到區網/tailnet，`edge/runtime/run_llama_server.py` 的 `--host 127.0.0.1` 預設值符合設計）

---

## 總結

| 項目 | 結果 |
|---|---|
| A（延遲，穩態） | **GO**（2.96–2.99s，三次樣本收斂） |
| A（延遲，冷啟動第一句） | **NO-GO**（暖身前 10.03s → 暖身後 5.85s，仍超門檻；建議 fallback：決賽開場前由主持人先講一輪暖場對話） |
| B（記憶體） | **PASS**（加總 ≈2723 MB，餘裕 1373 MB / 33.5%） |
| C（零雲端） | **PASS**（tcpdump 25s 零對外封包 + `network_mode:edge`） |
| D（綁定） | **PASS**（外部 curl 被拒、本機 200） |

B/C/D 三項乾淨 PASS。A 項僅「冷開機第一句」殘留 NO-GO，根因已查明（固定回覆規則文字位於使用者訊息尾巴、暖身無法命中），已實作並驗證暖身 mitigation（進步 42%），結構性根治列為 Phase 9 待辦。決賽現場採用「暖場先講一輪」的操作性 fallback 可規避此殘餘缺口。
