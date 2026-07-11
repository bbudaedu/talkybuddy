# web/vendor — 第三方前端資產（不進版控）

## sherpa-kws（喚醒詞 WASM）

瀏覽器喚醒詞引擎（sherpa-onnx KWS，喚醒詞「說說學伴」）需要預建 WASM 資產，
體積約 10–15MB，依 fetch-on-setup 交付、不進 git。

### 安裝（在有網路的機器跑一次）

```bash
cd talkybuddy
python scripts/fetch_sherpa_wasm.py
```

完成後 `web/vendor/sherpa-kws/` 應有：
`sherpa-onnx-kws.js`、`sherpa-onnx-wasm-kws-main.js`、`*.wasm`、`*.data`。

### 沒安裝會怎樣？

前端 degrade-safe：喚醒詞不可用，但點企鵝（push-to-talk）與快速語句照常運作。
