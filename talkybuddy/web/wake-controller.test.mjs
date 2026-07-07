import { test } from "node:test";
import assert from "node:assert/strict";
import { WakeController, WakeState } from "./wake-controller.js";

function manualTimers() {
  let seq = 0;
  const pending = new Map();
  return {
    setTimeout: (fn) => { const id = ++seq; pending.set(id, fn); return id; },
    clearTimeout: (id) => { pending.delete(id); },
    flushAll: () => { for (const [id, fn] of [...pending]) { pending.delete(id); fn(); } },
    size: () => pending.size,
  };
}

function makeStubs(opts = {}) {
  const calls = [];
  const engine = {
    _available: opts.available !== false,
    available() { return this._available; },
    async start(onDetected) { calls.push("engine.start"); this._onDetected = onDetected; },
    async stop() { calls.push("engine.stop"); },
  };
  const micRouter = {
    _resolve: null,
    async recordTurn() { calls.push("mic.recordTurn"); return new Promise((r) => { this._resolve = r; }); },
    stopTurn() { calls.push("mic.stopTurn"); if (this._resolve) { const r = this._resolve; this._resolve = null; r(); } },
  };
  return { engine, micRouter, calls };
}

test("arm 進 ARMED 並在引擎可用時開始 always-listening", async () => {
  const { engine, micRouter, calls } = makeStubs();
  const t = manualTimers();
  const c = new WakeController({ engine, micRouter, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout });
  await c.arm();
  assert.equal(c.state, WakeState.ARMED);
  assert.ok(calls.includes("engine.start"));
});

test("喚醒 → ACTIVE：先停引擎再錄音（循序）", async () => {
  const { engine, micRouter, calls } = makeStubs();
  const t = manualTimers();
  const c = new WakeController({ engine, micRouter, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout });
  await c.arm();
  await c.onWake("wakeword");
  assert.equal(c.state, WakeState.ACTIVE);
  const stopIdx = calls.indexOf("engine.stop");
  const recIdx = calls.indexOf("mic.recordTurn");
  assert.ok(stopIdx >= 0 && recIdx > stopIdx, "engine.stop 必須在 mic.recordTurn 之前");
});

test("ACTIVE 期間再收到喚醒 → 忽略（不重入）", async () => {
  const { engine, micRouter, calls } = makeStubs();
  const t = manualTimers();
  const c = new WakeController({ engine, micRouter, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout });
  await c.arm();
  await c.onWake("wakeword");
  const before = calls.length;
  await c.onWake("wakeword");
  assert.equal(calls.length, before, "重入喚醒不應再觸發任何呼叫");
});

test("一輪結束 → COOLDOWN → 去抖後回 ARMED 並重新 arm", async () => {
  const { engine, micRouter, calls } = makeStubs();
  const t = manualTimers();
  const c = new WakeController({ engine, micRouter, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout });
  await c.arm();
  const wake = c.onWake("wakeword");
  micRouter.stopTurn();           // 觸發 recordTurn resolve
  await wake;
  await new Promise((r) => setImmediate(r));   // 等背景該輪 recordTurn().then(_endTurn) 微任務排空（onWake 已於 ACTIVE 解析）
  assert.equal(c.state, WakeState.COOLDOWN);
  t.flushAll();                   // 去抖計時器到期
  await Promise.resolve();
  assert.equal(c.state, WakeState.ARMED);
  assert.equal(calls.filter((x) => x === "engine.start").length, 2);
});

test("引擎不可用：arm 不呼叫 engine.start，push 仍可進 ACTIVE", async () => {
  const { engine, micRouter, calls } = makeStubs({ available: false });
  const t = manualTimers();
  const c = new WakeController({ engine, micRouter, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout });
  await c.arm();
  assert.ok(!calls.includes("engine.start"));
  c.triggerPush();
  await Promise.resolve();
  assert.equal(c.state, WakeState.ACTIVE);
});

test("push tap-to-toggle：ACTIVE 時第二次 tap 結束該輪", async () => {
  const { engine, micRouter, calls } = makeStubs();
  const t = manualTimers();
  const c = new WakeController({ engine, micRouter, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout });
  await c.arm();
  c.triggerPush();                // 第一次：ARMED → ACTIVE
  await Promise.resolve();
  c.triggerPush();                // 第二次：停止錄音
  assert.ok(calls.includes("mic.stopTurn"));
});

test("錄音逾時：turnTimeout 到期自動停止該輪", async () => {
  const { engine, micRouter, calls } = makeStubs();
  const t = manualTimers();
  const c = new WakeController({ engine, micRouter, turnTimeoutMs: 15000, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout });
  await c.arm();
  const wake = c.onWake("wakeword");
  t.flushAll();                   // turn timer 到期 → stopTurn
  await wake;
  assert.ok(calls.includes("mic.stopTurn"));
});

test("canWake=false 時忽略喚醒（半雙工鎖）", async () => {
  const { engine, micRouter, calls } = makeStubs();
  const t = manualTimers();
  const c = new WakeController({ engine, micRouter, canWake: () => false, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout });
  await c.arm();
  await c.onWake("wakeword");
  assert.equal(c.state, WakeState.ARMED);
  assert.ok(!calls.includes("mic.recordTurn"));
});

test("degrade-safe：engine.start 失敗時 arm 不拋出、仍回到 ARMED（可用）", async () => {
  const { micRouter } = makeStubs();
  const t = manualTimers();
  const engine = {
    available() { return true; },
    async start() { throw new Error("engine.start 模擬失敗"); },
    async stop() {},
  };
  const c = new WakeController({ engine, micRouter, setTimeout: t.setTimeout, clearTimeout: t.clearTimeout });
  await assert.doesNotReject(c.arm());
  assert.equal(c.state, WakeState.ARMED);
});
