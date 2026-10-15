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
export function parseRoute(hash, linkCount = Infinity, lights = true) {
  const h = String(hash || "").replace(/^#/, "");
  if (h === "/lights") return lights ? { view: "lights" } : { view: "home" };
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
export function shouldPoll(view, hidden, lights = true) {
  return lights && !hidden && (view === "home" || view === "lights");
}

export const PALETTE = ["#a78bfa", "#2dd4bf", "#f472b6", "#4ade80", "#fb923c"];
export const LIGHTS_ON_COLOR = "#fbbf24";
export const LIGHTS_OFF_COLOR = "#7b8494";

export function isHexColor(value) {
  return typeof value === "string" && /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(value);
}

// Accent for a links.json tile: its own valid color, else a palette entry by index.
export function accentFor(link, index) {
  if (link && isHexColor(link.color)) return link.color;
  return PALETTE[((index % PALETTE.length) + PALETTE.length) % PALETTE.length];
}

export function lightsAccent(rooms) {
  return rooms && rooms.some((r) => r.on) ? LIGHTS_ON_COLOR : LIGHTS_OFF_COLOR;
}

// Same hold rule as mergeRooms, for the all-lights switch state ({id, on} or null).
export function mergeAll(local, polled, touchedAt, now, holdMs = HOLD_MS) {
  if (!polled) return null;
  if (local && touchedAt !== undefined && now - touchedAt < holdMs) return { ...polled, on: local.on };
  return polled;
}

// Rooms with every room set on or off; brightness is left as is.
export function setAllRooms(rooms, on) {
  return rooms.map((r) => ({ ...r, on }));
}

// Dashboard tiles in order: the built-in Lights tile (only when enabled), then every link.
export function tileList(links, lights) {
  const tiles = links.map((link, index) => ({ kind: "app", index, link }));
  return lights ? [{ kind: "lights" }, ...tiles] : tiles;
}
