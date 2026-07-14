import { test } from "node:test";
import assert from "node:assert";
import { LiveWakeCoordinator, LiveWakeState } from "./live-wake.js";

function harness(opts = {}) {
  const log = [];
  const engine = {
    _avail: opts.engineAvail !== false,
    available() { return this._avail; },
    async start(cb) { log.push("engine.start"); engine._cb = cb; },
    async stop() { log.push("engine.stop"); },
    async release() { log.push("engine.release"); },
  };
  const session = {
    startConversation() { log.push("session.startConversation"); },
    stopConversation() { log.push("session.stopConversation"); },
    async close() { log.push("session.close"); },
  };
  const c = new LiveWakeCoordinator({
    engine: opts.noEngine ? null : engine,
    startSession: async () => { log.push("startSession"); return session; },
    onCue: async (k) => { log.push("cue:" + k); },
    onStateChange: (s) => { log.push("state:" + s); },
    matchFarewell: (t) => t === "掰掰",
  });
  return { c, log, engine, session };
}

test("arm 進 IDLE 並啟動 KWS", async () => {
  const { c, log } = harness();
  await c.arm();
  assert.equal(c.state, LiveWakeState.IDLE);
  assert.ok(log.includes("engine.start"));
});

test("wake：先放 KWS 麥→開 session→播 cue→開麥", async () => {
  const { c, log } = harness();
  await c.arm();
  await c.wake("wakeword");
  assert.equal(c.state, LiveWakeState.CONVERSING);
  const order = log.filter(x => ["engine.stop","startSession","cue:start","session.startConversation"].includes(x));
  assert.deepEqual(order, ["engine.stop","startSession","cue:start","session.startConversation"]);
});

test("farewell 只認 USER role", async () => {
  const { c, log } = harness();
  await c.arm(); await c.wake("push");
  c.onTranscript("AI", "掰掰");        // 不觸發
  assert.equal(c.state, LiveWakeState.CONVERSING);
  c.onTranscript("USER", "掰掰");      // 觸發 end
  await new Promise(r => setTimeout(r, 0));
  assert.equal(c.state, LiveWakeState.IDLE);
  const order = log.filter(x => ["session.stopConversation","cue:stop","session.close"].includes(x));
  assert.deepEqual(order, ["session.stopConversation","cue:stop","session.close"]);
});

test("end 後重新 arm（可再喚醒）", async () => {
  const { c, log } = harness();
  await c.arm(); await c.wake("push"); await c.end("push");
  assert.equal(c.state, LiveWakeState.IDLE);
  assert.equal(log.filter(x => x === "engine.start").length, 2);  // arm 兩次
});

test("重入保護：CONVERSING 時再 wake 無效", async () => {
  const { c } = harness();
  await c.arm(); await c.wake("push");
  await c.wake("push");
  assert.equal(c.state, LiveWakeState.CONVERSING);
});

test("degrade：engine=null 時 arm 不崩、push 仍可 wake", async () => {
  const { c, log } = harness({ noEngine: true });
  await c.arm();
  assert.equal(c.state, LiveWakeState.IDLE);
  await c.wake("push");
  assert.equal(c.state, LiveWakeState.CONVERSING);
  assert.ok(log.includes("startSession"));
});
