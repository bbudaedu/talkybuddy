# 真機驗證腳本（2026-07-29 session 產出）

這些是對 Genio 520 跑的**可重跑驗證工具**，不是測試（測試在 `tests/`）。
它們打真裝置的 HTTP/WebSocket，所以需要裝置在線。

**開發機執行**（不是在裝置上跑）：

```bash
.venv/bin/python edge/probes/<script>.py
```

裝置位址寫死在各腳本的 `HOST = "192.168.31.78:8787"`——**IP 是 DHCP 配發的，
換網路環境要改**。

| 腳本 | 用途 | 需要隧道 |
|---|---|---|
| `probe_game_trigger.py` | i_spy 開局後在 `/ws/talk` 講話會不會走遊戲判定（發現 bug 的那支） | 否 |
| `probe_all_games.py` | 另外兩個遊戲（guess_who / restaurant）的觸發 | 否 |
| `probe_offtopic.py` | 同一句離題提問在 cloud 與 edge 的回覆對照 | 是（測 cloud 時） |
| `probe_compliance.py` | `PROMPT_ORDERING_FINDING.md` 要求的 5 組 × 3 規則合規率 | 否（edge 模式） |
| `probe_latency.py` | 同一條連線連續 4 輪的延遲，判斷是否為冷啟動成本 | 否 |
| `probe_netcut.py` | 斷網彩排 API 版：型態 A × 2 + 型態 B × 1 | **是** |
| `probe_netcut_b.py` | 型態 B 真實版（回合中切斷隧道製造雲端無回應）—— **尚未成功執行過** | **是** |

## 反向隧道（雲端路徑的前置）

裝置 `.env` 的 `ANTHROPIC_BASE_URL=http://127.0.0.1:8317` 指向反向隧道。
**沒建隧道時 `cloud_llm` 仍回報 `true`，但 `generate()` 會在 26ms 內
Connection refused**——雲端從頭到尾沒被呼叫過，演練會量到假結果。

```bash
# 開發機建隧道（背景）
ssh -N -o ServerAliveInterval=15 -o ExitOnForwardFailure=yes \
    -R 127.0.0.1:8317:192.168.100.200:8317 root@192.168.31.78 &

# 從裝置確認真的通了（要 200；000 = 沒人在聽）
ssh root@192.168.31.78 'curl -s -m 3 -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8317/'
```

## 其他檔案

- `memory-eval.html` — 長期記憶架構評估報告原始檔
  （已發布：https://claude.ai/code/artifact/0a48b597-7490-41c9-9355-c1d7148eb9a9）
- `PROMPT-path1.md` — 給另一個 session 接通 Path 1 barge-in 的提示詞
