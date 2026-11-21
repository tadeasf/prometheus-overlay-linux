#!/usr/bin/python3
"""Prometheus Overlay for Linux (niri / Wayland).

Runs the original overlay frontend (extracted from the Windows 0.43.3 build, ../frontend) in a transparent,
click-through GTK4 layer-shell window, and replaces the Rust/Tauri core with a small Python one that speaks the
same server protocol (see ../re/NOTES.md).

  prometheus_overlay.py              run the overlay
  prometheus_overlay.py hotkey ACT   press an overlay hotkey (for compositor binds when /dev/input is not readable)
  prometheus_overlay.py --selftest   check the stream parser
"""
import ctypes.util, json, os, queue, re, secrets, shutil, subprocess, sys, threading, time, urllib.error, urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SERVER = os.environ.get("PROMETHEUS_SERVER", "https://prometheusisle.hu")
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
try:  # the frontend's version (written by fetch_frontend.py): the server answers "outdated" to versions it no longer accepts
    VERSION = (FRONTEND / "VERSION").read_text().strip()
except OSError:
    VERSION = "0.43.3"
CONF = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "PrometheusOverlay"
RUNFILE = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "prometheus-overlay.json"
GAME_RE = re.compile(os.environ.get("PROMETHEUS_GAME_RE", r"steam_app_376210|TheIsle|The Isle"), re.I)
ALWAYS_VISIBLE = os.environ.get("PROMETHEUS_ALWAYS_VISIBLE") == "1"
UA = f"PrometheusOverlay/{VERSION} (Linux)"
PRESS_ONLY_GAP = 0.12  # niri binds give a press, no release: the release follows this long after


def log(level, msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}][{level}] {msg}"
    print(line, file=sys.stderr, flush=True)
    try:
        (CONF / "logs").mkdir(parents=True, exist_ok=True)
        with open(CONF / "logs" / "overlay.log", "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- token (system keyring, like the Windows build)
def token_get():
    r = subprocess.run(["secret-tool", "lookup", "service", "prometheus-overlay", "account", "token"],
                       capture_output=True, text=True)
    return r.stdout.strip() or None


def token_set(tok):
    r = subprocess.run(["secret-tool", "store", "--label=Prometheus Overlay", "service", "prometheus-overlay",
                        "account", "token"], input=tok, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"keyring: {r.stderr.strip()}")


def token_clear():
    subprocess.run(["secret-tool", "clear", "service", "prometheus-overlay", "account", "token"], capture_output=True)


# ---------------------------------------------------------------- stores (settings.json, log.json ...)
def store_path(name):
    if not re.fullmatch(r"[a-z0-9_-]+", name):
        raise ValueError(f"bad store name {name!r}")
    return CONF / f"{name}.json"


def store_load(name):
    try:
        return json.loads(store_path(name).read_text())
    except (OSError, ValueError):
        return None


def store_save(name, value):
    p = store_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False))
    os.replace(tmp, p)  # atomic: a crash mid-write keeps the old settings


