// 一輪錄音的麥克風管理：每輪重新 getUserMedia（循序切換所需，不常駐 stream），
// 錄音停止後把 blob 交給 sendBlob，並 track.stop() 釋放麥，讓喚醒引擎能重新接手。

export class MicRouter {
  constructor(opts) {
    this.getUserMedia = opts.getUserMedia;
    this.createRecorder = opts.createRecorder;
    this.sendBlob = opts.sendBlob;
    this.mimeType = opts.mimeType || "audio/webm;codecs=opus";
    this.minBytes = opts.minBytes != null ? opts.minBytes : 200;
    this._recorder = null;
    this._stream = null;
  }

  recordTurn() {
    const self = this;
    return new Promise(function (resolve, reject) {
      self.getUserMedia().then(function (stream) {
        self._stream = stream;
        const chunks = [];
        const recorder = self.createRecorder(stream, self.mimeType);
        self._recorder = recorder;
        recorder.ondataavailable = function (ev) {
          if (ev && ev.data && ev.data.size > 0) { chunks.push(ev.data); }
        };
        recorder.onstop = function () {
          const blob = new Blob(chunks, { type: self.mimeType });
          self._release();
          if (blob.size >= self.minBytes) {
            Promise.resolve(self.sendBlob(blob)).then(resolve, resolve);
          } else {
            resolve();   // 太短視為誤觸，不送
          }
        };
        recorder.start();
      }).catch(reject);
    });
  }

  stopTurn() {
    if (this._recorder && this._recorder.state !== "inactive") {
      this._recorder.stop();
    }
  }

  _release() {
    if (this._stream) {
      this._stream.getTracks().forEach(function (t) { t.stop(); });
      this._stream = null;
    }
    this._recorder = null;
  }
}
