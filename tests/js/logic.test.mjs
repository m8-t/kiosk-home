import test from "node:test";
import assert from "node:assert/strict";
import {
  clampBrightness, displayBrightness, iconFor, mergeRooms, createThrottle,
} from "../../kiosk/web/logic.js";

test("clampBrightness", () => {
  assert.equal(clampBrightness(0), 1);
  assert.equal(clampBrightness(-5), 1);
  assert.equal(clampBrightness(150), 100);
  assert.equal(clampBrightness("42.4"), 42);
  assert.equal(clampBrightness(NaN), 1);
  assert.equal(clampBrightness(undefined), 1);
});

test("displayBrightness", () => {
  assert.equal(displayBrightness({ brightness: null }), 100);
  assert.equal(displayBrightness({}), 100);
  assert.equal(displayBrightness({ brightness: 33.6 }), 34);
  assert.equal(displayBrightness({ brightness: 0.2 }), 1);
});

test("iconFor", () => {
  assert.equal(iconFor({ title: "Music", icon: "🎵" }), "🎵");
  assert.equal(iconFor({ title: "music" }), "M");
  assert.equal(iconFor({ title: "  cam ", icon: " " }), "C");
  assert.equal(iconFor({ title: "" }), "?");
});

test("mergeRooms keeps local state inside the hold window", () => {
  const local = [{ id: "a", name: "A", on: true, brightness: 80 }];
  const polled = [{ id: "a", name: "A", on: true, brightness: 50 }];
  assert.deepEqual(mergeRooms(local, polled, { a: 9000 }, 10000, 2000), local);
  assert.deepEqual(mergeRooms(local, polled, { a: 7000 }, 10000, 2000), polled);
  assert.deepEqual(mergeRooms(local, polled, {}, 10000, 2000), polled);
});

test("mergeRooms adds new rooms and drops removed ones", () => {
  const local = [{ id: "gone", name: "G", on: true, brightness: 1 }];
  const polled = [{ id: "new", name: "N", on: false, brightness: null }];
  assert.deepEqual(mergeRooms(local, polled, { gone: 10000, new: 10000 }, 10000), polled);
});

function fakeClock() {
  let t = 1000;
  const timers = [];
  return {
    now: () => t,
    setTimer: (fn, ms) => { const timer = { fn, at: t + ms, live: true }; timers.push(timer); return timer; },
    clearTimer: (timer) => { timer.live = false; },
    advance(ms) {
      t += ms;
      for (const timer of timers) {
        if (timer.live && timer.at <= t) { timer.live = false; timer.fn(); }
      }
    },
  };
}

test("throttle fires first call at once and collapses the rest", () => {
  const clock = fakeClock();
  const calls = [];
  const th = createThrottle((v) => calls.push(v), 250, clock);
  th.call(1);
  th.call(2);
  th.call(3);
  assert.deepEqual(calls, [1]);
  clock.advance(100);
  assert.deepEqual(calls, [1]);
  clock.advance(150);
  assert.deepEqual(calls, [1, 3]);
  th.call(4);
  assert.deepEqual(calls, [1, 3]);
  clock.advance(250);
  assert.deepEqual(calls, [1, 3, 4]);
});

test("throttle allows a call after the interval has passed", () => {
  const clock = fakeClock();
  const calls = [];
  const th = createThrottle((v) => calls.push(v), 250, clock);
  th.call(1);
  clock.advance(300);
  th.call(2);
  assert.deepEqual(calls, [1, 2]);
});

test("flush sends the final value and cancels the pending call", () => {
  const clock = fakeClock();
  const calls = [];
  const th = createThrottle((v) => calls.push(v), 250, clock);
  th.call(1);
  th.call(2);
  th.flush(3);
  assert.deepEqual(calls, [1, 3]);
  clock.advance(1000);
  assert.deepEqual(calls, [1, 3]);
});
