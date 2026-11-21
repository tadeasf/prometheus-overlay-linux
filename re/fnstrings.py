#!/usr/bin/env python3
"""fnstrings.py EXE DIS ADDR [--asm]: list rdata strings referenced by the function containing ADDR.

Function bounds come from .pdata (x64 unwind table). Strings use the compact
format_args template encoding (\\xc0<len>lit) or plain printable runs.
"""
import re, struct, sys, bisect

exe = open(sys.argv[1], "rb").read()
dis = open(sys.argv[2]).read().splitlines()
addr = int(sys.argv[3], 16)
show_asm = "--asm" in sys.argv

pe = struct.unpack_from("<I", exe, 0x3C)[0]
nsec = struct.unpack_from("<H", exe, pe + 6)[0]
optsz = struct.unpack_from("<H", exe, pe + 20)[0]
opt = pe + 24
base = struct.unpack_from("<Q", exe, opt + 24)[0]
secs = [(struct.unpack_from("<I", exe, o + 12)[0], *struct.unpack_from("<II", exe, o + 16))
        for o in (opt + optsz + 40 * i for i in range(nsec))]

def va2off(v):
    for va, sz, raw in secs:
        if base + va <= v < base + va + sz:
            return raw + v - base - va

# exception directory = data dir index 3
exc_rva, exc_sz = struct.unpack_from("<II", exe, opt + 112 + 3 * 8)
eo = va2off(base + exc_rva)
funcs = sorted(struct.unpack_from("<II", exe, eo + i)[:2] for i in range(0, exc_sz, 12))
starts = [base + b for b, _ in funcs]
i = bisect.bisect_right(starts, addr) - 1
lo, hi = starts[i], base + funcs[i][1]
print(f"function {lo:#x}..{hi:#x} ({hi-lo} bytes)")

def lit(v):
    o = va2off(v)
    if o is None:
        return None
    b = exe[o:o + 200]
    if b[0] == 0xC0:  # compact template
        parts, j = [], 0
        while j < len(b) and b[j] == 0xC0 and j + 1 < len(b):
            n = b[j + 1]
            parts.append(b[j + 2:j + 2 + n].decode("utf8", "replace"))
            j += 2 + n
            while j < len(b) and b[j] not in (0xC0, 0):  # arg spec bytes
                j += 1
            if j < len(b) and b[j] == 0:
                break
        return "TEMPLATE " + " {} ".join(parts)
    m = re.match(rb"[\x20-\x7e]{4,}", b)
    return m.group().decode() if m else None

pat = re.compile(r"^\s*([0-9a-f]+):")
seen = set()
for l in dis:
    m = pat.match(l)
    if not m:
        continue
    a = int(m.group(1), 16)
    if a < lo or a >= hi:
        continue
    if show_asm:
        print(l)
    r = re.search(r"# 0x([0-9a-f]+)", l)
    if r:
        t = int(r.group(1), 16)
        s = lit(t)
        if s and (t, s) not in seen:
            seen.add((t, s))
            print(f"  {a:#x} -> {t:#x}: {s[:160]!r}")
