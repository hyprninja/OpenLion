"""How well do government speed zones match OSM? (needs a collected.pickle of a region)

Usage: python tests/gov_speed_check.py <collected.pickle> <prepared zones .bin> [disagreements.csv]

Builds the graph from OSM alone, then asks the zone index about every segment:
- where OSM has a limit, how often the zones agree (the zones' accuracy, as far as
  OSM can judge it, and the matcher's);
- where OSM has none, how many segments and km the zones would fill.
"""
import collections
import csv
import os
import pickle
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import gov_speed, osm_import  # noqa: E402

CLASS = {0: "freeway", 1: "highway", 2: "arterial", 3: "street", 6: "ramp", 7: "track", 8: "busway"}


def seg_xy(g, s):
    return [g.node_xy[g.seg_a[s]], *(g.seg_shape[s] or []), g.node_xy[g.seg_b[s]]]


if __name__ == "__main__":
    c = pickle.load(open(sys.argv[1], "rb"))
    t = time.time()
    ix = gov_speed.open_index(sys.argv[2])
    print(f"index: {len(ix.speeds):,} lines ({time.time() - t:.0f}s)")
    g, si, st = osm_import.build_graph(c)
    t = time.time()
    tally = collections.Counter()
    dist = collections.Counter()
    pairs = collections.Counter()
    by_class = collections.defaultdict(collections.Counter)
    bad = []
    for s in range(g.n_segs):
        xy = seg_xy(g, s)
        if not ix.covers(xy):
            continue
        cls = CLASS.get((si[g.seg_info[s]] >> 3) & 15, "?")
        L = g.seg_len[s] * osm_import.FT / 1000
        osm = (g.seg_adas[s] or {}).get("speed_kmh")
        gov = ix.lookup(xy, "track" if cls == "track" else None)
        if osm:
            if gov is None:
                k = "osm only"
            elif gov == osm:
                k = "agree"
            else:
                k = "disagree"
                pairs[(osm, gov)] += 1
                if len(bad) < 5000:
                    x, y = xy[len(xy) // 2]
                    bad.append((osm, gov, cls, round(L * 1000), y / 1e5, x / 1e5, g.seg_label[s][0] or ""))
        else:
            k = "filled" if gov else "neither"
            if gov:
                dist[gov] += L
        tally[k] += 1
        tally[k + " km"] += L
        by_class[cls][k] += 1
    print(f"lookups: {time.time() - t:.0f}s")
    for k in ("agree", "disagree", "osm only", "filled", "neither"):
        print(f"  {k:9} {tally[k]:>9,} segments {tally[k + ' km']:>10,.0f} km")
    both = tally["agree"] + tally["disagree"]
    if both:
        print(f"  agreement where both have a limit: {100 * tally['agree'] / both:.1f}%")
    print("  top disagreements (osm -> gov):", pairs.most_common(12))
    print("  filled km by speed:", {k: round(v) for k, v in sorted(dist.items())})
    for cls, ct in sorted(by_class.items()):
        b = ct["agree"] + ct["disagree"]
        print(f"  {cls:8} agree {100 * ct['agree'] / b if b else 0:5.1f}%  filled {ct['filled']:>8,}  "
              f"neither {ct['neither']:>8,}  osm-only {ct['osm only']:>7,}")
    if len(sys.argv) > 3:
        with open(sys.argv[3], "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["osm", "gov", "class", "len_m", "lat", "lon", "name"])
            w.writerows(bad)
