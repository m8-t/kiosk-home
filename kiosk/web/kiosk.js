import {
  POLL_MS, HOLD_MS, SLIDER_INTERVAL_MS, clampBrightness, displayBrightness, iconFor,
  mergeRooms, createThrottle, parseRoute, routeHash, lightsSummary, shouldPoll,
  accentFor, lightsAccent, mergeAll, setAllRooms, tileList, LIGHTS_ON_COLOR,
} from "./logic.js";

const POWER_SVG =
  '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" ' +
  'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v9"/>' +
  '<path d="M6.3 6.8a8 8 0 1 0 11.4 0"/></svg>';

const HOME_SVG =
  '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" ' +
  'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 11l9-8 9 8"/>' +
  '<path d="M5 10v10h5v-6h4v6h5V10"/></svg>';
const BULB_SVG =
  '<svg viewBox="0 0 24 24" width="72" height="72" fill="none" stroke="currentColor" stroke-width="1.5" ' +
  'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18h6"/><path d="M10 21h4"/>' +
  '<path d="M12 3a6 6 0 0 0-3.6 10.8c.7.6 1.1 1.4 1.1 2.2h5c0-.8.4-1.6 1.1-2.2A6 6 0 0 0 12 3z"/></svg>';

const $ = (id) => document.getElementById(id);
const statusEl = $("status");
const roomsEl = $("rooms");

let rooms = [];
const lastChange = {};
const cards = new Map();
let statusTimer = null;
let pollTimer = null;
let polling = false;
let links = [];
let lightsEnabled = true;
let route = { view: "home" };
let roomsLoaded = false;
let allState = null;
let lightsTile = null;
const ALL_KEY = "__all";
let lightsSummaryEl = null;
const frames = new Map();

function showError(message) {
  statusEl.textContent = message;
  statusEl.hidden = false;
  clearTimeout(statusTimer);
  statusTimer = setTimeout(() => { statusEl.hidden = true; }, 6000);
}

function clearError() {
  clearTimeout(statusTimer);
  statusEl.hidden = true;
}

async function api(path, options) {
  let resp;
  try {
    resp = await fetch(path, options);
  } catch {
    throw new Error("Server unreachable");
  }
  let data = null;
  try { data = await resp.json(); } catch { /* not JSON */ }
  if (!resp.ok) throw new Error((data && data.error) || `Request failed (${resp.status})`);
  return data;
}

