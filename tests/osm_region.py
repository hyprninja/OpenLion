"""Import an OSM region, tile it as mp0 and render it (needs osmium + a .pbf).

Usage: python tests/osm_region.py <extract.osm.pbf> <factory db dir> <out dir> lon0 lat0 lon1 lat1
Writes <out>/auz.mp0, <out>/auz.si.raw and <out>/osm_render.png; prints import stats.
"""
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import flat, graph, layers, osm_import, render, tiler  # noqa: E402
from openlion.common import build_header  # noqa: E402

if __name__ == "__main__":
    pbf, db, out = sys.argv[1:4]
    lon0, lat0, lon1, lat1 = (float(v) for v in sys.argv[4:8])
    bbox = tuple(round(v * 1e5) for v in (lon0, lat0, lon1, lat1))
    os.makedirs(out, exist_ok=True)
    t = time.time()
    import pickle
    cache = os.path.join(out, "collected.pickle")
    if os.path.exists(cache):
        c = pickle.load(open(cache, "rb"))
    else:
        c = osm_import.read(pbf, bbox, progress=lambda n, w: print(f"  read {n:,} objects, kept {w:,} ways", flush=True))
        pickle.dump(c, open(cache, "wb"))
    print(f"read: {len(c.ways):,} ways, {len(c.restrictions):,} restriction relations ({time.time() - t:.0f}s)")
    g, si, stats = osm_import.build_graph(c)
    rank = layers.node_ranks(g, si)
    for n in range(g.n_nodes):
        g.node_info[n] = graph.shared({**g.node_info[n], "rank": rank[n]})
    print("graph:", dict(stats))
    # new seginfo table, factory header
    si_tpl = flat.unpack(open(os.path.join(db, "auz.si"), "rb").read())[:0x5E]
    si_raw = build_header(si_tpl, len(si), 0x5E + 4 * len(si)) + struct.pack(f"<{len(si)}I", *si)
    open(os.path.join(out, "auz.si.raw"), "wb").write(si_raw)
    mp0_tpl = open(os.path.join(db, "auz.mp0"), "rb").read(0x5E)
    t = time.time()
    data = tiler.tile(g, 0, mp0_tpl)
    open(os.path.join(out, "auz.mp0"), "wb").write(data)
    g2 = graph.extract(data)
    s1, s2 = graph.summary(g), graph.summary(g2)
    print(f"tiled mp0: {len(data):,} bytes ({time.time() - t:.0f}s); read back identical:",
          {k: s1[k] == s2[k] for k in s1})
    for k in s1:
        if s1[k] != s2[k]:
            a, b = s1[k] - s2[k], s2[k] - s1[k]
            print(f"   {k}: {sum(a.values())} only in import, {sum(b.values())} only in read-back")
            print("     e.g.", list(a)[:1], "\n      vs", list(b)[:1])
    n, w, h = render.render(os.path.join(out, "auz.mp0"), os.path.join(out, "auz.si.raw"),
                            os.path.join(out, "osm_render.png"), bbox, 1400)
    print(f"rendered {n:,} segments -> {w}x{h}")
    # routing layers derived from the imported graph, tiled and read back
    for k in (1, 2, 3, 4):
        gk = layers.derive(g, k, si)
        tpl = open(os.path.join(db, f"auz.mg{k}"), "rb").read(0x5E)
        dk = tiler.tile(gk, k, tpl)
        open(os.path.join(out, f"auz.mg{k}"), "wb").write(dk)
        ok = graph.summary(gk) == graph.summary(graph.extract(dk))
        print(f"mg{k}: {gk.n_segs:,} segments, {len(dk):,} bytes, read back identical: {ok}")
