import { test } from "node:test";
import assert from "node:assert/strict";
import { MicRouter } from "./mic-router.js";

function fakeStream() {
  const track = { stopped: false, stop() { this.stopped = true; } };
  return { _track: track, getTracks: () => [track] };
}

function fakeRecorder() {
  return {
    state: "recording",
    ondataavailable: null,
    onstop: null,
    start() { this.state = "recording"; },
    stop() { this.state = "inactive"; if (this.onstop) { this.onstop(); } },
    _emit(size) { if (this.ondataavailable) { this.ondataavailable({ data: new Blob([new Uint8Array(size)]) }); } },
  };
}

test("recordTurn 開麥錄音，stopTurn 後上傳且釋放麥", async () => {
  const calls = [];
  const stream = fakeStream();
  const recorder = fakeRecorder();
  let sent = null;
  const router = new MicRouter({
    getUserMedia: async () => { calls.push("getUserMedia"); return stream; },
    createRecorder: (s, mime) => { calls.push("createRecorder:" + mime); return recorder; },
    sendBlob: (blob) => { calls.push("sendBlob"); sent = blob; },
    mimeType: "audio/webm;codecs=opus",
    minBytes: 200,
  });
  const p = router.recordTurn();
  await Promise.resolve();
  recorder._emit(5000);          // 收到一段音訊
  router.stopTurn();             // 觸發停止
  await p;
  assert.deepEqual(calls, ["getUserMedia", "createRecorder:audio/webm;codecs=opus", "sendBlob"]);
  assert.ok(sent && sent.size >= 200);
  assert.equal(stream._track.stopped, true, "錄畢必須 track.stop() 釋放麥");
});

test("錄音過短（< minBytes）視為誤觸，不上傳但仍釋放麥", async () => {
  const stream = fakeStream();
  const recorder = fakeRecorder();
  let sendCount = 0;
  const router = new MicRouter({
    getUserMedia: async () => stream,
    createRecorder: () => recorder,
    sendBlob: () => { sendCount += 1; },
    minBytes: 200,
  });
  const p = router.recordTurn();
  await Promise.resolve();
  recorder._emit(50);            // 太短
  router.stopTurn();
  await p;
  assert.equal(sendCount, 0);
  assert.equal(stream._track.stopped, true);
});
