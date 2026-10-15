import test from "node:test";
import assert from "node:assert/strict";
import {
  clampBrightness, displayBrightness, iconFor, mergeRooms, createThrottle, parseRoute, routeHash, lightsSummary, shouldPoll,
  PALETTE, isHexColor, accentFor, lightsAccent, LIGHTS_ON_COLOR, LIGHTS_OFF_COLOR, mergeAll, setAllRooms,
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

test("parseRoute", () => {
  assert.deepEqual(parseRoute(""), { view: "home" });
  assert.deepEqual(parseRoute("#/"), { view: "home" });
  assert.deepEqual(parseRoute("#/lights"), { view: "lights" });
  assert.deepEqual(parseRoute("#/app/0", 2), { view: "app", index: 0 });
  assert.deepEqual(parseRoute("#/app/1", 2), { view: "app", index: 1 });
  assert.deepEqual(parseRoute("#/app/2", 2), { view: "home" });
  assert.deepEqual(parseRoute("#/app/3"), { view: "app", index: 3 });
  assert.deepEqual(parseRoute("#/app/x", 2), { view: "home" });
  assert.deepEqual(parseRoute("#/app/-1", 2), { view: "home" });
  assert.deepEqual(parseRoute("#/app/1/x", 2), { view: "home" });
  assert.deepEqual(parseRoute("#/nope"), { view: "home" });
  assert.deepEqual(parseRoute(undefined), { view: "home" });
});

test("routeHash round-trips", () => {
  for (const r of [{ view: "home" }, { view: "lights" }, { view: "app", index: 4 }]) {
    assert.deepEqual(parseRoute(routeHash(r)), r);
  }
});

test("lightsSummary", () => {
  assert.equal(lightsSummary(null), "");
  assert.equal(lightsSummary([]), "No rooms");
  assert.equal(lightsSummary([{ on: false }, { on: false }]), "All off");
  assert.equal(lightsSummary([{ on: true }, { on: false }, { on: true }]), "2 of 3 on");
  assert.equal(lightsSummary([{ on: true }]), "1 of 1 on");
});

test("shouldPoll", () => {
  assert.equal(shouldPoll("home", false), true);
  assert.equal(shouldPoll("lights", false), true);
  assert.equal(shouldPoll("app", false), false);
  assert.equal(shouldPoll("home", true), false);
  assert.equal(shouldPoll("lights", true), false);
});

test("isHexColor", () => {
  for (const ok of ["#fff", "#A855F7", "#0af"]) assert.equal(isHexColor(ok), true);
  for (const bad of ["fff", "#ff", "#ffff", "red", "#12345g", "#fff;x", "", null, 5]) {
    assert.equal(isHexColor(bad), false);
  }
});

test("accentFor uses own color or palette by index", () => {
  assert.equal(accentFor({ color: "#123456" }, 0), "#123456");
  assert.equal(accentFor({}, 0), PALETTE[0]);
  assert.equal(accentFor({ color: "bogus" }, 1), PALETTE[1]);
  assert.equal(accentFor({}, PALETTE.length + 2), PALETTE[2]);
});

test("lightsAccent", () => {
  assert.equal(lightsAccent(null), LIGHTS_OFF_COLOR);
  assert.equal(lightsAccent([{ on: false }]), LIGHTS_OFF_COLOR);
  assert.equal(lightsAccent([{ on: false }, { on: true }]), LIGHTS_ON_COLOR);
});

test("mergeAll holds local state inside the window", () => {
  const polled = { id: "a", on: true };
  assert.equal(mergeAll(null, null, undefined, 10000), null);
  assert.deepEqual(mergeAll({ id: "a", on: false }, polled, 9000, 10000, 2000), { id: "a", on: false });
  assert.deepEqual(mergeAll({ id: "a", on: false }, polled, 7000, 10000, 2000), polled);
  assert.deepEqual(mergeAll(null, polled, 9000, 10000, 2000), polled);
});

test("setAllRooms sets on and keeps the rest", () => {
  const rooms = [{ id: "a", on: true, brightness: 30 }, { id: "b", on: false, brightness: null }];
  assert.deepEqual(setAllRooms(rooms, false).map((r) => r.on), [false, false]);
  assert.deepEqual(setAllRooms(rooms, true)[0], { id: "a", on: true, brightness: 30 });
  assert.equal(rooms[0].on, true);
});
