// 隔離 sherpa-onnx WASM 的 Emscripten glue 載入（非 ESM、會操作全域 Module）。
// 讓 sherpa-engine.js 保持可 node --test（測試注入 fake Module，不經此 loader）。

export function loadSherpaModule(baseUrl, deps) {
  deps = deps || {};
  var scope = deps.scope || (typeof window !== "undefined" ? window : {});
  var appendScript = deps.appendScript || function (src) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = src;
      s.onload = function () { resolve(); };
      s.onerror = function () { reject(new Error("script load failed: " + src)); };
      document.head.appendChild(s);
    });
  };
  return new Promise(function (resolve, reject) {
    scope.Module = {
      locateFile: function (path) { return baseUrl + path; },
      onRuntimeInitialized: function () { resolve(scope.Module); },
    };
    appendScript(baseUrl + "sherpa-onnx-kws.js")
      .then(function () { return appendScript(baseUrl + "sherpa-onnx-wasm-kws-main.js"); })
      .catch(reject);
  });
}
