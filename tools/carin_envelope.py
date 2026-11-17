"""Check a rebuilt VY/VZ disc against the factory disc's own envelope.

  python tools/carin_envelope.py FACTORY.iso BUILT.iso

The factory disc is the only proof of what the head unit can handle, so every count and
shape a rebuild writes should stay inside what the factory itself does. This reports,
per measure, the factory maximum, the build's maximum and where the build exceeds it.
Exit status 1 when anything exceeds the factory (docs/30).
"""
import collections
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import carin, carin_build as cb  # noqa: E402

FIXED = (0x5168200, 0x5AF9400)      # background + licensing, kept byte for byte


def measures(db, skip_fixed):
    f = cb.Factory(db)
    m = collections.defaultdict(int)
    shapes = collections.Counter()
    for entry, name in ((0, "street"), (2, "level 1"), (3, "level 2"), (4, "level 3"), (1, "POI")):
        _cell, cells, _units = f.grid(entry)
        for a in set(cells):
            b = f.raw(a)
            m[f"{name} tiles per grid cell"] = max(m[f"{name} tiles per grid cell"], struct.unpack_from(">H", b, 14)[0])
    for u in f.units:
        if skip_fixed and FIXED[0] <= u.off < FIXED[1]:
            continue
        if u.table not in (0x05,):            # 0x0E units too: their header gives the unpacked size
            m[f"unpacked bytes, table {u.table:#04x}"] = max(m[f"unpacked bytes, table {u.table:#04x}"],
                                                           u.logical * 512 if u.codec else u.size)
        if u.table in (0, 1, 2, 3):
            raw = db[u.off:u.off + 0x74] if u.codec == carin.CODEC_E else carin.decode(db, u)[:0x74]
            c = [struct.unpack_from(">HH", raw, 8 + 4 * i)[1] for i in range(15)]
            x0, y0, x1, y1 = struct.unpack_from(">iiii", raw, 0x44)
            w, h = x1 - x0, y1 - y0
            shapes["square" if w == h else "2:1 wide" if w == 2 * h else "other"] += 1
            lv = {0: "street", 1: "level 1", 2: "level 2", 3: "level 3"}[u.table]
            for i, label in ((1, "places"), (4, "roads"), (7, "shape points"), (9, "neighbours"), (14, "names")):
                m[f"{lv} {label} per tile"] = max(m[f"{lv} {label} per tile"], c[i])
        if u.table in (1, 2, 3):
            # the child list is a slot grid: (w / v) x (h / v) slots, v = header 0x5C,
            # x-major, each filled slot's tile covering that slot's centre
            b = carin.decode(db, u)
            o8, n8 = struct.unpack_from(">HH", b, 8 + 4 * 8)
            v = struct.unpack_from(">I", b, 0x5C)[0]
            m[f"level {u.table} child slots per tile"] = max(m[f"level {u.table} child slots per tile"], n8)
            if not v or (w // v) * (h // v) != n8:
                m["coarse tiles whose child list isn't a slot grid"] += 1
        if u.table == 0x06:
            b = carin.decode(db, u)
            o, c = struct.unpack_from(">HH", b, 8)
            m["POIs per POI tile"] = max(m["POIs per POI tile"], c)
            xy = [struct.unpack_from(">HH", b, o + 28 * k + 6) for k in range(c)]
            if xy != sorted(xy):
                m["POI tiles not sorted x then y"] += 1
    m["tiles that aren't squares or 2:1 wide"] = shapes["other"]
    return m


def main(argv):
    fimg, bimg = (open(p, "rb").read() for p in argv[:2])
    _f, fdb = carin.read_db(fimg)
    _b, bdb = carin.read_db(bimg)
    fm, bm = measures(fdb, False), measures(bdb, True)
    over = 0
    print(f"{'measure':40s} {'factory':>9s} {'build':>9s}")
    for k in sorted(set(fm) | set(bm)):
        a, b = fm.get(k, 0), bm.get(k, 0)
        bad = b > a
        over += bad
        print(f"{k:40s} {a:9,d} {b:9,d}{'   <== over the factory' if bad else ''}")
    print(f"\n{over} measure(s) over the factory envelope")
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
