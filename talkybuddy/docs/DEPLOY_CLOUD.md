# 雲端 VM 佈署指南（跨平台雲端登入）

說說學伴的雲端終端（PC／平板／手機瀏覽器）走「全語音雲端管線」；玩偶端仍跑
邊緣本地 AI，定期把互動上行到雲端。雲端為唯一真相，導師跨終端查看單一身份。

> ⚠️ **安全前置（未完成不得上雲）**：先到 ElevenLabs 後台**撤銷任何曾外露的舊
> API 金鑰**並重新產生；新金鑰只放本 VM 的環境變數，**絕不 commit**。

## 1. 環境變數

| 變數 | 用途 | 範例／預設 |
| --- | --- | --- |
| `TALKYBUDDY_JWT_SECRET` | 簽發／驗證登入 JWT 的 HMAC 密鑰（**必設**，勿用預設 dev 值） | 隨機 32+ bytes |
| `TALKYBUDDY_PIPELINE_PROFILE` | 佈署 profile；雲端設 `cloud`，玩偶設 `edge` | `cloud` |
| `ELEVENLABS_API_KEY` | 雲端情緒 TTS（**新金鑰**，撤銷舊的後產生） | `sk-...` |
| `PICOVOICE_ACCESS_KEY` | 瀏覽器端語音喚醒（可選；未設則只用 push） | `...` |
| `TALKYBUDDY_CONSENT_GRANTED` | 家長同意閘門；未同意時強制 edge-only、資料不出境 | `true` |

產生 JWT 密鑰範例：

```bash
export TALKYBUDDY_JWT_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
```

## 2. 啟動指令（雲端）

```bash
cd talkybuddy
TALKYBUDDY_PIPELINE_PROFILE=cloud .venv/bin/python -m uvicorn server.app:app \
  --host 0.0.0.0 --port 8000
```

`cloud` profile 會讓 app 啟動時預設 `network_mode=cloud`（全語音走雲端 TTS）。

## 3. TLS / WSS（瀏覽器麥克風必要）

瀏覽器只在 **HTTPS/WSS** 或 `localhost` 下允許麥克風。正式佈署需在前面放
nginx / caddy 反向代理並掛 TLS 憑證：

- HTTP `:8000` ← 反代 ← HTTPS `:443`（`/` 與 `/ws/talk` 都要轉發，WS 需
  `Upgrade`/`Connection` header）。
- caddy 範例：

```
your.domain {
    reverse_proxy 127.0.0.1:8000
}
```

（caddy 會自動處理 WebSocket 升級與 Let's Encrypt 憑證。）

## 4. 玩偶端（edge）

玩偶跑本地 AI，設定：

```bash
export TALKYBUDDY_PIPELINE_PROFILE=edge
```

並定期把未同步互動上行到雲端（cron 或背景 task）——用 `server.sync_client`：

```python
import urllib.request, json
from server import sync_client

def http_post(url, payload, headers):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={**headers, "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

# device token 由雲端登入 device 帳號取得（POST /api/login）
sync_client.push_pending("https://your.domain", DEVICE_TOKEN, http_post)
```

`push_pending` 會讀本地 `synced=0` 的互動 → `POST /api/sync`（雲端依
`student_id`+`device_id`+`client_ts` 去重）→ 成功後 `mark_all_synced`。

## 5. 帳號（demo 種子）

啟動時自動建 `accounts` 表並種三筆（密碼皆 `demo1234`，正式請改）：

| email | role | sub |
| --- | --- | --- |
| `tutor@demo` | tutor | `TUTOR-001` |
| `aming@demo` | student | `STUDENT-AMING-004` |
| `device:GENIO-520-X992` | device | `STUDENT-AMING-004` |

- 學生瀏覽器：登入 `aming@demo` → 全語音對話（WS 帶 `?token=`）。
- 導師瀏覽器：登入 `tutor@demo` → 查看阿明的互動與診斷（`?student=` 範圍）。
- 玩偶：登入 `device:GENIO-520-X992` 取得 device token → `sync_client` 上行。
