import test from "node:test";
import assert from "node:assert/strict";
import { REPEAT_MAX, RepeatGuard } from "./misaka-link.ts";

test("the same call REPEAT_MAX times in a row is refused; waits and varied calls are not", () => {
  const g = new RepeatGuard();
  const date = { command: "date -u +%H:%M:%S" };
  const verdicts = Array.from({ length: REPEAT_MAX }, () => g.check("bash", date));
  assert.deepEqual(verdicts.slice(0, -1).every(v => !v), true);
  assert.equal(verdicts.at(-1), true);
  // a sister's rhythm: observe, act, observe… never trips it
  const h = new RepeatGuard();
  for (let i = 0; i < 20; i++) {
    assert.equal(h.check("body_observe", {}), false);
    assert.equal(h.check("body_act", { intent: "mine", n: i }), false);
    assert.equal(h.check("body_wait_events", { timeout_s: 60 }), false);
  }
  for (let i = 0; i < 20; i++) assert.equal(h.check("body_wait_events", { timeout_s: 60 }), false);
  g.reset();
  assert.equal(g.check("bash", date), false); // a new run starts clean
});
