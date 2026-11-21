#!/usr/bin/python3
"""Download the official Prometheus Overlay installer, verify its signature, and extract the frontend to ../frontend.

The overlay's UI is the original authors' work: this repo does not ship it. This script fetches the same signed installer
the Windows app updates itself from (prometheusisle.hu), checks the Tauri/minisign signature against the authors' public key
(pinned below, taken from their 0.43.3 binary), and unpacks only the web assets. Re-run it to update.

  fetch_frontend.py [--channel stable|test] [--installer PATH]   (PATH: a setup.exe you downloaded yourself)
"""
import base64, hashlib, json, shutil, subprocess, sys, tempfile, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UPDATE_URL = "https://prometheusisle.hu/api/overlay/version?channel={channel}&current=0.0.1"
ALLOWED_HOSTS = {"prometheusisle.hu", "theisle.ascensionstudio.hu"}
# minisign public key 0E6DE247282E672C, embedded in prometheus-overlay.exe 0.43.3 (tauri.conf updater pubkey)
PUBKEY = "RWQsZy4oR+JtDlrCNZJQX4hqOm5G/+DDuhWaj4SitREReo9sbsOcYLQ3"


def minisign_verify(data: bytes, sig_b64: str, pubkey: str = PUBKEY) -> str:
    """Verify a Tauri updater signature (base64 of a minisign .sig file). Returns the trusted comment; raises on failure."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    pk = base64.b64decode(pubkey)
    lines = base64.b64decode(sig_b64).decode().splitlines()
    sig = base64.b64decode(lines[1])
    trusted = lines[2].removeprefix("trusted comment: ")
    gsig = base64.b64decode(lines[3])
    if pk[:2] != b"Ed" or sig[2:10] != pk[2:10]:
        raise ValueError("signature is from another key")
    key = Ed25519PublicKey.from_public_bytes(pk[10:42])
    msg = hashlib.blake2b(data, digest_size=64).digest() if sig[:2] == b"ED" else data  # "ED": prehashed
    key.verify(sig[10:74], msg)  # raises InvalidSignature
    key.verify(gsig, sig[10:74] + trusted.encode())
    return trusted


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "PrometheusOverlay-Linux-fetch"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main():
    args = sys.argv[1:]
    channel = args[args.index("--channel") + 1] if "--channel" in args else "stable"
    info = get_json(UPDATE_URL.format(channel=urllib.parse.quote(channel)))
    plat = info["platforms"]["windows-x86_64"]
    version, url, sig = info["version"], plat["url"], plat["signature"]
    u = urllib.parse.urlparse(url)
    if u.scheme != "https" or u.hostname not in ALLOWED_HOSTS:
        sys.exit(f"refusing download from {url}")

    with tempfile.TemporaryDirectory(prefix="prometheus-fetch-") as tmp:
        tmp = Path(tmp)
        setup = tmp / "setup.exe"
        if "--installer" in args:
            shutil.copyfile(args[args.index("--installer") + 1], setup)
        else:
            print(f"downloading {version}: {url}")
            req = urllib.request.Request(url, headers={"User-Agent": "PrometheusOverlay-Linux-fetch"})
            with urllib.request.urlopen(req, timeout=120) as r, open(setup, "wb") as f:
                shutil.copyfileobj(r, f)
        trusted = minisign_verify(setup.read_bytes(), sig)
        print(f"signature ok ({trusted})")

        out = tmp / "x"
        r = subprocess.run(["7z", "x", "-y", f"-o{out}", str(setup), "prometheus-overlay.exe"], capture_output=True, text=True)
        exe = out / "prometheus-overlay.exe"
        if r.returncode or not exe.is_file():
            sys.exit(f"7z could not unpack the installer (install p7zip / 7zip):\n{r.stderr or r.stdout}")
        new = tmp / "frontend"
        subprocess.run([sys.executable, "-I", str(ROOT / "re" / "extract_assets.py"), str(exe), str(new)], check=True)
        bridge = new / "bridge.js"
        if not (new / "index.html").is_file() or "__TAURI__" not in bridge.read_text(errors="replace"):
            sys.exit("the new frontend no longer has bridge.js / window.__TAURI__: the Linux core needs porting first")
        (new / "VERSION").write_text(version + "\n")

        dst = ROOT / "frontend"
        old = ROOT / "frontend.old"
        shutil.rmtree(old, ignore_errors=True)
        if dst.exists():
            dst.rename(old)
        shutil.move(str(new), dst)
        shutil.rmtree(old, ignore_errors=True)
    print(f"frontend {version} ready in {dst}")


if __name__ == "__main__":
    main()
