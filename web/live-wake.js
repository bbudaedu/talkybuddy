// LiveWakeCoordinator — 喚醒詞→hands-free 全語音迴圈協調器。
// 純邏輯、零瀏覽器依賴（依賴由建構子注入），可 node --test。
// 狀態：IDLE（KWS 聽喚醒）↔ CONVERSING（LiveSession 常開對話）。麥克風分時互斥：
// IDLE 只 KWS 持麥、CONVERSING 只 LiveSession 持麥，交棒點各自 stop/release/close。

export const LiveWakeState = { IDLE: "idle", CONVERSING: "conversing" };

export class LiveWakeCoordinator {
  constructor(opts) {
    this.engine = opts.engine || null;            // sherpa KWS engine 或 null（無喚醒、僅點企鵝）
    this.startSession = opts.startSession;        // () => Promise<LiveSession>（已 start()）
    this.onCue = opts.onCue || function () { return Promise.resolve(); };
    this.onStateChange = opts.onStateChange || function () {};
    this.matchFarewell = opts.matchFarewell || function () { return false; };
    this._state = LiveWakeState.IDLE;
    this.session = null;
    this._busy = false;                           // 轉移進行中鎖（防 wake/end 重入競態）
    this._onDetected = () => { this.wake("wakeword"); };
  }

  get state() { return this._state; }

  async arm() {
    this._state = LiveWakeState.IDLE;
    this.session = null;
    this.onStateChange("idle");
    if (this.engine && this.engine.available()) {
      try { await this.engine.start(this._onDetected); }
      catch (e) { /* KWS 起不來：維持 IDLE，點企鵝 wake('push') 仍可用 */ }
    }
  }

  async wake(source) {
    if (this._state !== LiveWakeState.IDLE || this._busy) { return; }
    this._busy = true;
    try {
      if (this.engine && this.engine.available()) {
        try { await this.engine.stop(); } catch (e) {}
        try { if (this.engine.release) { await this.engine.release(); } } catch (e) {}
      }
      this.session = await this.startSession();
      this._state = LiveWakeState.CONVERSING;
      await this.onCue("start");                  // 播「我在，請說」（播完才開麥）
      if (this.session) { this.session.startConversation(); }
    } finally { this._busy = false; }
  }

  onTranscript(role, text) {
    if (this._state === LiveWakeState.CONVERSING && role === "USER" && this.matchFarewell(text)) {
      this.end("farewell");
    }
  }

  async end(source) {
    if (this._state !== LiveWakeState.CONVERSING || this._busy) { return; }
    this._busy = true;
    try {
      try { if (this.session) { this.session.stopConversation(); } } catch (e) {}
      await this.onCue("stop");                   // 播「先休息一下」
      try { if (this.session) { await this.session.close(); } } catch (e) {}
      this.session = null;
    } finally { this._busy = false; }
    await this.arm();                             // 回 IDLE、重啟 KWS
  }
}
