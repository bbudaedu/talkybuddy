// live-client.js — 即時 S2S 前端：麥克風 16k PCM 上行 + Nova Sonic 24k PCM 播放。
// 純函式（floatTo16BitPCM / base64ToPCM16 / PlaybackQueue / downsampleTo16k）可 node 測；
// LiveSession 整合層走瀏覽器 API（手動 e2e）。頂層僅宣告，node import 不觸發瀏覽器 API。

/* ---------- 純函式（node 可測） ---------- */

export function floatTo16BitPCM(float32) {
  const out = new DataView(new ArrayBuffer(float32.length * 2));
  for (let i = 0; i < float32.length; i++) {
    let s = Math.max(-1, Math.min(1, float32[i]));
    out.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return out.buffer;
}

export function base64ToPCM16(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Int16Array(bytes.buffer, 0, Math.floor(bytes.length / 2));
}

export class PlaybackQueue {
  constructor() { this._chunks = []; this._len = 0; }
  enqueue(int16) { this._chunks.push(int16); this._len += int16.length; }
  size() { return this._len; }
  drain() { const c = this._chunks; this._chunks = []; this._len = 0; return c; }
}

export function downsampleTo16k(float32, inRate) {
  if (inRate === 16000) return float32;
  const ratio = inRate / 16000;
  const outLen = Math.floor(float32.length / ratio);
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i++) out[i] = float32[Math.floor(i * ratio)];
  return out;
}

/* ---------- 瀏覽器整合層（手動 e2e；node 測不觸及） ---------- */

export function liveWsURL() {
  const proto = location.protocol === "https:" ? "wss://" : "ws://";
  return proto + location.host + "/ws/live";
}

// LiveSession：封裝一場 /ws/live 對話的錄放與 WS 接線（hold-to-talk）。
// callbacks：onTranscript(role,text) / onTurnEnd() / onError(reason) / onStateChange(state)
export class LiveSession {
  constructor(cb) {
    this.cb = cb || {};
    this.ws = null;
    this.micCtx = null;      // 擷取用 AudioContext（原生取樣率）
    this.playCtx = null;     // 播放用 AudioContext（24kHz）
    this.stream = null;
    this.node = null;
    this.capturing = false;
    this._nextStart = 0;
  }

  async start() {
    // 1) 開 WS
    this.ws = new WebSocket(liveWsURL());
    this.ws.binaryType = "arraybuffer";
    this.ws.onmessage = (ev) => this._onMessage(ev);
    await new Promise((resolve, reject) => {
      this.ws.onopen = resolve;
      this.ws.onerror = () => reject(new Error("ws_open_failed"));
    });
    // ws 開啟後才掛持續性 onerror（避免與 open reject 競態）
    this.ws.onerror = () => { if (this.cb.onError) this.cb.onError("stream_error"); };

    // 2) 播放 context（24kHz raw PCM 排程接續）
    this.playCtx = new AudioContext({ sampleRate: 24000 });

    // 3) 擷取：麥克風 → AudioWorklet → PCM16k 上行
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 } });
    this.micCtx = new AudioContext();
    await this.micCtx.audioWorklet.addModule("/static/live-capture-processor.js");
    const src = this.micCtx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.micCtx, "live-capture");
    this.node.port.onmessage = (e) => {
      if (!this.capturing || !this.ws || this.ws.readyState !== 1) return;
      const pcm16 = floatTo16BitPCM(downsampleTo16k(e.data, this.micCtx.sampleRate));
      this.ws.send(pcm16);
    };
    src.connect(this.node);
    // worklet node 需接 destination 才會被 graph 拉動；用 0-gain 靜音避免回授
    const mute = this.micCtx.createGain();
    mute.gain.value = 0;
    this.node.connect(mute).connect(this.micCtx.destination);
  }

  pushToTalkStart() {
    this.capturing = true;
    // AudioContext 建立於頁面載入（非使用者手勢）會停在 suspended；此處 pointerdown
    // 是使用者手勢，resume 才會拉動擷取 worklet（否則錄不到音）與播放（否則聽不到回覆）。
    if (this.micCtx && this.micCtx.state === "suspended") this.micCtx.resume();
    if (this.playCtx && this.playCtx.state === "suspended") this.playCtx.resume();
    if (this.cb.onStateChange) this.cb.onStateChange("listen");
  }

  pushToTalkEnd() {
    this.capturing = false;
    if (this.ws && this.ws.readyState === 1) {
      this.ws.send(JSON.stringify({ type: "user_end" }));
    }
    if (this.cb.onStateChange) this.cb.onStateChange("talk");
  }

  _onMessage(ev) {
    if (typeof ev.data !== "string") {
      this._play(new Int16Array(ev.data));
      return;
    }
    let m;
    try { m = JSON.parse(ev.data); } catch (e) { return; }
    if (m.type === "live_transcript") {
      if (this.cb.onTranscript) this.cb.onTranscript(m.role, m.text);
    } else if (m.type === "turn_end") {
      if (this.cb.onTurnEnd) this.cb.onTurnEnd();
    } else if (m.type === "live_error") {
      if (this.cb.onError) this.cb.onError(m.reason);
    }
  }

  _play(int16) {
    if (!this.playCtx || !int16.length) return;
    const f = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) f[i] = int16[i] / 32768;
    const buf = this.playCtx.createBuffer(1, f.length, 24000);
    buf.copyToChannel(f, 0);
    const node = this.playCtx.createBufferSource();
    node.buffer = buf;
    node.connect(this.playCtx.destination);
    const now = this.playCtx.currentTime;
    this._nextStart = Math.max(this._nextStart, now);
    node.start(this._nextStart);
    this._nextStart += buf.duration;
  }

  async close() {
    try { if (this.ws && this.ws.readyState === 1) this.ws.send(JSON.stringify({ type: "bye" })); } catch (e) {}
    try { if (this.ws) this.ws.close(); } catch (e) {}
    try { if (this.stream) this.stream.getTracks().forEach((t) => t.stop()); } catch (e) {}
    try { if (this.micCtx) await this.micCtx.close(); } catch (e) {}
    try { if (this.playCtx) await this.playCtx.close(); } catch (e) {}
  }
}
