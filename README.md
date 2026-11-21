# Prometheus Overlay for Linux (unofficial)

Run the **Prometheus Overlay** for *The Isle Evrima* on Linux.

Prometheus Overlay is the great in-game overlay made by the Prometheus Isle community
([prometheusisle.hu](https://prometheusisle.hu), Ascension Studio). It only ships for Windows, and it does not work under
Wine/Proton: WebView2 can't do a transparent background there, so the overlay covers the game with black.

We reverse engineered how the Windows app talks to its server and wrote a small Linux core, so the **original overlay** runs
natively on Linux. We don't take any credit for the overlay. The UI, the features, and the server are all the work of the
Prometheus team. This repo only makes their overlay usable for Linux players.

- **Not affiliated** with Prometheus Isle, Ascension Studio, or the developers of The Isle. Don't send bugs from this port
  to them, open an issue here.
- **The overlay itself is not in this repo.** `linux/fetch_frontend.py` downloads the official installer from prometheusisle.hu,
  checks its signature with the developers' own key, and unpacks the overlay's UI on your machine. You always run their
  current, unmodified version.
- If the Prometheus developers want this repo changed or taken down, open an issue and we'll do it.

## How it works

The Windows app is a Tauri program: a web UI (HTML/JS) plus a Rust core for the window, hotkeys, pairing, and the server
connection. The UI talks to the core through one small file (`bridge.js`). This port keeps the UI as it is and replaces the
core:

- `linux/prometheus_overlay.py`: the Linux core. It shows the UI in a transparent, click-through GTK4 + WebKitGTK window on
  the Wayland *layer-shell* overlay layer (above fullscreen games), and implements the same commands and events as the Rust
  core (pairing, the frame stream, the API, settings, hotkeys).
- Hotkeys are read passively from `/dev/input`, so the game still gets every key. They work only while the game window has
  focus (or in edit mode).
- The overlay never touches the game process (no injection, no memory reading). Everything comes from the Prometheus server,
  the same as on Windows.
- `re/`: the reverse-engineering tools and notes (`re/NOTES.md`).

## Requirements

- A Wayland compositor with layer-shell. Tested on **niri**. Hyprland, Sway and KDE should work, but show/hide-with-the-game
  only works on niri for now (elsewhere the overlay stays visible and F9 hides it). GNOME has no layer-shell.
- Packages (Fedora names): `python3-gobject gtk4 webkitgtk6.0 gtk4-layer-shell python3-brotli python3-cryptography
  python3-evdev p7zip libsecret wl-clipboard libnotify`.
  - Arch: `python-gobject gtk4 webkitgtk-6.0 gtk4-layer-shell python-brotli python-cryptography python-evdev 7zip libsecret
    wl-clipboard libnotify`
- Your user in the `input` group, for the hotkeys: `sudo usermod -aG input $USER` and log in again.
- A keyring (GNOME Keyring or KWallet with the Secret Service API) for the pairing token.

## Install

```sh
git clone https://github.com/tadeasf/prometheus-overlay-linux.git
cd prometheus-overlay-linux
linux/fetch_frontend.py          # downloads + verifies the official installer, unpacks the overlay UI
```

Run `linux/fetch_frontend.py` again whenever a new overlay version comes out.

## Start it with the game

In Steam: The Isle, **Properties, General, Launch Options**:

```
/full/path/to/prometheus-overlay-linux/linux/steam-launch.sh %command%
```

The overlay starts with the game and stops when the game exits. You can also start it by hand: `linux/prometheus_overlay.py`.

First run: press **F8** in the game (edit mode), click **Pair**, and approve on the website. The token is stored in your
keyring.

## Hotkeys

The overlay's own hotkeys from its settings: F8 edit mode, F9 hide, F5 Hub, F3 wheel, F2 companion panel, F10 / Shift+F10
zoom, F11 big map, and so on. Changed keys apply right away. Voice push-to-talk keys (Mouse4 / Mouse5) are read too, but see
the voice chat note below. Without `/dev/input` access you can bind keys in your compositor to
`linux/prometheus_overlay.py hotkey <action>` instead.

## Settings and troubleshooting

- Settings: `~/.config/PrometheusOverlay/settings.json`. Log: `~/.config/PrometheusOverlay/logs/overlay.log`.
- Environment variables: `PROMETHEUS_ALWAYS_VISIBLE=1` (show without the game), `PROMETHEUS_DEVTOOLS=1` (WebKit inspector,
  right click), `PROMETHEUS_HEADLESS=1` (core only, open the printed URL in a browser), `PROMETHEUS_GAME_RE=<regex>` (how to
  recognise the game window).
- Self-test: `linux/prometheus_overlay.py --selftest`.

## Not ported (yet)

**Voice chat** does not work on Fedora: its WebKitGTK is built without WebRTC (`RTCPeerConnection` does not exist), which the
overlay's voice chat (LiveKit) needs. Microphone access itself works. Distros whose WebKitGTK 6.0 has WebRTC enabled may work,
untested.

Also: Discord presence, the OBS stream page, the "press a key" capture in the push-to-talk setting, the admin window, in-app
updates (re-run `fetch_frontend.py`).

## License

The code in this repository (the Linux core, scripts and notes) is released into the public domain (see `LICENSE`).
Prometheus Overlay itself, which `fetch_frontend.py` downloads, belongs to its authors and is not covered by this license.
