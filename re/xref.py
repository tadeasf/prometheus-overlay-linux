#!/usr/bin/env python3
"""xref.py EXE DIS 'needle' [ctx]: print functions in objdump DIS that reference the string."""
import re, struct, sys, bisect

exe = open(sys.argv[1], "rb").read()
dis = open(sys.argv[2]).read().splitlines()
needle = sys.argv[3].encode()
ctx = int(sys.argv[4]) if len(sys.argv) > 4 else 0

pe = struct.unpack_from("<I", exe, 0x3C)[0]
nsec = struct.unpack_from("<H", exe, pe + 6)[0]
optsz = struct.unpack_from("<H", exe, pe + 20)[0]
base = struct.unpack_from("<Q", exe, pe + 24 + 24)[0]
secs = [(struct.unpack_from("<I", exe, o + 12)[0], *struct.unpack_from("<II", exe, o + 16))
        for o in (pe + 24 + optsz + 40 * i for i in range(nsec))]

def off2va(off):
    for va, sz, raw in secs:
        if raw <= off < raw + sz:
            return base + va + off - raw

off = exe.find(needle)
assert off >= 0, "needle not found"
va = off2va(off)
# a Rust &str literal may be referenced a few bytes before (merged literals) - search exact start only
print(f"string @ {va:#x}")
target = f"0x{va:x}"
fn_starts = [i for i, l in enumerate(dis) if l.endswith(">:") or re.match(r"^[0-9a-f]+ <", l)]
hits = [i for i, l in enumerate(dis) if f"# {target}" in l or f"# {va:x}" in l]
for h in hits:
    print("ref:", dis[h].strip())
    if ctx:
        print("\n".join(dis[max(0, h - ctx): h + ctx]))
        print("-" * 40)

if not hits:
    # format_args! pieces: &[&str] table of (ptr,len) in .rdata; code references the table start
    for m in re.finditer(re.escape(struct.pack("<Q", va)), exe):
        loc = off2va(m.start())
        for back in range(0, 6 * 16, 16):
            t = loc - back
            th = [l.strip() for l in dis if f"# 0x{t:x}" in l]
            for l in th:
                print(f"via table @{t:#x} (piece {back//16}):", l)
