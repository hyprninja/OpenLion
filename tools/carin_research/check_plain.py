"""Unpack every codec 0x0E street tile to plain bytes (carin_codec_e.to_plain) and check it.
    python check_plain.py FACTORY.iso

Per tile:
  * round trip: the plain bytes parse with carin_tile.parse and write back identically,
    and carin_tile.layout lands on the section offsets in the tile's own header;
  * the rules every readable factory tile follows (docs 31, 32), now on the town tiles:
    roads run west to east; junction lists are complete, closed and clockwise; the list
    starts at the first road clockwise from north; nodes sorted by group then x, y; the
    group table; no zero-length road, no two nodes at one point, no edge node on a corner;
    junction-type bits (0x100 dead end, 0x500 two roads, 0 three or more); house-number
    flag 0x70 only with a house-number unit; attribute bit 0 only on roads with numbers.
Across tiles: both copies of every edge node sit at the same absolute position.
Prints counts; a rule that fails shows its share and an example."""
import collections
import math
import struct
import sys

from common import Disc, Tile, carin
from openlion import carin_codec_e as ce, carin_tile as ct


class U:
    def __init__(self, off, table=0):
        self.off, self.table = off, table


def heading(m, no, r):
    p = [(m.nodes[r["A"]]["x"], m.nodes[r["A"]]["y"])] + [(q[0], q[1]) for q in r["shp"]] + \
        [(m.nodes[r["B"]]["x"], m.nodes[r["B"]]["y"])]
    if r["A"] != no:
        p = p[::-1]
    q = next((q for q in p[1:] if q != p[0]), p[-1])
    return math.degrees(math.atan2(q[0] - p[0][0], q[1] - p[0][1])) % 360


def rules(m, hn):
    """{rule: (passes, total)} for one plain street tile model."""
    R = collections.defaultdict(lambda: [0, 0])

    def rule(name, ok):
        R[name][0] += bool(ok)
        R[name][1] += 1
    w, h = (m.bbox[2] - m.bbox[0]) // 64, (m.bbox[3] - m.bbox[1]) // 64
    inc = collections.defaultdict(list)
    for r in m.roads:
        a, b = m.nodes[r["A"]], m.nodes[r["B"]]
        rule("road runs west to east", (a["x"], a["y"]) <= (b["x"], b["y"]))
        rule("road has length", (a["x"], a["y"]) != (b["x"], b["y"]) or r["shp"])
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
        rule("junction list holds every road at the node", sorted(x["k"] for x in seq) == sorted(x["k"] for x in lst))
        if len(lst) >= 3 and len(seq) == len(lst):
            hs = [heading(m, no, r) for r in seq]
            i = hs.index(min(hs))
            rot = hs[i:] + hs[:i]
            rule("junction list clockwise", rot == sorted(rot))
            rule("junction list starts nearest north (clockwise)", i == 0)
        deg = len(lst)
        lo = nd["f"] & 0x0F00
        if not nd["edge"] and lo in (0x000, 0x100, 0x500):
            rule("junction-type bits match the road count", lo == (0x100 if deg == 1 else 0x500 if deg == 2 else 0) or deg == 0)
        if nd["edge"]:
            rule("edge node not on a corner", not ((nd["x"] in (0, w)) and (nd["y"] in (0, h))))
    pos = collections.Counter((nd["x"], nd["y"]) for nd in m.nodes.values())
    rule("no two nodes at one point", max(pos.values(), default=1) == 1)
    groups = collections.defaultdict(lambda: 9)
    for r in m.roads:
        for e in (r["A"], r["B"]):
            groups[e] = min(groups[e], r["g"])
    s5 = [o for o in sorted(m.nodes) if not m.nodes[o]["edge"]]
    rule("inner nodes sorted by group, then x, y",
         [(groups[o], m.nodes[o]["x"], m.nodes[o]["y"]) for o in s5] ==
         sorted((groups[o], m.nodes[o]["x"], m.nodes[o]["y"]) for o in s5))
    R2 = m.roads
    rule("roads sorted by group", all(a["g"] <= b["g"] for a, b in zip(R2, R2[1:])))
    for g in range(7):
        first = [r for r in R2 if r["g"] >= g]
        rule("group table: first road of each group", m.groups[g][0] == (first[0]["off"] if first else m.offs[4] + 32 * len(R2)))
    h54, h70 = struct.unpack_from(">I", m.b, 0x54)[0], struct.unpack_from(">I", m.b, 0x70)[0]
    rule("house-number flag only with a house-number unit", (h70 == 0x01010000) == bool(h54) or h70 in (0, 0x01000000))
    if hn is not None:
        for r in R2:
            rec = hn[r["k"]]
            rule("attribute bit 0 = road has house numbers", bool(r["attr"] & 1) == (rec[:4] != (0x7FFF,) * 4))
    return R


def main(argv):
    d = Disc(argv[0])
    total = collections.defaultdict(lambda: [0, 0])
    example = {}
    status = collections.Counter()
    edges = {}
    for u in d.units:
        if u.table != 0 or u.codec != carin.CODEC_E:
            continue
        raw = d.db[u.off:u.off + u.size]
        try:
            model = ce.decode_street(raw, u.logical)
            plain = ce.to_plain(model, raw[:8])
        except ValueError as e:
            status["unpack failed: " + str(e)[:60]] += 1
            continue
        t = ct.parse(plain, True)
        if ct.write(t, plain[:8]) != plain:
            status["round trip differs"] += 1
            continue
        status["round trip identical"] += 1
        m = Tile(U(u.off), plain)
        hn = None
        a54 = struct.unpack_from(">I", plain, 0x54)[0]
        if a54:
            hb = carin.decode(d.db, d.unit(a54))
            o, cnt = struct.unpack_from(">HH", hb, 8)
            hn = [struct.unpack_from(">5H", hb, o + 10 * k) for k in range(cnt)]
            status["house-number unit has one record per road"] += cnt == len(m.roads)
        for name, (ok, n) in rules(m, hn).items():
            total[name][0] += ok
            total[name][1] += n
            if ok < n and name not in example:
                example[name] = hex(u.off)
        for k, nd in enumerate(model["s6"]):
            edges[(u.off, model["secs"][6][0] + 16 * k)] = (m.abs(nd["x"], nd["y"]), nd["neighbour"], nd["partner"])
    pairs = same = 0
    for (t_, o), (p, nb, po) in edges.items():
        other = edges.get(((nb >> 8) << 8, po))
        if other:
            pairs += 1
            same += other[0] == p
    print(dict(status))
    for name, (ok, n) in sorted(total.items()):
        print(f"  {name:52s} {ok:>10,} / {n:<10,} {'' if ok == n else 'e.g. ' + example[name]}")
    print(f"  {'edge node at the same point as its partner':52s} {same:>10,} / {pairs:<10,}")


if __name__ == "__main__":
    main(sys.argv[1:])
