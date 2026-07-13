// live-capture-processor.js — 即時 S2S 擷取用 AudioWorklet processor。
// 每個 render quantum 把單聲道 Float32 樣本 postMessage 回主執行緒；
// 主執行緒（LiveSession）負責 downsampleTo16k → floatTo16BitPCM → WS 上行。
// slice(0) 複製一份再送，避免共享底層 buffer 被下一輪覆寫。
class LiveCaptureProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch && ch.length) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor("live-capture", LiveCaptureProcessor);
