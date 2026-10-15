# kiosk-home

A small start page for a wall tablet (Android, Chromium WebView, 10 inch landscape, touch
only, portrait works too). It shows:

1. A home dashboard: a grid of large app tiles, one per entry of the links file plus a
   built-in Lights tile that shows a live summary ("2 of 3 on" or "All off").
2. A Lights view: an "All lights" switch on top, then one card per Philips Hue room with an
   on/off toggle and a brightness slider (1 to 100 percent). Rooms only: no single lights, colors or scenes.
3. An app view: a link opens inside the page in a full-size iframe below a thin top bar
   with a Home button. Opened apps stay loaded (hidden) so returning to them is instant.

Views use hash routes (`#/`, `#/lights`, `#/app/<index>`), so the Android back button
walks back to the dashboard. `/api/rooms` is polled every 5 seconds only while the page is
visible and the dashboard or Lights view is shown.

The server is a single aiohttp process that serves the page and talks to the Hue bridge
over the local CLIP v2 API. The app key never reaches the browser.

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `KIOSK_HUE_HOST` | required | IP address or host name of the Hue bridge |
| `KIOSK_HUE_APP_KEY` | required | Hue application key (secret) |
| `KIOSK_LINKS_FILE` | `/config/links.json` | Path of the links file |
| `KIOSK_PORT` | `8080` | Listen port |
| `KIOSK_LOG_LEVEL` | `info` | `debug`, `info`, `warning` or `error` |

The server exits with a message if a required variable is missing. The app key is never
logged. TLS verification is disabled for requests to the Hue bridge only, because the
bridge uses a self-signed certificate.

### Links file

A JSON array. `icon` is optional; without it the first letter of the title is used. URLs
must be http or https. `open` is optional: `"frame"` (default) opens the app inside the
page, `"tab"` navigates to the URL like a plain link. Any other value is an error. Sites
that send `X-Frame-Options` or a restrictive `Content-Security-Policy: frame-ancestors`
cannot be framed; use `"open": "tab"` for those. `color` is optional: a CSS hex color
(`#rgb` or `#rrggbb`) used as the tile accent. Without it, tiles take a color from a fixed
palette by position. The Lights tile is amber (grey when all lights are off). The file is read on every request, so a changed ConfigMap applies
without a restart. A missing file means no tiles.

```json
[
  {"title": "Music", "url": "https://music.example.org", "icon": "M", "color": "#2dd4bf"},
  {"title": "Router", "url": "http://192.0.2.1", "open": "tab"}
]
```

## Create a Hue app key

Press the link button on the bridge, then within 30 seconds run:

```
curl -sk -X POST https://<hue-ip>/api -d '{"devicetype":"kiosk-home#tablet","generatekey":true}'
```

Use the `username` field of the response as `KIOSK_HUE_APP_KEY`.

## HTTP API

- `GET /api/links`
- `GET /api/rooms` returns `{"all": {id, on} | null, "rooms": [{id, name, on, brightness}]}`
  with rooms sorted by name. `id` is the grouped light id. `all` is the bridge_home group
  (on if any light is on) and is null if the bridge has none.
- `POST /api/all` with `{"on": true}` or `{"on": false}` switches every light with one
  request to the bridge. 404 if the bridge has no all-lights group, 400 for bad bodies.
- `POST /api/rooms/{id}` with `{"on": true}` and/or `{"brightness": 42}`. Setting
  brightness on a room that is off also turns it on. Unknown room 404, bad values 400,
  Hue failures 502.
- `GET /healthz` does not contact the bridge.

## Run locally

Needs Python 3.11 or newer with aiohttp.

```
KIOSK_HUE_HOST=192.0.2.196 KIOSK_HUE_APP_KEY=... KIOSK_LINKS_FILE=./links.json \
  python -m kiosk
```

Open http://localhost:8080/.

## Tests

```
python -m pytest -q -p no:cacheprovider tests
node --test tests/js/*.test.mjs
```

## Deploy

The image is `ghcr.io/m8-t/kiosk-home:<version>`, built by GitHub Actions when a `v*.*.*`
tag is pushed. The tag is refused unless `CHANGELOG.md` has a section for that version,
and that section becomes the release notes.

Kubernetes manifests are in `deploy/kubernetes/`:

1. Create the secret from `secret.example.yaml` (or the equivalent `k create secret`
   command in it) in the target namespace.
2. Edit `KIOSK_HUE_HOST` and the example links in `configmap.yaml`.
3. `k apply -k deploy/kubernetes/`
4. Expose it with `httproute.example.yaml` or `ingress.example.yaml`.

There is no authentication in the app. Keep it on the LAN or put it behind your own
access control.

## License

WTFPL, see `LICENSE`.
