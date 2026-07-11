import { test } from "node:test";
import assert from "node:assert/strict";
import { loadSherpaModule } from "./sherpa-loader.js";

test("依序注入兩支 glue、locateFile 前綴 baseUrl、runtime init 後 resolve Module", async () => {
  const scope = {};
  const srcs = [];
  const appendScript = (src) => { srcs.push(src); return Promise.resolve(); };
  const p = loadSherpaModule("/static/vendor/sherpa-kws/", { scope, appendScript });
  // 注入鏈跑完後，模擬 Emscripten runtime 就緒
  await Promise.resolve();
  assert.deepEqual(srcs, [
    "/static/vendor/sherpa-kws/sherpa-onnx-kws.js",
    "/static/vendor/sherpa-kws/sherpa-onnx-wasm-kws-main.js",
  ]);
  assert.equal(scope.Module.locateFile("foo.wasm"), "/static/vendor/sherpa-kws/foo.wasm");
  scope.Module.onRuntimeInitialized();
  const mod = await p;
  assert.equal(mod, scope.Module);
});

test("appendScript 失敗 → promise reject", async () => {
  const scope = {};
  const appendScript = () => Promise.reject(new Error("load fail"));
  await assert.rejects(() => loadSherpaModule("/x/", { scope, appendScript }), /load fail/);
});
