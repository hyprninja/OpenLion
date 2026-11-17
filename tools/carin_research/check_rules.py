"""Check a VY/VZ map disc against every rule the factory disc follows (docs 31-33).
    python check_rules.py DISC.iso [DISC2.iso ...]

Works on the factory disc (codec 0x0E street tiles are unpacked with carin_codec_e) and on
rebuilt discs. For each disc, prints every rule as passes / total. Run it on the factory
disc next to a build: a rule the factory passes at 100% must pass at 100% on the build.
Only the map database is read; the background and licensing block is skipped."""
import collections
import math
import re
import struct
import sys

from common import Disc, Tile, carin, FIXED
from openlion import carin_codec_e as ce

LIMITS = {0: 588, 3: 204, 2: 324, 1: 402}      # factory's longest road, shape points
SLIP = {1: 1, 8: 1, 2: 2, 9: 2, 3: 3, 10: 3}   # road form -> slip type in word +24
LEVEL_CODE = {1: 0x0000, 2: 0x4000, 3: 0x8000, 0: 0xC000}


class U:
    def __init__(self, off, table):
        self.off, self.table = off, table


def heading(m, no, r):
    p = [(m.nodes[r["A"]]["x"], m.nodes[r["A"]]["y"])] + [(q[0], q[1]) for q in r["shp"]] + \
        [(m.nodes[r["B"]]["x"], m.nodes[r["B"]]["y"])]
    if r["A"] != no:
        p = p[::-1]
    q = next((q for q in p[1:] if q != p[0]), p[-1])
    return math.degrees(math.atan2(q[0] - p[0][0], q[1] - p[0][1])) % 360


def load_tiles(d):
    """All road tiles of a disc as plain Tile models (codec 0x0E unpacked)."""
    out = []
    for u in d.units:
        if u.table > 3 or FIXED[0] <= u.off < FIXED[1]:
            continue
        raw = d.db[u.off:u.off + u.size]
        if u.codec == carin.CODEC_E:
            b = ce.to_plain(ce.decode_street(raw, u.logical), raw[:8])
        else:
            b = carin.decode(d.db, u)
        out.append(Tile(U(u.off, u.table), b))
    return out


