export const POLL_MS = 5000;
export const HOLD_MS = 2000;
export const SLIDER_INTERVAL_MS = 1000;

export function clampBrightness(value) {
  const n = Math.round(Number(value));
  if (!Number.isFinite(n)) return 1;
  return Math.min(100, Math.max(1, n));
}

// Brightness to show on a slider; rooms without dimming info fall back to full.
export function displayBrightness(room) {
  return room.brightness == null ? 100 : clampBrightness(room.brightness);
}

export function iconFor(link) {
  const icon = (link.icon || "").trim();
  if (icon) return icon;
  const first = Array.from((link.title || "").trim())[0];
  return first ? first.toUpperCase() : "?";
}

// Merge a poll result into local rooms. Rooms changed locally within holdMs keep
// their local state so sliders do not jump back; rooms that vanished are dropped.
export function mergeRooms(local, polled, lastChange, now, holdMs = HOLD_MS) {
  const localById = new Map(local.map((r) => [r.id, r]));
  return polled.map((room) => {
    const touched = lastChange[room.id];
    const mine = localById.get(room.id);
    if (mine && touched !== undefined && now - touched < holdMs) return mine;
    return room;
  });
}

// Trailing-edge throttle: first call fires at once, later calls inside the interval
// collapse into one call with the latest arguments. flush() fires a pending call now.
export function createThrottle(fn, intervalMs, deps = {}) {
  const now = deps.now || Date.now;
  const setTimer = deps.setTimer || setTimeout;
  const clearTimer = deps.clearTimer || clearTimeout;
  let last = -Infinity;
  let timer = null;
  let pending = null;

  function fire(args) {
    last = now();
    fn(...args);
  }

  function call(...args) {
    const wait = last + intervalMs - now();
    if (wait <= 0 && timer === null) {
      fire(args);
      return;
    }
    pending = args;
    if (timer === null) {
      timer = setTimer(() => {
        timer = null;
        const a = pending;
        pending = null;
        if (a) fire(a);
      }, Math.max(wait, 0));
    }
  }

  function flush(...args) {
    if (timer !== null) {
      clearTimer(timer);
      timer = null;
    }
    pending = null;
    fire(args);
  }

  return { call, flush };
}
