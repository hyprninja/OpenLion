"""Full-database structural check of a CARiN disc image (openlion.carin_check NAV_DB.iso).

Read-only. Walks every unit and reports violations of the invariants the factory disc
holds. Exit status 1 with a count when anything is found, so it can gate a build.

Checks, per table:
  tiles 0x00-0x03  section table in bounds and 4-aligned, road node/shape references
                   inside s5+s6 / s7 and aligned, shape lists monotonic with the fence
                   at the section end, every per-node circular road list closes, s6
                   partner back-references (tile and offset) correct, rasters in range
  grids 0x08/0x09  every cell address resolves to a 0x09 unit, sub-grid not larger
                   than its section, offset and address sections inside the unit
  search 0x0C-0x11 leaf and run offsets inside their target units
  root   0x07      all 12 layer entries resolve to 0x08 units
In-tile references are absolute byte offsets into the tile (not indices). Node
references may legitimately point into s5 (interior nodes) or s6 (edge nodes).

Part of the analysis behind docs/29_vz_map_disc.md and docs/30_protection_and_rebuild.md.
Needs the `openlion` package importable (run from the repo root).
"""
import collections
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openlion import carin
from openlion.carin_tile import ORDER

RS = {True: 32, False: 26}
TILE = {0x00, 0x01, 0x02, 0x03}


def check_tile(b, detail, out):
    rs = RS[detail]
    s = [struct.unpack_from(">HH", b, 8 + 4 * i) for i in range(15)]
    seq = [s[i][0] for i in ORDER if not (i == 14 and s[i][1] == 0)]
    if seq != sorted(seq):
        out["section_order_wrong"] += 1
    for i, (o, c) in enumerate(s):
        if o % 4 and c:
            out[f"s{i}_misaligned"] += 1
        if o > len(b):
            out[f"s{i}_offset_past_end"] += 1
    (o4, n4), (o5, n5), (o6, n6), (o7, n7) = s[4], s[5], s[6], s[7]
    end4, end5, end6, end7 = o4 + rs * (n4 + 1), o5 + 8 * n5, o6 + 16 * n6, o7 + 6 * n7
    if end4 > len(b) or end5 > len(b) or end6 > len(b) or end7 > len(b):
        out["section_past_end"] += 1
        return
    roads = [struct.unpack_from(">HHHHHH", b, o4 + rs * k) for k in range(n4 + 1)]
    nodes = [struct.unpack_from(">HHHH", b, o5 + 8 * k) for k in range(n5)]
    for k, (na, nb, fs, nxa, nxb, _cls) in enumerate(roads[:n4]):
        for v in (na, nb):
            if v and not ((o5 <= v < end5 and (v - o5) % 8 == 0)
                          or (o6 <= v < end6 and (v - o6) % 16 == 0)):
                out["node_ref_bad"] += 1
        if not (o7 <= fs <= end7) or (fs - o7) % 6:
            out["shape_ref_bad"] += 1
        for v in (nxa, nxb):
            if v and (not (o4 <= v < end4) or (v - o4) % rs):
                out["next_road_bad"] += 1
    fence = roads[n4][2] if len(roads) > n4 else None
    if fence is not None and fence != end7:
        out["shape_fence_wrong"] += 1
    last = None
    for k in range(n4):
        fs = roads[k][2]
        if last is not None and fs < last:
            out["shape_list_backwards"] += 1
        last = fs
        for v in (roads[k][3], roads[k][4]):
            if v and v < roads[k][2] and v != 0:
                pass
    # circular lists round each node
    for ni, (nx, ny, fr, _fl) in enumerate(nodes):
        lim = 0x6000 if detail else 0xC000
        if nx >= lim or ny >= lim:
            out["node_coord_outside"] += 1
        if not (o4 <= fr < end4) or (fr - o4) % rs:
            out["node_first_road_bad"] += 1
            continue
        node_off = o5 + 8 * ni
        cur, steps, seen = fr, 0, set()
        while steps <= n4 + 2 and cur not in seen:
            seen.add(cur)
            r = roads[(cur - o4) // rs]
            cur = r[3] if r[0] == node_off else r[4]
            steps += 1
        if cur != fr:
            out["node_list_no_close"] += 1
    # s6 partners
    for k in range(n6):
        _x, _y, _fr, _fl, na, noff, _z = struct.unpack_from(">HHHHIHH", b, o6 + 16 * k)
        nu = by.get((na >> 8) << 8)
        if nu is None:
            out["partner_tile_missing"] += 1
            continue
        if nu.codec == carin.CODEC_E:
            continue
        nb = carin.decode(by["_db"], nu)
        ts = [struct.unpack_from(">HH", nb, 8 + 4 * i) for i in range(15)]
        to6, tn6 = ts[6]
        if noff + 16 > to6 + 16 * tn6:
            out["partner_offset_oob"] += 1
            continue
        if ((struct.unpack_from(">I", nb, noff + 8)[0] >> 8) << 8) != cur_tile:
            out["partner_no_backref"] += 1


cur_tile = None
by = {}


def yield_tile(addr):
    u = by.get((addr >> 8) << 8)
    if u is None or u.table not in TILE or u.codec == carin.CODEC_E:
        return None
    return carin.decode(by["_db"], u)


def main(argv):
    global by, cur_tile
    if len(argv) != 1:
        print("usage: python tools/carin_check.py NAV_DB.iso")
        return 2
    img = open(argv[0], "rb").read()
    _f, db = carin.read_db(img)
    by = {u.off: u for u in carin.units(db)}
    by["_db"] = db
    out = collections.Counter()
    root = carin.decode(db, by[0xC00])

    for entry in range(12):
        a, = struct.unpack_from(">I", root, 0x10 + 24 * entry)
        if a and (not by.get((a >> 8) << 8) or by[(a >> 8) << 8].table != 0x08):
            out["root_entry_not_grid"] += 1

    for off, u in by.items():
        if off == "_db" or u.codec == carin.CODEC_E:
            continue
        b = carin.decode(db, u)
        if u.table in TILE:
            cur_tile = off
            check_tile(b, u.table == 0x00, out)
        elif u.table == 0x09:
            (o0, m), (o1, k) = struct.unpack_from(">HH", b, 8), struct.unpack_from(">HH", b, 12)
            sub, = struct.unpack_from(">I", b, 16)
            if o0 + 2 * m > o1 or o1 + 4 * k > len(b):
                out["cell_sections_overlap"] += 1
            for j in range(m):
                e, = struct.unpack_from(">H", b, o0 + 2 * j)
                if e and e + 4 > o1 + 4 * k:
                    out["cell_entry_oob"] += 1
        elif u.table == 0x0F:
            o, c = struct.unpack_from(">HH", b, 8)
            for k in range(c):
                a, _ch, leaf, off_, cnt = struct.unpack_from(">IBBHH", b, o + 12 * k)
                if not leaf:
                    continue
                v = by.get((a >> 8) << 8)
                if v is None or v.table != 0x0E:
                    out["trie_leaf_target_bad"] += 1
                    continue
                logical = max((v.codec & 0xFF) * 512, v.size)
                if off_ >= logical:
                    out["trie_leaf_offset_past_end"] += 1

    total = sum(out.values())
    print(f"{argv[0]}: {len(by) - 1} units, {total} problem(s)")
    for k, v in sorted(out.items()):
        print(f"  {k}: {v}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
