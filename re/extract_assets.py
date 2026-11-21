#!/usr/bin/env python3
"""Extract Tauri embedded assets from a Windows PE (x64).

Tauri's codegen stores assets as a phf map of (&str path, &[u8] brotli data).
In .rdata that is laid out as consecutive (ptr, len) u64 pairs: key then value.
We find each path string, find the (ptr,len) pair pointing at it, and read the
next pair as the brotli blob.
"""
import re, struct, sys, pathlib, brotli

exe = pathlib.Path(sys.argv[1]).read_bytes()
out = pathlib.Path(sys.argv[2])

# minimal PE section map: VA <-> file offset
pe = struct.unpack_from("<I", exe, 0x3C)[0]
nsec = struct.unpack_from("<H", exe, pe + 6)[0]
optsz = struct.unpack_from("<H", exe, pe + 20)[0]
base = struct.unpack_from("<Q", exe, pe + 24 + 24)[0]
secs = []
for i in range(nsec):
    o = pe + 24 + optsz + 40 * i
    va, rawsz, rawptr = struct.unpack_from("<I", exe, o + 12)[0], *struct.unpack_from("<II", exe, o + 16)
    secs.append((va, rawsz, rawptr))

def off2va(off):
    for va, sz, raw in secs:
        if raw <= off < raw + sz:
            return base + va + off - raw

def va2off(v):
    for va, sz, raw in secs:
        if base + va <= v < base + va + sz:
            return raw + v - base - va

pat = re.compile(rb"/[A-Za-z0-9_./-]+\.(?:html|js|css|png|svg|json|ico|webp|woff2?|mp3|ogg|wav|jpg|txt)")
n = 0
for m in pat.finditer(exe):
    key = m.group()
    kva = off2va(m.start())
    if kva is None:
        continue
    needle = struct.pack("<QQ", kva, len(key))
    ref = exe.find(needle)
    if ref < 0:
        continue
    dptr, dlen = struct.unpack_from("<QQ", exe, ref + 16)
    doff = va2off(dptr)
    if doff is None or dlen > 50_000_000:
        continue
    blob = exe[doff:doff + dlen]
    try:
        data = brotli.decompress(blob)
    except brotli.error:
        data = blob  # some builds store uncompressed
    dst = (out / key.decode().lstrip("/")).resolve()
    if not dst.is_relative_to(out.resolve()):  # a path like /../x from the exe must not escape the output dir
        continue
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    n += 1
print(f"extracted {n} assets to {out}")