def check(path):
    d = Disc(path)
    R = collections.defaultdict(lambda: [0, 0])
    ex = {}

    def rule(name, ok, where=None):
        R[name][0] += bool(ok)
        R[name][1] += 1
        if not ok and name not in ex and where is not None:
            ex[name] = where
    tiles = load_tiles(d)
    by = {m.u.off: m for m in tiles}
    lvname = lambda m: "street" if m.L == 0 else f"level {m.L}"
    # positions of nodes per level, and the street-level road count at each position
    pos = {L: set() for L in range(4)}
    sdeg = collections.Counter()
    for m in tiles:
        inc = collections.Counter()
        for r in m.roads:
            inc[r["A"]] += 1
            inc[r["B"]] += 1
        m.inc = inc
        for o, nd in m.nodes.items():
            P = m.abs(nd["x"], nd["y"])
            pos[m.L].add(P)
            if m.L == 0:
                sdeg[P] += inc[o]
    order = {0: [3, 2, 1], 3: [2, 1], 2: [1], 1: []}
    for m in tiles:
        L, where = m.L, hex(m.u.off)
        rs = 32 if L == 0 else 26
        w, h = (m.bbox[2] - m.bbox[0]) // 64, (m.bbox[3] - m.bbox[1]) // 64
        inc = collections.defaultdict(list)
        for r in m.roads:
            a, b = m.nodes[r["A"]], m.nodes[r["B"]]
            rule(f"{lvname(m)}: road runs west to east", (a["x"], a["y"]) <= (b["x"], b["y"]), where)
            rule("no zero-length road", (a["x"], a["y"]) != (b["x"], b["y"]) or r["shp"], where)
            rule(f"{lvname(m)}: shape points on one road <= {LIMITS[L]}", len(r["shp"]) <= LIMITS[L], where)
            f = r["cls"] & 0xF
            if f in SLIP:
                rule("slip roads: word +24 set", (r["x24"] >> 8) & 3 == SLIP[f], where)
            inc[r["A"]].append(r)
            inc[r["B"]].append(r)
        for no, nd in m.nodes.items():
            lst = inc[no]
            seq, cur, seen = [], nd["fr"], set()
            while cur in m.road_at and cur not in seen:
                seen.add(cur)
                r = m.road_at[cur]
                seq.append(r)
                cur = r["nA"] if r["A"] == no else r["nB"]
            rule("junction list holds every road at the node", sorted(x["k"] for x in seq) == sorted(x["k"] for x in lst), where)
            if len(lst) >= 3 and len(seq) == len(lst):
                hs = [heading(m, no, r) for r in seq]
                i = hs.index(min(hs))
                rot = hs[i:] + hs[:i]
                rule("junction list clockwise", rot == sorted(rot), where)
                rule("junction list starts at the first road clockwise from north (~99%)", i == 0, where)
            P = m.abs(nd["x"], nd["y"])
            top = L
            for M in order[L]:
                if P in pos[M]:
                    top = M
                else:
                    break
            rule("node level bits = coarsest level the node reaches", nd["f"] & 0xC000 == LEVEL_CODE[top], where)
            if L in (2, 3):
                rule("level 2/3 node is also a street node", P in pos[0], where)
            if L == 2:
                rule("level 2 node is also a level 3 node", P in pos[3], where)
            lo = nd["f"] & 0x0F00
            if lo in (0, 0x100, 0x500) and P in sdeg:
                dg = sdeg[P]
                rule("junction-type bits match the street-level road count (~97%)",
                     lo == (0x100 if dg == 1 else 0x500 if dg == 2 else 0), where)
        s5 = [o for o in sorted(m.nodes) if not m.nodes[o]["edge"]]
        g = collections.defaultdict(lambda: 9)
        for r in m.roads:
            for e in (r["A"], r["B"]):
                g[e] = min(g[e], r["g"])
        rule("inner nodes sorted by group, then x, y",
             [(g[o], m.nodes[o]["x"], m.nodes[o]["y"]) for o in s5] ==
             sorted((g[o], m.nodes[o]["x"], m.nodes[o]["y"]) for o in s5), where)
        rule("roads sorted by group", all(a["g"] <= b["g"] for a, b in zip(m.roads, m.roads[1:])), where)
        for gg in range(7):
            first = [r for r in m.roads if r["g"] >= gg]
            rule("group table", m.groups[gg][0] == (first[0]["off"] if first else m.offs[4] + rs * len(m.roads)), where)
        grp = {r["g"] for r in m.roads}
        rule(f"{lvname(m)}: groups used <= {L if L else 6}", max(grp, default=0) <= (L if L else 6), where)
        rule("every tile has a neighbour", m.t.n9 > 0, where)
        p = struct.unpack_from(">I", m.b, 0x58)[0]
        if p:
            pt = by.get((p >> 8) << 8)
            if pt is not None:
                X0, Y0, X1, Y1 = pt.bbox
                x0, y0, x1, y1 = m.bbox
                rule("tile inside its parent", X0 <= x0 and x1 <= X1 and Y0 <= y0 and y1 <= Y1, where)
        if L:
            rule("zoomed-out tile has a child slot", m.t.n8 > 0, where)
        if L == 0:
            h54, h70 = struct.unpack_from(">I", m.b, 0x54)[0], struct.unpack_from(">I", m.b, 0x70)[0]
            rule("house-number flag only with a house-number unit", (h70 == 0x01010000) == bool(h54) or h70 in (0, 0x01000000), where)
            for po, fl in m.t.names:
                s = m.b[po:m.b.index(b"\0", po)]
                rule("map label <= 43 characters", len(s) <= 43, where)
                rule("label characters a-z 0-9 space ' . /", re.fullmatch(rb"[a-z0-9 './]*", s) is not None, where)
                if fl in (0x10, 0x11):
                    rule("route number = shield code + digits, <= 4 characters",
                         re.fullmatch(rb"[a-z]{1,2}[0-9]{1,3}", s) is not None and len(s) <= 4, where)
    # grids: empty cells
    f = d
    root = carin.decode(d.db, d.by[0xC00])
    for entry in (0, 1, 2, 3, 4):
        a = struct.unpack_from(">I", root, 0x10 + 24 * entry)[0]
        off, cells = (a >> 8) << 8, []
        while off in d.by and d.by[off].table == 0x08:
            b = carin.decode(d.db, d.by[off])
            s, c = struct.unpack_from(">HH", b, 8)
            cell = struct.unpack_from(">I", b, 12)[0]
            cells += struct.unpack_from(f">{c}I", b, s)
            off += d.by[off].size
            if (0x18000000 // cell) ** 2 == len(cells):
                break
        runs = collections.Counter()
        prev = None
        for c in cells:
            if c != prev:
                runs[c] += 1
            prev = c
        for c, n in runs.items():
            b = carin.decode(d.db, d.unit(c))
            k = struct.unpack_from(">H", b, 14)[0]
            if k == 0:
                rule("empty grid cell: one unit per run of empty cells", n == 1, hex(c))
                sub = struct.unpack_from(">I", b, 16)[0]
                if entry in (2, 3, 4):
                    rule("empty zoomed-out cell: sub-cell <= 0x300000", sub <= 0x300000, hex(c))
    # country record
    b = carin.decode(d.db, d.by[0x1000])
    o, n = struct.unpack_from(">HH", b, 0x14)
    rule("country record POI lists: counted or cleared", n > 0 or b[0x5C:0x5C + 48] == bytes(48), "0x1000")
    return R, ex


def main(argv):
    results = [(p, *check(p)) for p in argv]
    names = sorted(set().union(*(r.keys() for _p, r, _e in results)))
    print(f"{'rule':74s}" + "".join(f"{p.rsplit('/', 1)[-1][:22]:>26s}" for p in argv))
    for nm in names:
        row = f"{nm:74s}"
        for _p, r, ex in results:
            ok, n = r.get(nm, (0, 0))
            cellv = f"{ok:,}/{n:,}" if n else "-"
            mark = "" if ok == n else " X"
            row += f"{cellv + mark:>26s}"
        print(row)
    for p, r, ex in results:
        bad = {k: v for k, v in ex.items()}
        if bad:
            print(f"\n{p.rsplit('/', 1)[-1]} examples:", ", ".join(f"{k} @ {v}" for k, v in sorted(bad.items())))


if __name__ == "__main__":
    main(sys.argv[1:])
