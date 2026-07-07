import { test } from "node:test";
import assert from "node:assert/strict";
import { createPorcupineEngine } from "./porcupine-engine.js";

function fakeDeps() {
  const calls = [];
  const worker = { released: false, async release() { this.released = true; }, terminate() { calls.push("terminate"); } };
  const PorcupineWorker = {
    async create(key, keywords, cb, model) { calls.push(["create", key, keywords, model]); worker._cb = cb; return worker; },
  };
  const BuiltInKeyword = { Bumblebee: "BUMBLEBEE_BYTES", Porcupine: "PORCUPINE_BYTES" };
  const WebVoiceProcessor = {
    async subscribe(w) { calls.push(["subscribe", w === worker]); },
    async unsubscribe(w) { calls.push(["unsubscribe", w === worker]); },
  };
  return { deps: { PorcupineWorker, BuiltInKeyword, WebVoiceProcessor }, calls, worker };
}

const baseCfg = {
  accessKey: "k-1", keywordBuiltin: "Bumblebee", keywordLabel: "Bumblebee",
  keywordPublicPath: "", modelPublicPath: "/assets/porcupine_params.pv", sensitivity: 0.6,
};

test("available() 依 accessKey", async () => {
  const { deps } = fakeDeps();
  const eng = await createPorcupineEngine(baseCfg, deps);
  assert.equal(eng.available(), true);
  const eng2 = await createPorcupineEngine({ ...baseCfg, accessKey: "" }, deps);
  assert.equal(eng2.available(), false);
});

test("start：以內建喚醒詞 create 一次並 subscribe", async () => {
  const { deps, calls, worker } = fakeDeps();
  const detected = [];
  const eng = await createPorcupineEngine(baseCfg, deps);
  await eng.start((d) => detected.push(d));
  const createCall = calls.find((c) => Array.isArray(c) && c[0] === "create");
  assert.ok(createCall);
  assert.equal(createCall[1], "k-1");
  assert.deepEqual(createCall[2], [{ builtin: "BUMBLEBEE_BYTES", sensitivity: 0.6 }]);
  assert.deepEqual(createCall[3], { publicPath: "/assets/porcupine_params.pv" });
  assert.ok(calls.some((c) => Array.isArray(c) && c[0] === "subscribe" && c[1] === true));
  worker._cb({ index: 0, label: "Bumblebee" });   // 引擎回呼 → onDetected
  assert.deepEqual(detected, [{ index: 0, label: "Bumblebee" }]);
});

test("自訂 .ppn（keywordPublicPath）時用 publicPath+label", async () => {
  const { deps, calls } = fakeDeps();
  const eng = await createPorcupineEngine(
    { ...baseCfg, keywordPublicPath: "/assets/hey-penguin_wasm.ppn", keywordLabel: "Hey Penguin" }, deps);
  await eng.start(() => {});
  const createCall = calls.find((c) => Array.isArray(c) && c[0] === "create");
  assert.deepEqual(createCall[2], [{ publicPath: "/assets/hey-penguin_wasm.ppn", label: "Hey Penguin", sensitivity: 0.6 }]);
});

test("stop 呼叫 unsubscribe；start 兩次只 create 一次", async () => {
  const { deps, calls } = fakeDeps();
  const eng = await createPorcupineEngine(baseCfg, deps);
  await eng.start(() => {});
  await eng.stop();
  await eng.start(() => {});
  assert.equal(calls.filter((c) => Array.isArray(c) && c[0] === "create").length, 1);
  assert.ok(calls.some((c) => Array.isArray(c) && c[0] === "unsubscribe" && c[1] === true));
});

test("無 accessKey：start 為 no-op（不 create/subscribe）", async () => {
  const { deps, calls } = fakeDeps();
  const eng = await createPorcupineEngine({ ...baseCfg, accessKey: "" }, deps);
  await eng.start(() => {});
  assert.equal(calls.length, 0);
});
