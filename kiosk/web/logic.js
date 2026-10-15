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

// Hash route. Unknown hashes and out-of-range app indexes fall back to home.
export function parseRoute(hash, linkCount = Infinity) {
  const h = String(hash || "").replace(/^#/, "");
  if (h === "/lights") return { view: "lights" };
  const m = /^\/app\/(\d+)$/.exec(h);
  if (m) {
    const index = Number(m[1]);
    if (Number.isSafeInteger(index) && index < linkCount) return { view: "app", index };
  }
  return { view: "home" };
}

export function routeHash(route) {
  if (route.view === "lights") return "#/lights";
  if (route.view === "app") return `#/app/${route.index}`;
  return "#/";
}

// Text for the Lights tile. null means rooms are not loaded yet.
export function lightsSummary(rooms) {
  if (!rooms) return "";
  if (rooms.length === 0) return "No rooms";
  const on = rooms.filter((r) => r.on).length;
  return on === 0 ? "All off" : `${on} of ${rooms.length} on`;
}

// Poll only while the page is visible and the dashboard or lights view is shown.
export function shouldPoll(view, hidden) {
  return !hidden && (view === "home" || view === "lights");
}
