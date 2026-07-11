// WakeWordEngine 的 sherpa-onnx KWS 實作，介面同 porcupine-engine.js：
// available()/start(onDetected)/stop()/release()。內部自持 AudioContext + ScriptProcessorNode
// 把 16k PCM 餵進 sherpa WASM KWS。deps 可注入供 node --test（不碰真實瀏覽器 API）。

// WASM .data 內建的模型檔路徑（fp32 epoch-12-avg-2，＝spike 驗證那顆）。
function buildKwsConfig(cfg) {
  return {
    featConfig: { samplingRate: 16000, featureDim: 80 },
    modelConfig: {
      transducer: {
        encoder: "./encoder-epoch-12-avg-2-chunk-16-left-64.onnx",
        decoder: "./decoder-epoch-12-avg-2-chunk-16-left-64.onnx",
        joiner: "./joiner-epoch-12-avg-2-chunk-16-left-64.onnx",
      },
      tokens: "./tokens.txt",
      provider: "cpu", modelType: "", numThreads: cfg.numThreads || 1,
      debug: 0, modelingUnit: "cjkchar", bpeVocab: "",
    },
    maxActivePaths: 4, numTrailingBlanks: 1,
    keywordsScore: cfg.keywordsScore != null ? cfg.keywordsScore : 1.0,
    keywordsThreshold: cfg.keywordsThreshold != null ? cfg.keywordsThreshold : 0.25,
    keywords: cfg.keywords,
  };
}

// 平均法降採樣到 16k（沿用 sherpa-onnx wasm 範例 downsampleBuffer 作法）。
function downsample(buffer, inRate, outRate) {
  if (outRate === inRate) { return buffer; }
  var ratio = inRate / outRate;
  var newLen = Math.round(buffer.length / ratio);
  var out = new Float32Array(newLen);
  var oi = 0, bi = 0;
  while (oi < newLen) {
    var next = Math.round((oi + 1) * ratio);
    var acc = 0, cnt = 0;
    for (var i = bi; i < next && i < buffer.length; i++) { acc += buffer[i]; cnt++; }
    out[oi] = cnt ? acc / cnt : 0; oi++; bi = next;
  }
  return out;
}

export function createSherpaEngine(cfg, deps) {
  deps = deps || {};
  var hasWin = typeof window !== "undefined";
  var Module = deps.Module != null ? deps.Module : (hasWin ? window.Module : null);
  var createKws = deps.createKws != null ? deps.createKws : (hasWin ? window.createKws : null);
  var getUserMedia = deps.getUserMedia || function () { return navigator.mediaDevices.getUserMedia({ audio: true }); };
  var createAudioContext = deps.createAudioContext || function () {
    var AC = window.AudioContext || window.webkitAudioContext;
    return new AC({ sampleRate: 16000 });
  };
  var kwsConfig = buildKwsConfig(cfg);

  var kws = null, stream = null, audioCtx = null, micStream = null, source = null, processor = null, started = false;

  function pump(onDetected) {
    while (kws.isReady(stream)) {
      kws.decode(stream);
      var r = kws.getResult(stream);
      if (r && r.keyword && r.keyword.length > 0) { kws.reset(stream); onDetected(r); }
    }
  }

  return {
    available: function () { return !!Module && typeof createKws === "function"; },

    start: async function (onDetected) {
      if (!Module || typeof createKws !== "function") { return; }   // no-op（未 available）
      if (started) { return; }
      micStream = await getUserMedia();
      if (!audioCtx) { audioCtx = createAudioContext(); }   // 重用單一 AudioContext，避免每輪 start 洩漏
      if (!kws) { kws = createKws(Module, kwsConfig); }             // 只建一次
      if (!stream) { stream = kws.createStream(); }
      source = audioCtx.createMediaStreamSource(micStream);
      processor = (audioCtx.createScriptProcessor
        ? audioCtx.createScriptProcessor(4096, 1, 1)
        : audioCtx.createJavaScriptNode(4096, 1, 1));
      var sr = audioCtx.sampleRate;
      processor.onaudioprocess = function (e) {
        var s = e.inputBuffer.getChannelData(0);
        if (sr !== 16000) { s = downsample(s, sr, 16000); }
        stream.acceptWaveform(16000, s);
        pump(onDetected);
      };
      source.connect(processor);
      processor.connect(audioCtx.destination);
      started = true;
    },

    stop: async function () {
      if (!started) { return; }
      if (processor) { try { processor.disconnect(); } catch (e) {} processor.onaudioprocess = null; }
      if (source) { try { source.disconnect(); } catch (e) {} }
      if (micStream) { micStream.getTracks().forEach(function (t) { t.stop(); }); }   // 釋放麥（I4）
      micStream = null; source = null; processor = null; started = false;
    },

    release: async function () {
      await this.stop();
      if (stream && stream.free) { stream.free(); }
      if (kws && kws.free) { kws.free(); }
      if (audioCtx && audioCtx.close) { try { await audioCtx.close(); } catch (e) {} }
      stream = null; kws = null; audioCtx = null;
    },
  };
}
