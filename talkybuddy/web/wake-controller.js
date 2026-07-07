// A1 喚醒層狀態機（純邏輯、零瀏覽器依賴，可 node --test）。
// 唯一協調者：串接注入的 WakeWordEngine 與 MicRouter，保證麥克風循序切換。
// 狀態：ARMED ──(喚醒詞|push)──▶ ACTIVE(一輪) ──(結束|逾時)──▶ COOLDOWN ──(去抖)──▶ ARMED

export const WakeState = { ARMED: "armed", ACTIVE: "active", COOLDOWN: "cooldown" };

export class WakeController {
  constructor(opts) {
    this.engine = opts.engine;
    this.micRouter = opts.micRouter;
    this.onState = opts.onState || function () {};
    this.onWakeEvent = opts.onWakeEvent || function () {};
    this.canWake = opts.canWake || function () { return true; };
    this.cooldownMs = opts.cooldownMs != null ? opts.cooldownMs : 1500;
    this.turnTimeoutMs = opts.turnTimeoutMs != null ? opts.turnTimeoutMs : 15000;
    this._setTimeout = opts.setTimeout || setTimeout;
    this._clearTimeout = opts.clearTimeout || clearTimeout;
    this._state = WakeState.ARMED;
    this._turnTimer = null;
    this._cooldownTimer = null;
    this._onDetected = () => { this.onWake("wakeword"); };
  }

  get state() { return this._state; }

  async arm() {
    // 清掉可能還在等待的舊計時器，避免重覆 arm（例如手動 arm() 撞上尚未觸發的去抖計時器）。
    if (this._cooldownTimer != null) { this._clearTimeout(this._cooldownTimer); this._cooldownTimer = null; }
    if (this._turnTimer != null) { this._clearTimeout(this._turnTimer); this._turnTimer = null; }
    this._state = WakeState.ARMED;
    this.onState(WakeState.ARMED);
    if (this.engine && this.engine.available()) {
      try {
        await this.engine.start(this._onDetected);
      } catch (err) {
        // engine.start 失敗視同引擎不可用：維持 ARMED（degrade-safe），push 仍可運作，
        // 避免去抖後自動重新 arm 產生未處理的 rejection。
      }
    }
  }

  async onWake(source) {
    if (this._state !== WakeState.ARMED) { return; }   // 重入保護
    if (!this.canWake()) { return; }                    // 半雙工鎖
    this._state = WakeState.ACTIVE;
    this.onWakeEvent(source);
    this.onState(WakeState.ACTIVE);
    if (this.engine && this.engine.available()) {
      // 先發動釋放麥（順序保留，關鍵）；不 await 以保證 recordTurn 於同步階段接手，
      // onWake 於 ACTIVE 即解析，該輪在背景進行（見下方 recordTurn().then）。
      Promise.resolve(this.engine.stop()).catch(function () {});
    }
    this._turnTimer = this._setTimeout(() => { this._safeStopTurn(); }, this.turnTimeoutMs);
    // 該輪於背景進行；錄音停止(或失敗)後結束該輪、退回待命（degrade-safe）。
    this.micRouter.recordTurn().then(
      () => { this._endTurn(); },
      () => { this._endTurn(); }
    );
  }

  triggerPush() {
    if (this._state === WakeState.ARMED) { this.onWake("push"); }
    else if (this._state === WakeState.ACTIVE) { this._safeStopTurn(); }
  }

  _safeStopTurn() {
    if (this._state === WakeState.ACTIVE) { this.micRouter.stopTurn(); }
  }

  async _endTurn() {
    if (this._turnTimer != null) { this._clearTimeout(this._turnTimer); this._turnTimer = null; }
    this._state = WakeState.COOLDOWN;
    this.onState(WakeState.COOLDOWN);
    this._cooldownTimer = this._setTimeout(() => { this.arm(); }, this.cooldownMs);
  }
}
