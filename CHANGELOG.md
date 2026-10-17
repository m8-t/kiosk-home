# Changelog

All notable changes to this project are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is below
1.0, a minor release may change behaviour.

Each release is published as a container image:
`ghcr.io/m8-t/kiosk-home:<version>`.

## [0.5.0] - 2026-10-17

### Added

- `KIOSK_HUE_BRIDGE_ID` turns on the Hue Bridge certificate check: the certificate must be
  signed by Signify's Hue CA (bundled as `kiosk/hue_ca.pem`) and issued to that bridge id,
  so nobody on the network can pose as the bridge and catch the app key. Without it the
  certificate is not checked, as before, and a warning is logged at start.

### Changed

- `POST /api/rooms/{id}` and `POST /api/all` answer 415 unless the body is sent as
  `application/json`, and 403 when the browser marks the request as cross-site
  (`Sec-Fetch-Site`, or an `Origin` from another host). Before, a web page opened anywhere
  on the network could switch the lights with a plain-text POST.
- Open dashboards share Hue reads: the room overview is cached for 1.5 seconds and
  refreshed right after every switch or dim.
- An unknown room id re-reads the rooms from the bridge at most once every 10 seconds, and
  a failed re-read no longer forgets the known rooms.

## [0.4.0] - 2026-10-15

### Added

- `KIOSK_LIGHTS` (default `true`; accepts true/false/1/0/yes/no) makes the Hue lights part
  optional. When `false`, the Hue host and app key are not required, the Hue bridge is
  never contacted, `/api/rooms`, `/api/rooms/{id}` and `/api/all` return 404, and the page
  shows no Lights tile or view and does not poll.
- `GET /api/config` returns `{"lights": bool}` so the page knows whether to show lights.

### Changed

- README rewritten: screenshots, quick start with and without Hue, how to add tiles,
  colours and icons, and a note that this is a hobby project.
- Hue app key instructions no longer use `generatekey`, which some bridges reject.

## [0.3.0] - 2026-10-15

### Added

- "All lights" switch at the top of the Lights view, backed by the bridge_home group and
  the new `POST /api/all` endpoint. Room cards update at once when it is used.
- Optional `color` field in `links.json` (hex `#rgb` or `#rrggbb`), validated by the
  server.

### Changed

- The dashboard tiles are centered horizontally and vertically and are larger.
- Every tile has its own accent color (own `color` or a palette by position) used for the
  card tint, glow and icon. Text icons are drawn in the accent color at the size of the
  built-in icon. The Lights icon is amber when any room is on and grey when all are off.
- Page background is a subtle gradient instead of flat.
- `GET /api/rooms` now returns an object `{"all": ..., "rooms": [...]}` instead of a list.

## [0.2.0] - 2026-10-15

### Added

- Application dashboard as start page: large app tiles for every link plus a built-in
  Lights tile with a live summary of the rooms that are on.
- Apps open inside the page in a full-size iframe below a top bar with a Home button.
  Opened apps stay loaded, so returning to them is instant.
- Optional `open` field in `links.json`: `"frame"` (default) or `"tab"`. Other values are
  rejected.
- Hash routes (`#/`, `#/lights`, `#/app/<index>`) so the Android back button returns to
  the dashboard.

### Changed

- Room cards moved to their own Lights view with the same top bar.
- Room polling runs only while the page is visible and the dashboard or Lights view is
  shown.

## [0.1.0] - 2026-10-15

### Added

- Start page for a wall tablet with link tiles read from a JSON file, re-read on every
  request so ConfigMap updates apply without a restart.
- One card per Philips Hue room with an on/off toggle and a brightness slider.
- Server endpoints for links, rooms and room updates, backed by the Hue CLIP v2 API.
- Container image, Kubernetes manifests and a release workflow.
