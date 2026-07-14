import { test } from "node:test";
import assert from "node:assert/strict";
import { floatTo16BitPCM, base64ToPCM16, PlaybackQueue, downsampleTo16k, LiveSession } from "./live-client.js";

test("floatTo16BitPCM 轉出 16-bit LE，範圍夾限", () => {
  const buf = floatTo16BitPCM(new Float32Array([0, 1, -1, 2]));
  const view = new DataView(buf);
  assert.equal(view.getInt16(0, true), 0);
  assert.equal(view.getInt16(2, true), 32767);   // 1.0 → 32767
  assert.equal(view.getInt16(4, true), -32768);  // -1.0 → -32768
  assert.equal(view.getInt16(6, true), 32767);   // 2.0 夾限到 32767
});

test("base64ToPCM16 還原 Int16Array", () => {
  // 兩個 sample：1000, -1000（16-bit LE）
  const bytes = new Uint8Array([0xe8, 0x03, 0x18, 0xfc]);
  const b64 = Buffer.from(bytes).toString("base64");
  const pcm = base64ToPCM16(b64);
  assert.equal(pcm.length, 2);
  assert.equal(pcm[0], 1000);
  assert.equal(pcm[1], -1000);
});

test("PlaybackQueue 累加長度", () => {
  const q = new PlaybackQueue();
  q.enqueue(new Int16Array([1, 2, 3]));
  q.enqueue(new Int16Array([4, 5]));
  assert.equal(q.size(), 5);
});

test("downsampleTo16k 48k→16k 長度約 1/3、16k passthrough 原參考", () => {
  const src = new Float32Array(48);
  for (let i = 0; i < 48; i++) src[i] = i / 48;
  assert.equal(downsampleTo16k(src, 48000).length, 16);
  assert.equal(downsampleTo16k(src, 16000), src);   // 同 sampleRate 直接回傳原陣列
});

test("_flushPlayback 停所有 source、清陣列、歸零排程游標（barge-in）", () => {
  const s = new LiveSession({});
  s.playCtx = { currentTime: 5 };              // 假 playCtx（不觸真瀏覽器 API）
  const stopped = [];
  s._sources = [{ stop: () => stopped.push("a") }, { stop: () => stopped.push("b") }];
  s._nextStart = 99;
  s._flushPlayback();
  assert.deepEqual(stopped, ["a", "b"]);       // 兩個 source 都停
  assert.equal(s._sources.length, 0);          // 陣列清空
  assert.equal(s._nextStart, 5);               // 歸零到 playCtx.currentTime
});

test("startConversation 設 capturing=true 並回報 listen（micCtx 為 null 時不觸瀏覽器 API）", () => {
  let state = null;
  const s = new LiveSession({ onStateChange: (v) => { state = v; } });
  s.startConversation();
  assert.equal(s.capturing, true);
  assert.equal(state, "listen");
});
