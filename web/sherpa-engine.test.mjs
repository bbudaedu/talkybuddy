import { test } from "node:test";
import assert from "node:assert/strict";
import { createSherpaEngine } from "./sherpa-engine.js";

function fakeKws(results) {
  // results: 每次 getResult 依序回傳的陣列；耗盡後回 {keyword:""}
  const calls = [];
  let i = 0;
  const stream = { freed: false, _wave: [], acceptWaveform(sr, s) { calls.push(["accept", sr, s.length]); }, free() { this.freed = true; } };
  const kws = {
    freed: false,
    createStream() { calls.push("createStream"); return stream; },
    _ready: true,
    isReady() { const r = i < results.length; return r; },
    decode() { calls.push("decode"); },
    getResult() { const r = results[i] || { keyword: "" }; i += 1; return r; },
    reset() { calls.push("reset"); },
    free() { this.freed = true; },
  };
  return { kws, stream, calls };
}

function fakeAudio() {
  const tracks = [{ stopped: false, stop() { this.stopped = true; } }];
  const micStream = { getTracks() { return tracks; } };
  const node = { connected: [], disconnect() { this.disconnected = true; }, connect(x) { this.connected.push(x); } };
  const ctx = {
    sampleRate: 16000, closed: false,
    createMediaStreamSource() { return node; },
    createScriptProcessor() { return { onaudioprocess: null, connect() {}, disconnect() {} }; },
    createJavaScriptNode() { return this.createScriptProcessor(); },
    destination: {},
    close() { this.closed = true; return Promise.resolve(); },
  };
  return { micStream, tracks, ctx };
}

const baseCfg = { keywords: "sh uō sh uō x ué b àn @說說學伴", keywordsThreshold: 0.25 };

test("available()：Module 與 createKws 皆備才 true", () => {
  const { kws } = fakeKws([]);
  const a = fakeAudio();
  const eng = createSherpaEngine(baseCfg, { Module: {}, createKws: () => kws, getUserMedia: async () => a.micStream, createAudioContext: () => a.ctx });
  assert.equal(eng.available(), true);
  const eng2 = createSherpaEngine(baseCfg, { Module: null, createKws: () => kws });
  assert.equal(eng2.available(), false);
});

test("start：createKws 帶正確 keywords/threshold、只建一次", async () => {
  const { kws } = fakeKws([]);
  const a = fakeAudio();
  let seenCfg = null; let n = 0;
  const createKws = (m, c) => { n += 1; seenCfg = c; return kws; };
  const eng = createSherpaEngine(baseCfg, { Module: {}, createKws, getUserMedia: async () => a.micStream, createAudioContext: () => a.ctx });
  await eng.start(() => {});
  await eng.stop();
  await eng.start(() => {});
  assert.equal(n, 1);                                  // 只建一次
  assert.equal(seenCfg.keywords, baseCfg.keywords);
  assert.equal(seenCfg.keywordsThreshold, 0.25);
  assert.ok(seenCfg.modelConfig && seenCfg.modelConfig.transducer.encoder.includes("epoch-12-avg-2"));
});

test("偵測：getResult 有 keyword → reset 並呼叫 onDetected", async () => {
  const { kws } = fakeKws([{ keyword: "說說學伴" }]);
  const a = fakeAudio();
  const detected = [];
  // 用可控 processor 手動觸發一次 onaudioprocess
  let proc = null;
  a.ctx.createScriptProcessor = () => { proc = { onaudioprocess: null, connect() {}, disconnect() {} }; return proc; };
  const eng = createSherpaEngine(baseCfg, { Module: {}, createKws: () => kws, getUserMedia: async () => a.micStream, createAudioContext: () => a.ctx });
  await eng.start((r) => detected.push(r));
  proc.onaudioprocess({ inputBuffer: { getChannelData: () => new Float32Array(160) } });
  assert.deepEqual(detected, [{ keyword: "說說學伴" }]);
});

test("stop：停 mic tracks 釋放麥", async () => {
  const { kws } = fakeKws([]);
  const a = fakeAudio();
  const eng = createSherpaEngine(baseCfg, { Module: {}, createKws: () => kws, getUserMedia: async () => a.micStream, createAudioContext: () => a.ctx });
  await eng.start(() => {});
  await eng.stop();
  assert.equal(a.tracks[0].stopped, true);
});

test("未 available：start 為 no-op（不取麥、不建 kws）", async () => {
  let gum = 0;
  const eng = createSherpaEngine(baseCfg, { Module: null, createKws: null, getUserMedia: async () => { gum += 1; return {}; } });
  await eng.start(() => {});
  assert.equal(gum, 0);
});

test("start/stop/start：AudioContext 只建一次（不洩漏）", async () => {
  const { kws } = fakeKws([]);
  const a = fakeAudio();
  let ctxN = 0;
  const eng = createSherpaEngine(baseCfg, { Module: {}, createKws: () => kws, getUserMedia: async () => a.micStream, createAudioContext: () => { ctxN += 1; return a.ctx; } });
  await eng.start(() => {});
  await eng.stop();
  await eng.start(() => {});
  assert.equal(ctxN, 1);
});
