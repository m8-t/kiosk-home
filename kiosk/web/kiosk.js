import {
  POLL_MS, HOLD_MS, SLIDER_INTERVAL_MS, clampBrightness, displayBrightness, iconFor,
  mergeRooms, createThrottle,
} from "./logic.js";

const POWER_SVG =
  '<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2" ' +
  'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v9"/>' +
  '<path d="M6.3 6.8a8 8 0 1 0 11.4 0"/></svg>';

const $ = (id) => document.getElementById(id);
const statusEl = $("status");
const roomsEl = $("rooms");

let rooms = [];
const lastChange = {};
const cards = new Map();
let statusTimer = null;
let pollTimer = null;
let polling = false;

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
    const polled = await api("/api/rooms");
    rooms = mergeRooms(rooms, polled, lastChange, Date.now(), HOLD_MS);
    render();
    clearError();
  } catch (err) {
    showError(err.message);
  } finally {
    polling = false;
  }
}

function startPolling() {
  stopPolling();
  poll();
  pollTimer = setInterval(poll, POLL_MS);
}

function stopPolling() {
  clearInterval(pollTimer);
  pollTimer = null;
}

function renderLinks(links) {
  const ul = $("links");
  ul.replaceChildren();
  for (const link of links) {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.className = "tile";
    a.href = link.url;
    const icon = document.createElement("span");
    icon.className = "tile-icon";
    icon.textContent = iconFor(link);
    const title = document.createElement("span");
    title.className = "tile-title";
    title.textContent = link.title;
    a.append(icon, title);
    li.append(a);
    ul.append(li);
  }
  $("links-section").hidden = links.length === 0;
}

async function loadLinks() {
  try {
    renderLinks(await api("/api/links"));
  } catch (err) {
    showError(err.message);
  }
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopPolling();
  else { loadLinks(); startPolling(); }
});

loadLinks();
if (!document.hidden) startPolling();
