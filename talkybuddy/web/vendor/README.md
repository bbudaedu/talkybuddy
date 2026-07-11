# web/vendor — 第三方前端資產（不進版控）

## sherpa-kws（喚醒詞 WASM）

瀏覽器喚醒詞引擎（sherpa-onnx KWS，喚醒詞「說說學伴」）需要預建 WASM 資產，
體積約 10–15MB，依 fetch-on-setup 交付、不進 git。

### 安裝（在有網路的機器跑一次）

```bash
cd talkybuddy
.venv/bin/python scripts/fetch_sherpa_wasm.py
```

腳本會依序試 Huggingface Space → ModelScope 官方鏡像（給無法連 HF 的網路），
每個檔下載後做完整性驗證（擋掉登入頁/WAF 挑戰頁被當成資產寫入）。若兩者都不通，
可到 k2-fsa 的 KWS wasm space/studio Files 頁核對 resolve 連結後自訂來源：

```bash
.venv/bin/python scripts/fetch_sherpa_wasm.py --base <你的鏡像/…/resolve/main/>
```

完成後 `web/vendor/sherpa-kws/` 應有：
`sherpa-onnx-kws.js`、`sherpa-onnx-wasm-kws-main.js`、`*.wasm`、`*.data`。

### 沒安裝會怎樣？

前端 degrade-safe：喚醒詞不可用，但點企鵝（push-to-talk）與快速語句照常運作。