# ---------------------------------------------------------------- HTTP to the server
def http(method, path, body=None, auth=True, timeout=20, envelope=False):
    """envelope=True: {status, body} for every HTTP status (the `api` command's contract); else the body, non-2xx raises."""
    url = path if path.startswith("http") else SERVER + path
    headers = {"X-Overlay-Version": VERSION, "User-Agent": UA, "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if auth and (tok := token_get()):
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
        if not envelope:
            raise RuntimeError(f"HTTP {status}: {raw[:300].decode('utf8', 'replace')}") from None
    try:
        data = json.loads(raw) if raw.strip() else None
    except ValueError:
        data = raw.decode("utf8", "replace")
    return {"status": status, "body": data} if envelope else data


def parse_stream(lines):
    """Yield (event_id, obj) from a stream that is either SSE (data:/id: lines) or one JSON object per line."""
    ev_id, data = None, []
    for raw in lines:
        line = raw.decode("utf8", "replace").rstrip("\r\n") if isinstance(raw, bytes) else raw.rstrip("\r\n")
        if not line:
            if data:
                yield ev_id, json.loads("\n".join(data))
            ev_id, data = None, []
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif line.startswith("id:"):
            ev_id = line[3:].strip()
        elif line.startswith((":", "event:", "retry:")):
            continue
        elif line.lstrip().startswith("{"):
            yield None, json.loads(line)
    if data:
        yield ev_id, json.loads("\n".join(data))


# ---------------------------------------------------------------- key names (the settings' "Shift+F10", "Mouse4")
# linux/input-event-codes.h values: no evdev import needed to parse
MODKEYS = {42: "shift", 54: "shift", 29: "ctrl", 97: "ctrl", 56: "alt", 100: "alt", 125: "super", 126: "super"}
MODNAMES = {"shift": "shift", "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "super": "super", "win": "super",
            "meta": "super", "cmd": "super"}
_F = {f"F{i}": c for i, c in enumerate([59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 87, 88], 1)}
_F.update({f"F{i}": 183 + i - 13 for i in range(13, 25)})
_LETTERS = dict(zip("QWERTYUIOP", range(16, 26))) | dict(zip("ASDFGHJKL", range(30, 39))) | dict(zip("ZXCVBNM", range(44, 51)))
KEYCODES = _F | _LETTERS | {str(d): 2 + (d - 1) % 10 for d in range(10)} | {
    "MOUSE4": 0x113, "MOUSE5": 0x114, "XBUTTON1": 0x113, "XBUTTON2": 0x114, "MOUSE3": 0x112, "MIDDLE": 0x112,
    "ESCAPE": 1, "ESC": 1, "TAB": 15, "SPACE": 57, "ENTER": 28, "RETURN": 28, "BACKSPACE": 14, "CAPSLOCK": 58,
    "INSERT": 110, "DELETE": 111, "HOME": 102, "END": 107, "PAGEUP": 104, "PAGEDOWN": 109,
    "UP": 103, "DOWN": 108, "LEFT": 105, "RIGHT": 106, "ARROWUP": 103, "ARROWDOWN": 108, "ARROWLEFT": 105, "ARROWRIGHT": 106,
    "BACKQUOTE": 41, "`": 41, "MINUS": 12, "EQUAL": 13, "-": 12, "=": 13, "[": 26, "]": 27, ";": 39, "'": 40,
    "\\": 43, ",": 51, ".": 52, "/": 53, "PAUSE": 119, "SCROLLLOCK": 70, "PRINTSCREEN": 99,
    **{f"NUM{d}": c for d, c in zip(range(10), [82, 79, 80, 81, 75, 76, 77, 71, 72, 73])},
    **{f"NUMPAD{d}": c for d, c in zip(range(10), [82, 79, 80, 81, 75, 76, 77, 71, 72, 73])},
}


def parse_key(name):
    """'Shift+F10' -> (68, frozenset({'shift'})); unknown or empty -> None."""
    if not name or not isinstance(name, str):
        return None
    parts = [p.strip() for p in name.split("+") if p.strip()]
    if not parts:
        return None
    mods = frozenset(MODNAMES[p.lower()] for p in parts[:-1] if p.lower() in MODNAMES)
    key = parts[-1].upper().removeprefix("KEY").removeprefix("DIGIT") if len(parts[-1]) > 1 else parts[-1].upper()
    code = KEYCODES.get(key)
    if code is None:
        log("WARN", f"hotkey {name!r}: unknown key name")
        return None
    return code, mods


# ---------------------------------------------------------------- the core (what the Rust side did)
class Core:
    def __init__(self):
        self.clients = []  # SSE queues
        self.lock = threading.Lock()
        self.conn = "connecting"
        self.edit = False
        self.hold = False  # the full map / wheel took the mouse
        self.game_front = ALWAYS_VISIBLE
        self.front_title = ""
        self.since = 0
        self.wake = threading.Event()
        self.window = None  # set by the GTK side
        self.pairing = None
        self.binds = None  # (keycode, mods) -> (kind, name); None = read from the settings again
        self.held = set()

    # events to the page
    def emit(self, name, payload):
        msg = f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
        with self.lock:
            for q in self.clients:
                q.put(msg)

    def set_conn(self, state):
        if state != self.conn:
            self.conn = state
            log("INFO", f"conn: {state}")
            self.emit("conn", {"state": state})

    def lang(self):
        s = store_load("settings") or {}
        return s.get("lang") or "en"

    # the frame stream
    def stream_loop(self):
        backoff, conn_no = 2, 0
        while True:
            if not token_get():
                self.set_conn("unpaired")
                self.wake.wait(5)
                self.wake.clear()
                continue
            q = urllib.parse.urlencode({"since": self.since, "lang": self.lang()})
            req = urllib.request.Request(f"{SERVER}/api/overlay/stream?{q}&player=1", headers={
                "Authorization": f"Bearer {token_get()}", "X-Overlay-Version": VERSION, "User-Agent": UA,
                "Accept": "text/event-stream, application/x-ndjson, application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    conn_no += 1
                    backoff = 2
                    self.set_conn("online")
                    log("INFO", f"stream connected ({r.headers.get('content-type')})")
                    first = True
                    for ev_id, obj in parse_stream(r):
                        if not isinstance(obj, dict):
                            continue
                        if first:
                            log("INFO", f"first frame keys: {sorted(obj)[:40]}")
                            first = False
                        # shortcut: `since` resumes from the event id or a seq field; if the server uses something
                        # else, a reconnect just starts from "now" (since=0), which loses nothing visible.
                        for cand in (ev_id, obj.get("seq"), obj.get("id") if "fast" not in obj else None):
                            if cand is not None and str(cand).isdigit():
                                self.since = int(cand)
                                break
                        obj["_rx"] = int(time.time() * 1000)
                        obj["_conn"] = conn_no
                        self.emit("frame", obj)
            except urllib.error.HTTPError as e:
                detail = e.read()[:200].decode("utf8", "replace")
                log("WARN", f"stream HTTP {e.code}: {detail}")
                if e.code == 401:
                    token_clear()  # revoked or unpaired on the website
                    self.set_conn("unpaired")
                    continue
                if e.code in (409, 410, 426) or "outdated" in detail:
                    self.set_conn("outdated")
                    time.sleep(300)
                    continue
                self.set_conn("connecting")
            except (OSError, ValueError) as e:
                log("WARN", f"stream: {e}")
                self.set_conn("connecting")
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)

    # pairing (device flow)
    def pair_start(self, lang):
        p = http("POST", f"/api/overlay/pair/start?lang={urllib.parse.quote(lang or 'en')}", auth=False)
        self.pairing = p
        if p.get("verification_uri_complete"):
            subprocess.Popen(["xdg-open", p["verification_uri_complete"]], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        threading.Thread(target=self.pair_poll, args=(p,), daemon=True).start()
        return p

    def pair_poll(self, p):
        interval = max(2, int(p.get("interval") or 5))
        deadline = time.time() + int(p.get("expires_in") or 600)
        while time.time() < deadline and self.pairing is p:
            time.sleep(interval)
            try:
                r = http("POST", "/api/overlay/pair/poll", {"device_code": p.get("device_code")}, auth=False) or {}
            except RuntimeError as e:
                log("WARN", f"pair poll: {e}")
                continue
            if r.get("token"):
                token_set(r["token"])
                log("INFO", "paired")
                self.emit("pairing", {"state": "done"})
                self.wake.set()
                return
            if r.get("status") in ("expired", "denied"):
                break
        if self.pairing is p:
            self.emit("pairing", {"state": "expired"})

    # window state
    def push_visibility(self):
        self.emit("visibility", {"visible": self.game_front or ALWAYS_VISIBLE, "gameFront": self.game_front})

    def apply_input(self):
        if self.window:
            from gi.repository import GLib
            GLib.idle_add(self.window.set_interactive, self.edit or self.hold, self.edit)

    def set_edit(self, on):
        self.edit = bool(on)
        self.apply_input()
        self.emit("mode", {"edit": self.edit})

    def hotkey(self, action):
        if action == "edit":
            self.set_edit(not self.edit)
            return
        self.emit("hotkey", {"action": action, "pressed": True})
        threading.Timer(PRESS_ONLY_GAP, self.emit, ("hotkey", {"action": action, "pressed": False})).start()

    # global keys: read passively from /dev/input (needs the `input` group); the game still gets every key
    def key_bindings(self):
        s = store_load("settings") or {}
        binds = {}
        for action, name in {"edit": "F8", **(s.get("hotkeys") or {})}.items():
            if (k := parse_key(name)):
                binds[k] = ("hotkey", action)
        v = s.get("voice") or {}
        for ch in ("prox", "group", "whisper"):
            if (k := parse_key(v.get(f"{ch}Key"))):
                binds[(k[0], None)] = ("ptt", ch)
        return binds

    def on_key(self, code, down, mods):
        if self.binds is None:
            self.binds = self.key_bindings()
        hit = self.binds.get((code, frozenset(mods))) or self.binds.get((code, None))  # None: ptt ignores modifiers
        if not hit:
            return
        if down and not (self.game_front or self.edit or ALWAYS_VISIBLE):
            return  # another program is in front: its keys are not ours
        if not down and hit not in self.held:
            return
        (self.held.add if down else self.held.discard)(hit)
        kind, name = hit
        if kind == "ptt":
            self.emit("ptt", {"channel": name, "down": down})
        elif name == "edit":
            if down:
                self.set_edit(not self.edit)
        else:
            self.emit("hotkey", {"action": name, "pressed": down})

    def keys_loop(self):
        try:
            import evdev, select
            from evdev import ecodes as E
        except ImportError:
            log("WARN", "python3-evdev missing: hotkeys only through `prometheus_overlay.py hotkey <action>`")
            return
        devs, mods, last, scanned = {}, set(), {}, 0
        while True:
            if time.time() - scanned > 10:  # hotplug: a keyboard or mouse plugged in later
                scanned = time.time()
                for path in evdev.list_devices():
                    if path in devs:
                        continue
                    try:
                        d = evdev.InputDevice(path)
                    except OSError:
                        continue
                    keys = d.capabilities().get(E.EV_KEY, [])
                    if E.KEY_F8 in keys or E.BTN_SIDE in keys:
                        devs[path] = d
                    else:
                        d.close()
                if not devs:
                    log("WARN", "no readable keyboard in /dev/input (is the user in the `input` group?)")
            r = select.select(list(devs.values()), [], [], 2)[0] if devs else (time.sleep(2) or [])
            for d in r:
                try:
                    for ev in d.read():
                        if ev.type != E.EV_KEY or ev.value == 2:
                            continue
                        down = ev.value == 1
                        if (m := MODKEYS.get(ev.code)):
                            (mods.add if down else mods.discard)(m)
                            continue
                        t = time.monotonic()
                        if last.get((ev.code, down), -1) > t - 0.03:
                            continue  # one keyboard seen through two device nodes
                        last[(ev.code, down)] = t
                        self.on_key(ev.code, down, mods)
                except OSError:
                    devs.pop(d.path, None)

    # niri: which window is in front
    def niri_loop(self):
        if not os.environ.get("NIRI_SOCKET") or not shutil.which("niri"):
            # shortcut: only niri reports the focused window; elsewhere the overlay stays shown (F9 hides it)
            log("INFO", "not running under niri: overlay always shown, hotkeys always active")
            self.game_front = True
            self.push_visibility()
            return
        windows, focused = {}, None
        while True:
            try:
                p = subprocess.Popen(["niri", "msg", "-j", "event-stream"], stdout=subprocess.PIPE, text=True)
                for line in p.stdout:
                    ev = json.loads(line)
                    if "WindowsChanged" in ev:
                        windows = {w["id"]: w for w in ev["WindowsChanged"]["windows"]}
                        focused = next((w["id"] for w in windows.values() if w.get("is_focused")), focused)
                    elif "WindowOpenedOrChanged" in ev:
                        w = ev["WindowOpenedOrChanged"]["window"]
                        windows[w["id"]] = w
                        if w.get("is_focused"):
                            focused = w["id"]
                    elif "WindowClosed" in ev:
                        windows.pop(ev["WindowClosed"]["id"], None)
                    elif "WindowFocusChanged" in ev:
                        fid = ev["WindowFocusChanged"]["id"]
                        if fid is None and (self.edit or self.hold):
                            continue  # our own layer took the keyboard: the game is still behind us
                        focused = fid
                    else:
                        continue
                    w = windows.get(focused) or {}
                    self.front_title = f"{w.get('app_id') or ''} {w.get('title') or ''}".strip()
                    front = bool(GAME_RE.search(self.front_title))
                    if front != self.game_front:
                        self.game_front = front
                        log("INFO", f"game front: {front} ({self.front_title})")
                        self.push_visibility()
            except (OSError, ValueError) as e:
                log("WARN", f"niri event-stream: {e}")
            time.sleep(3)

    # the commands the page invokes (bridge.js -> window.__TAURI__.core.invoke)
    def invoke(self, cmd, a):
        if cmd == "bootstrap":
            return {"conn": self.conn, "edit": self.edit, "version": VERSION, "settings": store_load("settings") or {}}
        if cmd == "api":
            r = http(a.get("method", "GET"), a["path"], a.get("body"), envelope=True)
            if r["status"] >= 400:
                log("WARN", f"api {a.get('method', 'GET')} {a['path']}: HTTP {r['status']}")
            return r
        if cmd == "fetch_public":
            return http("GET", a["path"], auth=False)
        if cmd == "store_load":
            v = store_load(a["name"])
            return v if v is not None else (None if a["name"] == "settings" else [])
        if cmd == "store_save":
            store_save(a["name"], a["value"])
            if a["name"] == "settings":
                self.binds = None  # changed hotkeys apply at once
            failed = [k for k, n in ((a["value"] or {}).get("hotkeys") or {}).items() if n and not parse_key(n)] \
                if a["name"] == "settings" else []
            return failed  # the settings page marks these keys red
        if cmd == "log_line":
            log(str(a.get("level", "info")).upper(), f"ui: {a.get('message')}")
            return None
        if cmd == "set_edit_mode":
            self.set_edit(a.get("on"))
            return None
        if cmd == "set_interactive":
            self.hold = bool(a.get("on"))
            self.apply_input()
            return None
        if cmd == "pair_start":
            return self.pair_start(a.get("lang"))
        if cmd == "logout":
            token_clear()
            self.pairing = None
            self.set_conn("unpaired")
            return None
        if cmd == "copy_text":
            subprocess.run(["wl-copy"], input=a.get("text", ""), text=True, check=True)
            return None
        if cmd == "notify_os":
            subprocess.Popen(["notify-send", "-a", "Prometheus Overlay", a.get("title", ""), a.get("body", "")])
            return None
        if cmd in ("open_site", "open_admin"):
            path = a.get("path") or "/"
            if not path.startswith("/"):
                path = "/" + path
            subprocess.Popen(["xdg-open", SERVER + path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return None
        if cmd == "foreground_now":
            return self.front_title
        if cmd == "check_update":
            return None  # updates: re-run linux/fetch_frontend.py
        if cmd == "install_update":
            raise RuntimeError("Linux port: update by running linux/fetch_frontend.py")
        # presence_set (Discord), obs_push (stream page), ptt_capture, admin_*: not ported
        return None


# ---------------------------------------------------------------- local server: frontend + bridge
SHIM = """<script>(() => {
  const KEY = %s;
  const es = new EventSource("/linux/events?k=" + KEY);
  async function invoke(cmd, args) {
    const r = await fetch("/linux/invoke/" + cmd, { method: "POST", headers: { "X-Key": KEY, "Content-Type": "application/json" },
      body: JSON.stringify(args ?? {}) });
    const j = await r.json();
    if (!r.ok) throw j.error;
    return j.result;
  }
  window.__TAURI__ = {
    core: { invoke },
    event: { listen: async (name, cb) => { const h = (e) => cb({ payload: JSON.parse(e.data) });
      es.addEventListener(name, h); return () => es.removeEventListener(name, h); } },
  };
})();</script>"""


def make_handler(core, key, port):
    host_ok = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def guard(self, need_key):
            if self.headers.get("Host") not in host_ok:  # DNS rebinding
                self.send_error(403)
                return False
            if need_key:
                k = self.headers.get("X-Key") or urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("k", [""])[0]
                if not secrets.compare_digest(k, key):  # a custom header: other web origins cannot send it
                    self.send_error(403)
                    return False
            return True

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path.startswith("/linux/events"):
                if not self.guard(True):
                    return
                return self.events()
            if not self.guard(False):
                return
            if path in ("/", "/index.html"):
                html = (FRONTEND / "index.html").read_text().replace("<head>", "<head>" + SHIM % json.dumps(key), 1)
                return self.send(200, html.encode(), "text/html; charset=utf-8")
            f = (FRONTEND / path.lstrip("/")).resolve()
            if not f.is_relative_to(FRONTEND) or not f.is_file():
                return self.send_error(404)
            ctype = {".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml",
                     ".png": "image/png", ".json": "application/json", ".wasm": "application/wasm",
                     ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".wav": "audio/wav"}.get(f.suffix, "application/octet-stream")
            self.send(200, f.read_bytes(), ctype)

        def do_POST(self):
            if not self.guard(True):
                return
            path = urllib.parse.urlparse(self.path).path
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            if path.startswith("/linux/hotkey/"):
                core.hotkey(path.rsplit("/", 1)[1])
                return self.send(200, b'{"result":null}', "application/json")
            cmd = path.rsplit("/", 1)[1]
            try:
                res, code = {"result": core.invoke(cmd, body)}, 200
            except Exception as e:  # the page gets the error as the rejected promise, like Tauri's Err(String)
                log("WARN", f"invoke {cmd}: {e}")
                res, code = {"error": str(e)}, 500
            self.send(code, json.dumps(res).encode(), "application/json")

        def send(self, code, data, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def events(self):
            q = queue.Queue()
            with core.lock:
                core.clients.append(q)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(b": hi\n\n")
                self.wfile.flush()
                core.push_visibility()
                while True:
                    try:
                        self.wfile.write(q.get(timeout=15))
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except OSError:
                pass
            finally:
                with core.lock:
                    core.clients.remove(q)

    return H


# ---------------------------------------------------------------- the window
def run_window(url, core):
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    gi.require_version("WebKit", "6.0")
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gdk, Gtk, Gtk4LayerShell as LS, WebKit
    import cairo

    class Overlay(Gtk.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title="Prometheus Overlay")
            LS.init_for_window(self)
            LS.set_namespace(self, "prometheus-overlay")
            LS.set_layer(self, LS.Layer.OVERLAY)  # above fullscreen windows in niri
            for edge in (LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT):
                LS.set_anchor(self, edge, True)
            LS.set_exclusive_zone(self, -1)
            LS.set_keyboard_mode(self, LS.KeyboardMode.NONE)
            css = Gtk.CssProvider()
            css.load_from_string("window, window.background { background: transparent; }")
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                      Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            wv = WebKit.WebView()
            wv.set_background_color(Gdk.RGBA(0, 0, 0, 0))
            s = wv.get_settings()
            s.set_media_playback_requires_user_gesture(False)
            s.set_enable_media_stream(True)
            s.set_enable_webrtc(True)
            s.set_enable_developer_extras(os.environ.get("PROMETHEUS_DEVTOOLS") == "1")
            wv.connect("permission-request", self.on_permission)
            wv.load_uri(url)
            self.set_child(wv)
            self.connect("realize", lambda *_: self.set_interactive(False, False))

        @staticmethod
        def on_permission(_wv, req):
            # the voice chat's microphone only; no camera, no device listing (that asks the camera portal)
            if isinstance(req, WebKit.UserMediaPermissionRequest) and req.props.is_for_audio_device \
                    and not req.props.is_for_video_device:
                req.allow()
            else:
                req.deny()
            return True

        def set_interactive(self, mouse, keyboard):
            surf = self.get_surface()
            if surf:
                surf.set_input_region(None if mouse else cairo.Region())  # empty region: clicks go to the game
            LS.set_keyboard_mode(self, LS.KeyboardMode.ON_DEMAND if keyboard else LS.KeyboardMode.NONE)
            return False

    app = Gtk.Application(application_id="hu.ascensionstudio.prometheus.overlay.linux")

    def on_activate(a):
        core.window = Overlay(a)
        core.window.present()

    app.connect("activate", on_activate)
    app.run([])


def main():
    if sys.argv[1:2] == ["--selftest"]:
        sse = [b"id: 7\n", b"event: frame\n", b'data: {"a":1}\n', b"\n", b": ping\n", b"\n"]
        assert list(parse_stream(sse)) == [("7", {"a": 1})]
        assert list(parse_stream([b'{"b":2}\n', b'{"c":3}'])) == [(None, {"b": 2}), (None, {"c": 3})]
        assert parse_key("F8") == (66, frozenset())
        assert parse_key("Shift+F10") == (68, frozenset({"shift"}))
        assert parse_key("F11") == (87, frozenset()) and parse_key("Mouse4") == (0x113, frozenset())
        assert parse_key("Ctrl+K") == (37, frozenset({"ctrl"})) and parse_key("") is None
        c = Core()
        c.binds = {(66, frozenset()): ("hotkey", "edit"), (61, frozenset()): ("hotkey", "wheel")}
        c.on_key(66, True, set())
        assert not c.edit  # game not in front: ignored
        c.game_front = True
        c.on_key(66, True, set()); c.on_key(66, False, set())
        assert c.edit
        c.on_key(61, True, {"shift"})
        assert not c.held  # exact modifiers only
        print("selftest ok")
        return
    if sys.argv[1:2] == ["hotkey"]:
        rf = json.loads(RUNFILE.read_text())
        req = urllib.request.Request(f"http://127.0.0.1:{rf['port']}/linux/hotkey/{sys.argv[2]}", data=b"{}",
                                     method="POST", headers={"X-Key": rf["key"]})
        urllib.request.urlopen(req, timeout=3).read()
        return
    if not (FRONTEND / "index.html").is_file():
        sys.exit(f"no frontend in {FRONTEND}: run linux/fetch_frontend.py first")
    # gtk4-layer-shell must load before libwayland-client
    lib = ctypes.util.find_library("gtk4-layer-shell")
    if not lib:
        sys.exit("gtk4-layer-shell not found: install it (Fedora/Arch: gtk4-layer-shell, Debian/Ubuntu: libgtk4-layer-shell0)")
    if "libgtk4-layer-shell" not in os.environ.get("LD_PRELOAD", ""):
        os.environ["LD_PRELOAD"] = (lib + " " + os.environ.get("LD_PRELOAD", "")).strip()
        os.execv(sys.executable, [sys.executable] + sys.argv)

    core = Core()
    key = secrets.token_urlsafe(24)
    srv = ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("PROMETHEUS_PORT", "0"))), BaseHTTPRequestHandler)
    port = srv.server_address[1]
    srv.RequestHandlerClass = make_handler(core, key, port)
    srv.daemon_threads = True
    fd = os.open(RUNFILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, json.dumps({"port": port, "key": key, "pid": os.getpid()}).encode())
    os.close(fd)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=core.stream_loop, daemon=True).start()
    threading.Thread(target=core.niri_loop, daemon=True).start()
    threading.Thread(target=core.keys_loop, daemon=True).start()
    log("INFO", f"Prometheus Overlay {VERSION} (Linux) on http://127.0.0.1:{port}/")
    if os.environ.get("PROMETHEUS_HEADLESS") == "1":  # no window: for testing the page in a browser
        threading.Event().wait()
    run_window(f"http://127.0.0.1:{port}/index.html", core)


if __name__ == "__main__":
    main()
