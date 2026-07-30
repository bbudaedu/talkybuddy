# 邊緣裝置開機 SOP（照做即可，不要再 debug）

**Genio 520 開機後只有一個手動步驟，然後跑一行自檢。** 其餘全部已固化。

> 這份文件的存在理由：2026-07-30 一整輪除錯下來，真正吃掉時間的都不是難題，
> 而是「有沒有做某個前置動作」無法一眼確認。所有能自動化的都已寫進 systemd 與
> `provision_device.sh`，剩下不能自動化的只有第 1 步。

---

## 開機後照這樣做

### 1. 按一次 USB 麥克風的實體靜音鍵 ← **唯一的手動步驟**

重開機後它會回到靜音，而且**軟體偵測不到也控制不了**。沒按的話玩偶收不到音，
症狀是「按了、講了，但沒反應」，看起來很像壞掉。

### 2. 跑自檢（含收音測試）

```bash
ssh root@<裝置IP>
cd /root/talkybuddy
./.venv/bin/python -m edge.runtime.preflight --mic
```

看到 `--mic` 的提示後**對麥克風說話 3 秒**。全綠就能直接 demo：

```
✅ talkybuddy-server        active + 開機自啟
✅ talkybuddy-local-client  active + 開機自啟
✅ /api/status              asr/llm/tts 就緒
✅ 觸發鍵                    KEY_POWER(116) 短按，logind 已放手
✅ 按鍵節點                   /dev/input/event1
✅ ALSA 裝置                 錄音 plughw:1,0／播放 plughw:0,0
✅ 記憶體                    可用 1781MB
✅ 麥克風收音                 peak=0.31，人聲頻段 62%
```

**任何 ❌ 的那一行都會直接寫出要執行的指令**，不必翻文件。

### 3. 暖場一輪（不可略）

冷啟動第一輪 KV cache 全空，`round_total` 會落在 4.5–5 秒（超過 D-05 的 3–4 秒門檻）。
**先隨便講一輪暖機**，穩態才會回到 ~3.3 秒。冷啟動數字不得與穩態混算。

### 4. 開始 demo

**短按 power 鍵 → 講話 → 聽 3.5mm 喇叭回答。** 不要按住不放（見下方風險）。

---

## 開機會自動生效的（不用管）

| 項目 | 機制 |
|---|---|
| `uvicorn` + `llama-server` | `talkybuddy-server.service`（`enabled`，`Restart=always`）|
| 對話迴圈 `local_client` | `talkybuddy-local-client.service`（同上，ALSA 裝置寫在 unit 內）|
| power 鍵不觸發關機 | `/etc/systemd/logind.conf.d/10-talkybuddy-powerkey.conf`（drop-in，持久）|
| ALSA mixer（`Lineout` 音量等） | `alsactl store` → `/var/lib/alsa/asound.state`，`alsa-restore.service`。**2026-07-30 已實測撐過重開機** |
| 崩潰自動復活 | 兩個 unit 的 `Restart=always` |

服務掛了或要重來：

```bash
systemctl restart talkybuddy-server talkybuddy-local-client
journalctl -u talkybuddy-local-client -f -o cat     # 看「按一下按鍵開始錄音...」
```

## 開機**不會**自動處理的

1. **USB 麥克風實體靜音鍵**（上面第 1 步）——硬體開關，無解，只能手按。
2. **`cloud_tts`** 需要 `.env` 裡的 `ELEVENLABS_API_KEY`。純離線 demo 不需要；
   但**斷網降級的對比會少了 TTS 那一段**（雲端與邊緣聽起來會一樣）。
3. **`network_mode`** 預設 `edge`。要演練斷網降級得先切到 `cloud`，否則沒有東西可降級。

---

## 換機／重刷機才需要做

```bash
./edge/runtime/provision_device.sh      # venv + 套件 + logind drop-in（步驟 3/3）
./edge/deploy/install_services.sh --now # 安裝並啟動兩個 service
```

同步程式碼（**不要用 `push.sh`，它會連 1GB GGUF 一起推**）：

```bash
for d in server edge/runtime edge/deploy web; do
  rsync -az --exclude='__pycache__' --exclude='*.pyc' --exclude='bin/llama-server' \
    -e "ssh -o ServerAliveInterval=15" "$d/" root@<裝置IP>:/root/talkybuddy/$d/
done
systemctl restart talkybuddy-server talkybuddy-local-client   # 讓新程式碼生效
```

---

## ⚠️ 兩個必須知道的限制

**1. power 鍵是 demo 權宜方案，不是出貨設計。**
`HandlePowerKeyLongPress=ignore` 只擋得住 logind 那一層。**PMIC 的長按強制斷電是
硬體行為、不經 kernel，軟體攔不住**（約按住 8–10 秒）。你自己短按沒問題，
交給小孩按住不放就會斷電。要出貨得換 GPIO 外接按鈕或改非按鍵觸發。

**2. 板上那顆「自訂鍵」不能用。**
`KEY_HOME`(102) 在 kernel 位元圖裡有註冊，但**那顆實體鍵不送任何 evdev 事件**
（按數十次、跨重開機、繞過 Python 直接 `dd` 讀都是 0 bytes；同時以耳機孔插拔
事件作對照組，證明觀測方法本身有效）。**註冊 ≠ 接得上。** 2026-07-29 記錄的
「按自訂鍵可用」是錯的，已由 commit `eb44d44` 更正。誤按它會以為按鍵壞了。

---

## 出問題時的診斷順序

1. `./.venv/bin/python -m edge.runtime.preflight --mic` ← **先跑這個**，它會直接指出問題
2. 按鍵沒反應 → `./.venv/bin/python -m edge.runtime.key_probe`（掃全部節點、印實際讀哪個）
3. 對話內容不對 → `TALKYBUDDY_PIPELINE_PROFILE=edge ./.venv/bin/python -m edge.runtime.dump_recent_turns`
4. 服務層 → `journalctl -u talkybuddy-local-client -n 50 -o cat`

> **診斷鐵則：先排除觀測管道，再懷疑硬體。** 經 SSH 前景執行的診斷工具，連線一斷
> 輸出就卡在死掉的 TCP 裡永遠傳不回來，看起來與「按了沒反應」一模一樣。這個假象
> 在 07-30 浪費了兩輪除錯。要可靠就讓結果留在裝置本機：
> `nohup ... key_probe --seconds 60 > /tmp/kp.log 2>&1 &`，之後再撈檔案。

相關文件：`edge/runtime/README.md`（按鍵與 ALSA 細節）、
`edge/NETCUT_REHEARSAL_CHECKLIST.md`（斷網演練）。
