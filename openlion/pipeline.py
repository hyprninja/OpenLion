"""OSM extract -> every replaceable map table, ready for build.py.

Usage:
  python -m openlion.pipeline <extract.osm.pbf> <factory navigation/db_aunz/db> <out dir>
         [--bbox lon0 lat0 lon1 lat1] [--lookup CITY STREET ...]

Slow OSM reads are cached as pickles in <out dir>. Output tables go to <out dir>/db/
with the factory file names, packed (FLAT) exactly as on the card, and the POI
database to <out dir>/edb/POI/aunz_sql.db3.
"""
import argparse
import collections
import gc
import os
import pickle
import struct
import sys
import time

if not __package__:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import (flat, graph, layers, mapfile, osm_fea, osm_import, osm_names, osm_poi,  # noqa: E402
                      roads, search, tiler)
from openlion.common import build_header  # noqa: E402

AU_BBOX = (9000000, -4810918, 17998594, -726427)   # factory map extent (deg * 1e5)


def log(msg, t0=[time.time()]):
    print(f"[{time.time() - t0[0]:6.0f}s] {msg}", flush=True)


def cached(path, fn):
    if os.path.exists(path):
        return pickle.load(open(path, "rb"))
    v = fn()
    pickle.dump(v, open(path, "wb"), protocol=5)
    return v


def run(pbf, db, out, bbox=None, lookups=()):
    os.makedirs(os.path.join(out, "db"), exist_ok=True)
    region = bbox or AU_BBOX

    def raw(fn):
        d = open(os.path.join(db, fn), "rb").read()
        return flat.unpack(d) if d[:4] == b"zip\x01" else d

    # 1. roads
    c = cached(os.path.join(out, "roads_collected.pickle"), lambda: osm_import.read(pbf, bbox))
    log(f"roads: {len(c.ways):,} ways, {len(c.restrictions):,} restriction relations")
    g, si, st = osm_import.build_graph(c)
    rank = layers.node_ranks(g, si)
    for n in range(g.n_nodes):
        g.node_info[n]["rank"] = rank[n]
    log(f"graph: {dict(st)}")
    # the background layer only needs the overview roads; drop the raw OSM ways
    road_lines = []
    for wid, tags, pts in c.ways:
        r = osm_import.road_rank(tags)
        if r >= 2:
            road_lines.append((r, osm_import.HIGHWAY[tags["highway"]][1], tags.get("ref"),
                               [(x, y) for _, x, y in pts]))
    del c
    gc.collect()

    # 2. names + search
    areas = cached(os.path.join(out, "boundaries.pickle"), lambda: osm_names.read_boundaries(pbf, bbox))
    log(f"boundaries: {len(areas):,} areas")
    factory = {"stt": raw("auz.stt"), "cny": raw("auz.cny"), "cal": raw("au.cal"), "cat": raw("au.cat")}
    for k, fn in (("str_header", "auz.str"), ("cty_header", "auz.cty"), ("rd_header", "auz.rd"),
                  ("rt_header", "auz.rt"), ("rl_header", "auz.rl"), ("cl_header", "auz.cl"),
                  ("ct_header", "auz.ct")):
        factory[k] = raw(fn)[:0x5E]
    tables, nst = osm_names.build(g, g.seg_label, areas, factory)
    log(f"names/search: {dict(nst)}")

    # 3. seginfo
    tpl = raw("auz.si")[:0x5E]
    tables["auz.si"] = build_header(tpl, len(si), 0x5E + 4 * len(si)) + struct.pack(f"<{len(si)}I", *si)

    # 4. map layers
    out_files = {}
    data = tiler.tile(g, 0, open(os.path.join(db, "auz.mp0"), "rb").read(0x5E))
    out_files["auz.mp0"] = data
    log(f"mp0: {len(data):,} bytes, {mapfile.MapFile(data).pcl_cnt:,} parcels")
    for k in (1, 2, 3, 4):
        gk = layers.derive(g, k, si)
        dk = tiler.tile(gk, k, open(os.path.join(db, f"auz.mg{k}"), "rb").read(0x5E))
        out_files[f"auz.mg{k}"] = dk
        log(f"mg{k}: {gk.n_segs:,} segments, {len(dk):,} bytes")

    # 5. background
    fc = cached(os.path.join(out, "fea_collected.pickle"), lambda: osm_fea.read(pbf, region))
    log(f"background: {len(fc.areas):,} areas, {len(fc.lines):,} lines, {len(fc.coast):,} coastline ways")
    fea, fst = osm_fea.build(fc, open(os.path.join(db, "auz.fea"), "rb").read(), region, road_lines,
                             progress=lambda k: log(f"  fea level {k} done"))
    out_files["auz.fea"] = fea
    log(f"fea: {len(fea):,} bytes; dropped for size: "
        f"{sum(v for k, v in fst.items() if 'dropped' in k):,}")

    # 6. points of interest
    pois = cached(os.path.join(out, "pois.pickle"), lambda: osm_poi.read(pbf, bbox))
    poi_path = os.path.join(out, "edb", "POI", "aunz_sql.db3")
    pst = osm_poi.build(pois, os.path.join(db, "..", "edb", "POI", "aunz_sql.db3"), poi_path, areas, region)
    log(f"POIs: {pst['pois']:,} written ({len(pois):,} read from OSM)")

    # 7. write: FLAT tables packed, others as-is
    for name, blob in tables.items():
        out_files[name] = blob if name.startswith("au.") else flat.pack(blob)
    for name, blob in out_files.items():
        open(os.path.join(out, "db", name), "wb").write(blob)
    log(f"wrote {len(out_files)} tables to {os.path.join(out, 'db')}: {sorted(out_files)}")

    # 8. checks
    m = mapfile.MapFile(out_files["auz.mp0"])
    bad = 0
    for i in range(m.pcl_cnt):
        p = mapfile.Parcel(m.parcel(i))
        P = p.positions()
        ns = p.nodes()
        p.segments()
        bad += not (p._node_end == p.offs["seg"][0] and p._seg_end == p.offs["attr"][0] and len(P) == len(ns))
    log(f"mp0 framing: {m.pcl_cnt - bad:,} ok, {bad} bad")
    if lookups:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"))
        from osm_names_region import lookup
        for i in range(0, len(lookups), 2):
            r = lookup(tables, lookups[i], lookups[i + 1])
            log("  lookup " + (r[0] if isinstance(r, tuple) else r))
    return out_files


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pbf")
    ap.add_argument("db")
    ap.add_argument("out")
    ap.add_argument("--bbox", nargs=4, type=float)
    ap.add_argument("--lookup", nargs="*", default=[])
    a = ap.parse_args()
    bb = tuple(round(v * 1e5) for v in a.bbox) if a.bbox else None
    run(a.pbf, a.db, a.out, bb, a.lookup)
