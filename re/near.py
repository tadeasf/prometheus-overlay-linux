import re,struct,sys
exe=open(sys.argv[1],'rb').read(); dis=open(sys.argv[2]).read().splitlines()
pe=struct.unpack_from("<I",exe,0x3C)[0]; optsz=struct.unpack_from("<H",exe,pe+20)[0]; nsec=struct.unpack_from("<H",exe,pe+6)[0]
base=struct.unpack_from("<Q",exe,pe+48)[0]
secs=[(struct.unpack_from("<I",exe,o+12)[0],*struct.unpack_from("<II",exe,o+16)) for o in (pe+24+optsz+40*i for i in range(nsec))]
def off2va(off):
    for va,sz,raw in secs:
        if raw<=off<raw+sz: return base+va+off-raw
refs={}
for l in dis:
    r=re.search(r"^\s*([0-9a-f]+):.*# 0x([0-9a-f]+)",l)
    if r: refs.setdefault(int(r.group(2),16),[]).append(int(r.group(1),16))
for n in sys.argv[3:]:
    va=off2va(exe.find(n.encode()))
    c=[(t,a) for t in range(va-48,va+1) for a in refs.get(t,[])]
    print(n, hex(va), [(hex(t),hex(a)) for t,a in c][:6])
