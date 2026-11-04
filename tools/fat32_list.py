"""Read-only FAT32 lister for a raw SD image. Lists every file (incl. deleted
entries, flagged) with size, and SHA-256 of live files.
Usage: python -I fat32_list.py <image> <out_tsv>
"""
import hashlib
import struct
import sys

img = open(sys.argv[1], "rb")
mbr = img.read(512)
lba = struct.unpack_from("<I", mbr, 446 + 8)[0]
base = lba * 512
img.seek(base)
bs = img.read(512)
bps, spc = struct.unpack_from("<HB", bs, 11)
rsv, nfat = struct.unpack_from("<HB", bs, 14)
fatsz, root = struct.unpack_from("<I4xI", bs, 36)
clus = bps * spc
fat_off = base + rsv * bps
data_off = fat_off + nfat * fatsz * bps
img.seek(fat_off)
fat = img.read(fatsz * bps)


def chain(c):
    while 2 <= c < 0x0FFFFFF8:
        yield c
        c = struct.unpack_from("<I", fat, c * 4)[0] & 0x0FFFFFFF


def read_chain(c, size=None):
    out = bytearray()
    for cl in chain(c):
        img.seek(data_off + (cl - 2) * clus)
        out += img.read(clus)
        if size is not None and len(out) >= size:
            break
    return bytes(out if size is None else out[:size])


rows = []


def walk(c, path):
    data = read_chain(c)
    lfn = []
    for i in range(0, len(data), 32):
        e = data[i:i + 32]
        if e[0] == 0:
            break
        if e[11] == 0x0F:
            lfn.insert(0, (e[1:11] + e[14:26] + e[28:32]).decode("utf-16le", "ignore").split("\0")[0].rstrip("￿"))
            continue
        name = "".join(lfn) or (e[:8].decode("ascii", "replace").rstrip() + ("." + e[8:11].decode("ascii", "replace").rstrip() if e[8:11].strip() else ""))
        lfn = []
        deleted = e[0] == 0xE5
        if name in (".", "..") or e[11] & 0x08:
            continue
        hi, lo = struct.unpack_from("<H", e, 20)[0], struct.unpack_from("<H", e, 26)[0]
        start = (hi << 16) | lo
        size = struct.unpack_from("<I", e, 28)[0]
        p = path + "/" + name
        if e[11] & 0x10:
            rows.append((p + "/", 0, "DIR", "deleted" if deleted else ""))
            if not deleted:
                walk(start, p)
        else:
            h = "" if deleted else hashlib.sha256(read_chain(start, size) if size else b"").hexdigest()
            rows.append((p, size, h, "deleted" if deleted else ""))


walk(root, "")
with open(sys.argv[2], "w", encoding="utf-8") as o:
    o.write("path\tsize\tsha256\tnote\n")
    for r in rows:
        o.write("\t".join(map(str, r)) + "\n")
print(f"cluster {clus} bytes; {sum(1 for r in rows if r[2] not in ('DIR',) and not r[3])} live files; {sum(1 for r in rows if r[3])} deleted entries")
for r in rows:
    if r[0].count("/") <= 2:
        print(r)
