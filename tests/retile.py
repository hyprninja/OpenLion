"""Re-tile test: extract the road graph from a factory map file, tile it again with
openlion's kd-tree/parcel builder, read the new file back and compare the graphs.

Usage: python tests/retile.py <path to navigation/db_aunz/db> [mp0 mg1 ... mg4]
"""
import collections
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import graph, mapfile, tiler  # noqa: E402

LAYER = {"mp0": 0, "mg1": 1, "mg2": 2, "mg3": 3, "mg4": 4}


def check_structure(data):
    """Reader-level invariants on the new file."""
    m = mapfile.MapFile(data)
    st = collections.Counter()
    for i in range(m.pcl_cnt):
        p = mapfile.Parcel(m.parcel(i))
        P = p.positions()
        ns = p.nodes()
        p.segments()
        st["framing ok"] += (p._node_end == p.offs["seg"][0] and p._seg_end == p.offs["attr"][0]
                             and len(P) == len(ns))
        st["positions sorted"] += P == sorted(P)
    return m, st


def run(db, layer_name):
    t = time.time()
    src = open(os.path.join(db, f"auz.{layer_name}"), "rb").read()
    g0 = graph.extract(src)
    t1 = time.time()
    out = tiler.tile(g0, LAYER[layer_name], src[:0x5E])
    t2 = time.time()
    m, st = check_structure(out)
    g1 = graph.extract(out)
    s0, s1 = graph.summary(g0), graph.summary(g1)
    res = {k: s0[k] == s1[k] for k in s0}
    print(f"auz.{layer_name}: factory {len(src):,} B / {mapfile.MapFile(src).pcl_cnt} parcels -> "
          f"re-tiled {len(out):,} B / {m.pcl_cnt} parcels, kd depth {m.kd_depth}")
    print(f"   graph: {g0.n_nodes:,} nodes, {g0.n_segs:,} segments, {len(g0.turns):,} turn restrictions")
    print(f"   structure: {dict(st)}")
    print(f"   identical after re-tiling: {res}")
    for k, ok in res.items():
        if not ok:
            a, b = s0[k], s1[k]
            print(f"   {k}: {sum((a - b).values())} only in factory, {sum((b - a).values())} only in re-tiled;"
                  f" e.g. {list((a - b))[:1]} vs {list((b - a))[:1]}")
    print(f"   time: extract {t1 - t:.0f}s, tile {t2 - t1:.0f}s, verify {time.time() - t2:.0f}s")
    return all(res.values())


if __name__ == "__main__":
    db = sys.argv[1]
    for name in sys.argv[2:] or ["mg4", "mg3", "mg2", "mg1", "mp0"]:
        run(db, name)