function post(id, body) {
  return api(`/api/rooms/${encodeURIComponent(id)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function roomById(id) {
  return rooms.find((r) => r.id === id);
}

function touch(id) {
  lastChange[id] = Date.now();
}

function buildCard(room) {
  const li = document.createElement("li");
  li.className = "room";
  const head = document.createElement("div");
  head.className = "room-head";
  const text = document.createElement("div");
  const name = document.createElement("div");
  name.className = "room-name";
  const value = document.createElement("div");
  value.className = "room-value";
  text.append(name, value);
  const power = document.createElement("button");
  power.type = "button";
  power.className = "power";
  power.setAttribute("aria-label", "Toggle");
  const ic = document.createElement("span");
  ic.className = "ic";
  ic.innerHTML = POWER_SVG;
  power.append(ic);
  head.append(text, power);
  const slider = document.createElement("input");
  slider.type = "range";
  slider.className = "slider";
  slider.min = "1";
  slider.max = "100";
  slider.step = "1";
  li.append(head, slider);

  const id = room.id;
  const send = createThrottle((value) => sendBrightness(id, value), SLIDER_INTERVAL_MS);

  power.addEventListener("click", () => toggle(id));
  slider.addEventListener("input", () => {
    const v = clampBrightness(slider.value);
    const r = roomById(id);
    if (!r) return;
    r.brightness = v;
    r.on = true;
    touch(id);
    paint(r);
    send.call(v);
  });
  slider.addEventListener("change", () => {
    const v = clampBrightness(slider.value);
    touch(id);
    send.flush(v);
  });

  const card = { li, name, value, power, slider, dragging: false };
  slider.addEventListener("pointerdown", () => { card.dragging = true; });
  const stop = () => { card.dragging = false; };
  slider.addEventListener("pointerup", stop);
  slider.addEventListener("pointercancel", stop);
  return card;
}

function paint(room) {
  const card = cards.get(room.id);
  if (!card) return;
  const level = displayBrightness(room);
  card.name.textContent = room.name;
  card.value.textContent = room.on ? `${level}%` : "Off";
  card.power.setAttribute("aria-pressed", String(room.on));
  card.li.classList.toggle("off", !room.on);
  if (!card.dragging) {
    card.slider.value = String(level);
  }
  card.slider.style.setProperty("--fill", `${((level - 1) / 99) * 100}%`);
  card.slider.setAttribute("aria-label", `${room.name} brightness`);
}

function render() {
  const seen = new Set(rooms.map((r) => r.id));
  for (const [id, card] of cards) {
    if (!seen.has(id)) {
      card.li.remove();
      cards.delete(id);
    }
  }
  rooms.forEach((room, i) => {
    let card = cards.get(room.id);
    if (!card) {
      card = buildCard(room);
      cards.set(room.id, card);
    }
    if (roomsEl.children[i] !== card.li) roomsEl.insertBefore(card.li, roomsEl.children[i] || null);
    paint(room);
  });
  $("rooms-empty").hidden = rooms.length > 0;
  paintAll();
}

function paintAll() {
  $("all-row").hidden = !allState;
  if (!allState) return;
  $("all-power").setAttribute("aria-pressed", String(allState.on));
  $("all-value").textContent = allState.on ? "On" : "Off";
}

async function toggleAll() {
  if (!allState) return;
  const target = !allState.on;
  const snapshot = { all: allState, rooms };
  allState = { ...allState, on: target };
  rooms = setAllRooms(rooms, target);
  const stamp = Date.now();
  lastChange[ALL_KEY] = stamp;
  for (const r of rooms) lastChange[r.id] = stamp;
  render();
  updateSummary();
  try {
    await api("/api/all", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ on: target }),
    });
  } catch (err) {
    allState = snapshot.all;
    rooms = snapshot.rooms;
    delete lastChange[ALL_KEY];
    for (const r of rooms) delete lastChange[r.id];
    render();
    updateSummary();
    showError(err.message);
  }
}

async function toggle(id) {
  const room = roomById(id);
  if (!room) return;
  const before = { on: room.on, brightness: room.brightness };
  room.on = !room.on;
  touch(id);
  paint(room);
  try {
    await post(id, { on: room.on });
    touch(id);
  } catch (err) {
    const r = roomById(id);
    if (r) Object.assign(r, before);
    delete lastChange[id];
    if (r) paint(r);
    showError(err.message);
  }
}

async function sendBrightness(id, value) {
  try {
    await post(id, { brightness: value });
    touch(id);
  } catch (err) {
    delete lastChange[id];
    showError(err.message);
  }
}

async function poll() {
  if (polling) return;
  polling = true;
  try {
    const data = await api("/api/rooms");
    const now = Date.now();
    rooms = mergeRooms(rooms, data.rooms, lastChange, now, HOLD_MS);
    allState = mergeAll(allState, data.all, lastChange[ALL_KEY], now, HOLD_MS);
    roomsLoaded = true;
    render();
    updateSummary();
    clearError();
  } catch (err) {
    showError(err.message);
  } finally {
    polling = false;
  }
}

function syncPolling() {
  const want = shouldPoll(route.view, document.hidden, lightsEnabled);
  if (want && pollTimer === null) {
    poll();
    pollTimer = setInterval(poll, POLL_MS);
  } else if (!want && pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function svgIcon(svg, extra = "") {
  const ic = document.createElement("span");
  ic.className = `ic ${extra}`.trim();
  ic.innerHTML = svg;
  return ic;
}

function updateSummary() {
  if (lightsTile) lightsTile.style.setProperty("--a", lightsAccent(roomsLoaded ? rooms : null));
  if (lightsSummaryEl) lightsSummaryEl.textContent = lightsSummary(roomsLoaded ? rooms : null);
}

function buildTile(tag, iconNode, titleText, accent) {
  const el = document.createElement(tag);
  el.className = "tile";
  el.style.setProperty("--a", accent);
  const icon = document.createElement("span");
  icon.className = "tile-icon";
  icon.append(iconNode);
  const title = document.createElement("span");
  title.className = "tile-title";
  title.textContent = titleText;
  el.append(icon, title);
  return el;
}

function textIcon(text) {
  const span = document.createElement("span");
  span.className = "tile-glyph";
  span.textContent = text;
  return span;
}

function renderTiles() {
  const ul = $("tiles");
  ul.replaceChildren();
  lightsTile = null;
  lightsSummaryEl = null;

  for (const tile of tileList(links, lightsEnabled)) {
    const li = document.createElement("li");
    if (tile.kind === "lights") {
      lightsTile = buildTile("button", svgIcon(BULB_SVG, "ic-xl"), "Lights", LIGHTS_ON_COLOR);
      lightsTile.type = "button";
      lightsSummaryEl = document.createElement("span");
      lightsSummaryEl.className = "tile-sub";
      lightsTile.append(lightsSummaryEl);
      lightsTile.addEventListener("click", () => go({ view: "lights" }));
      li.append(lightsTile);
    } else {
      const { index, link } = tile;
      const icon = textIcon(iconFor(link));
      const accent = accentFor(link, index);
      let el;
      if (link.open === "tab") {
        el = buildTile("a", icon, link.title, accent);
        el.href = link.url;
      } else {
        el = buildTile("button", icon, link.title, accent);
        el.type = "button";
        el.addEventListener("click", () => go({ view: "app", index }));
      }
      li.append(el);
    }
    ul.append(li);
  }
  updateSummary();
}

function frameFor(index) {
  const link = links[index];
  let entry = frames.get(index);
  if (entry && entry.url !== link.url) {
    entry.el.remove();
    frames.delete(index);
    entry = null;
  }
  if (!entry) {
    const el = document.createElement("iframe");
    el.setAttribute("allow", "autoplay; fullscreen");
    el.title = link.title;
    el.src = link.url;
    el.hidden = true;
    $("frames").append(el);
    entry = { el, url: link.url };
    frames.set(index, entry);
  }
  return entry.el;
}

function applyRoute() {
  let r = parseRoute(location.hash, links.length, lightsEnabled);
  if (r.view === "app" && links[r.index].open === "tab") r = { view: "home" };
  route = r;
  const isApp = r.view === "app";
  $("view-home").hidden = r.view !== "home";
  $("view-lights").hidden = r.view !== "lights";
  $("frames").hidden = !isApp;
  $("bar").hidden = r.view === "home";
  for (const entry of frames.values()) entry.el.hidden = true;
  if (isApp) {
    $("bar-title").textContent = links[r.index].title;
    frameFor(r.index).hidden = false;
  } else if (r.view === "lights") {
    $("bar-title").textContent = "Lights";
  }
  syncPolling();
}

function go(target) {
  history.pushState({ nav: true }, "", routeHash(target));
  applyRoute();
}

function goHome() {
  if (history.state && history.state.nav) {
    history.back();
  } else {
    history.replaceState(null, "", routeHash({ view: "home" }));
    applyRoute();
  }
}

async function loadLinks() {
  try {
    const [config, list] = await Promise.all([api("/api/config"), api("/api/links")]);
    lightsEnabled = config.lights !== false;
    links = list;
  } catch (err) {
    showError(err.message);
    applyRoute();
    return;
  }
  for (const [index, entry] of frames) {
    if (!links[index] || links[index].url !== entry.url) {
      entry.el.remove();
      frames.delete(index);
    }
  }
  renderTiles();
  applyRoute();
}

$("home-ic").innerHTML = HOME_SVG;
$("home-btn").addEventListener("click", goHome);
$("all-power").append(svgIcon(POWER_SVG, "ic-lg"));
$("all-power").addEventListener("click", toggleAll);
window.addEventListener("popstate", applyRoute);

document.addEventListener("visibilitychange", () => {
  if (document.hidden) syncPolling();
  else if (route.view === "app") syncPolling();
  else {
    loadLinks();
    syncPolling();
  }
});

loadLinks();
