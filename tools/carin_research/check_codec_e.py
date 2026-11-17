"""Check openlion/carin_codec_e.py on every codec 0x0E street tile of the factory disc.
    python check_codec_e.py FACTORY.iso

Structure checks per tile: every edge node's neighbour/partner equals the record found
independently by Disc.edge_records(); the closing record's shape index is n7 and its s10
index n10; road ends are valid node offsets; next-road indices <= n4; all coordinates
inside the tile. Tail checks: string start equals s14 + 4*n14, name-entry pointers lie in
the string table, s0 entries are (0, s14, s14 + 4), the stream ends with '001'."""
import collections
import struct
import sys

from common import Disc, carin
from openlion import carin_codec_e as ce

d = Disc(sys.argv[1])
res, tail = collections.Counter(), collections.Counter()
nodes = shapes = roads = 0
for u in d.units:
    if u.table != 0 or u.codec != carin.CODEC_E:
        continue
    try:
        m = ce.decode_street(d.db[u.off:u.off + u.size], u.logical)
    except ValueError:
        res["stream error"] += 1
        continue
    secs = m["secs"]
    x0, y0, x1, y1 = struct.unpack_from(">iiii", m["header"], 0x44)
    mx, my = (x1 - x0) // 64, (y1 - y0) // 64
    valid = {secs[5][0] + 8 * k for k in range(secs[5][1])} | {secs[6][0] + 16 * k for k in range(secs[6][1])}
    er = [(a, po) for _p, a, po in d.edge_records(u.off)]
    ok = ([(nd["neighbour"], nd["partner"]) for nd in m["s6"]] == er
          and m["fence"] == dict(p10=secs[10][1], sh=secs[7][1])
          and all(r["A"] in valid and r["B"] in valid and r["nA"] <= secs[4][1] and r["nB"] <= secs[4][1]
                  for r in m["roads"])
          and all(0 <= nd["x"] <= mx and 0 <= nd["y"] <= my for nd in m["s5"] + m["s6"])
          and all(0 <= x <= mx and 0 <= y <= my for x, y, _f in m["s7"]))
    res[ok] += 1
    if ok:
        nodes += len(m["s5"]) + len(m["s6"]); shapes += len(m["s7"]); roads += len(m["roads"])
    t = m.get("tail")
    if t:
        s14, n14 = secs[14]
        tail[t["strings_start"] == s14 + 4 * n14 and t["s0_entries"][1:] == [s14, s14 + 4]
             and all(t["strings_start"] <= p <= t["strings_end"] for p, _f in t["s14"])
             and t["final"] == "001"] += 1
    else:
        tail["not parsed"] += 1
print("structure:", dict(res), f"({roads} roads, {nodes} nodes, {shapes} shape points)")
print("name tail:", dict(tail))
