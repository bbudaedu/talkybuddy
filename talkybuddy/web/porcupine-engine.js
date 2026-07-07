// WakeWordEngine 的 Porcupine 實作，封裝 @picovoice/porcupine-web@4.0.1 +
// @picovoice/web-voice-processor@4.0.10。生命週期無 start/stop：靠 WVP subscribe/unsubscribe。
// deps 可注入（單元測用 fake），省略時從 jsDelivr +esm 動態載入。

const CDN_PORCUPINE = "https://cdn.jsdelivr.net/npm/@picovoice/porcupine-web@4.0.1/+esm";
const CDN_WVP = "https://cdn.jsdelivr.net/npm/@picovoice/web-voice-processor@4.0.10/+esm";

async function loadDeps() {
  const p = await import(CDN_PORCUPINE);
  const w = await import(CDN_WVP);
  return { PorcupineWorker: p.PorcupineWorker, BuiltInKeyword: p.BuiltInKeyword, WebVoiceProcessor: w.WebVoiceProcessor };
}

export async function createPorcupineEngine(cfg, deps) {
  const d = deps || (await loadDeps());
  const PorcupineWorker = d.PorcupineWorker;
  const BuiltInKeyword = d.BuiltInKeyword;
  const WebVoiceProcessor = d.WebVoiceProcessor;
  let worker = null;

  function keywords() {
    if (cfg.keywordPublicPath) {
      return [{ publicPath: cfg.keywordPublicPath, label: cfg.keywordLabel, sensitivity: cfg.sensitivity }];
    }
    return [{ builtin: BuiltInKeyword[cfg.keywordBuiltin], sensitivity: cfg.sensitivity }];
  }

  return {
    available: function () { return !!cfg.accessKey; },
    start: async function (onDetected) {
      if (!cfg.accessKey) { return; }
      if (!worker) {
        worker = await PorcupineWorker.create(
          cfg.accessKey,
          keywords(),
          function (detection) { onDetected(detection); },
          { publicPath: cfg.modelPublicPath }
        );
      }
      await WebVoiceProcessor.subscribe(worker);
    },
    stop: async function () {
      if (worker) { await WebVoiceProcessor.unsubscribe(worker); }
    },
    release: async function () {
      if (worker) {
        await WebVoiceProcessor.unsubscribe(worker);
        await worker.release();
        worker.terminate();
        worker = null;
      }
    },
  };
}
