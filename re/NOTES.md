# Reverse engineering notes: Prometheus Overlay 0.43.3

Target: `prometheus-overlay.exe` from `PrometheusOverlay_0.43.3_x64-setup.exe` (NSIS, 7z-extractable),
sha256 `a9b3419c775f0c6647ccb94b515ef248d91afd8d77898d2a66255a525eb7cbf8`. Rust (rustc 88d9e12a), Tauri 2.12.1, wry/WebView2,
reqwest 0.12/0.13 + rustls-platform-verifier, keyring (WinCredential). Crate `overlay_lib`; modules: lib, net, stream,
pairing, update, hotkeys, tray, presence, ptt, adminsub, adminwin.

Tools here: `extract_assets.py` (Tauri phf asset table -> brotli -> files), `fnstrings.py` (strings referenced by the function
containing an address, bounds from .pdata), `near.py` / `xref.py` (code refs to a literal). Disassembly: `objdump -d -M intel`.

## Observed (static)
- Frontend: 328 assets, entry `index.html` -> `app.js` (ES modules). All native calls go through `bridge.js`
  (`window.__TAURI__.core.invoke` / `event.listen`). 23 commands, 16 events (list: `grep -o 'invoke("[a-z_]*"'`).
- `obs-shim.js` replaces `__TAURI__` for the OBS page via `/obs/settings` + SSE `/obs/events`; the Linux shim copies this idea.
- Rust format templates use the compact encoding `\xc0<len><literal>` with args in between.
- Stream (fn 0x140173c10): `{server}/api/overlay/stream?since={u64}&lang={lang}&player=1`, headers
  `Authorization: Bearer <token>`, `X-Overlay-Version: 0.43.3`. Each message is parsed to a serde_json Object; the core inserts
  `_rx` (receive ms) and `_conn` and emits it as Tauri event `frame`. Backlog check: `(_rx - ts) - min(_rx - ts) > 15000` ->
  "behind the server: dropping the backlog, reconnecting".
- Pairing: `POST /api/overlay/pair/start?lang=` -> fields `device_code user_code interval expires_in verification_uri(_complete)`;
  `POST /api/overlay/pair/poll` reads `status`, `token`; token saved to keyring.
- Server base `https://prometheusisle.hu` (alt `theisle.ascensionstudio.hu`); settings in `%APPDATA%\PrometheusOverlay\settings.json`
  (written via `.json.tmp`, `.json.bak`).
- Hotkey `edit` is handled by the core (no JS listener); the rest by the frontend (`hotkey` event `{action, pressed}`).

## Observed (live, read-only requests)
- `GET /api/overlay/version?channel=stable&current=0.43.3` -> 204 (no update).
- `GET /api/overlay/stream` without token -> 401 `{"detail":"token"}` (FastAPI).
- `GET pair/start`, `GET pair/poll` -> 405 Allow: POST. `POST pair/poll {"device_code":"x"}` -> 200 `{"status":"expired"}`.

## Inferred / unknown
- Stream framing (SSE vs NDJSON) not confirmed: the Linux core accepts both. Confirm with the "stream connected (content-type)"
  and "first frame keys" log lines after pairing.
- What `since` holds (event id / seq / ts) is not confirmed; the core uses an SSE `id:` or a numeric `seq`, else 0.
- Pairing response status names other than `expired`, and the success payload beyond `token`, are unconfirmed.
- Confirmed from call sites + live: `api` resolves to `{status, body}` for every HTTP status (pages test `status === 200/409/403/...`);
  `fetch_public` resolves to the bare body. Stream is `text/event-stream`; frame keys: admins_on_duty live marks notices offline
  player server time ts weather.
