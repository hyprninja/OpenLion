"""OSM extract -> every replaceable map table, ready for build.py.

Usage:
  python -m openlion.pipeline <extract.osm.pbf> <factory navigation/db_aunz/db> <out dir>
         [--extract NZ=<extract.osm.pbf> ...] [--bbox lon0 lat0 lon1 lat1] [--lookup CITY STREET ...]

The first extract may be prefixed with its country (AU=..., NZ=...); a bare path is
Australia. --extract adds more countries (see countries.py).

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
from openlion import (countries, flat, graph, layers, mapfile, osm_fea, osm_import, osm_names,  # noqa: E402
                      osm_poi, roads, search, tiler)
from openlion.common import build_header  # noqa: E402

AU_BBOX = (9000000, -4810918, 17998594, -726427)   # factory map extent (deg * 1e5)


# Stage names passed to run()'s stage callback, in order.
STAGES = ("roads", "graph", "boundaries", "names", "layers", "fea_read", "fea0", "fea1", "fea2",
          "fea3", "fea4", "fea_pack", "pois_read", "pois", "write")


def log(msg, t0=[time.time()]):
    print(f"[{time.time() - t0[0]:6.0f}s] {msg}", flush=True)


def cached(path, fn):
    if os.path.exists(path):
        with open(path, "rb") as f:
            return pickle.load(f)
    v = fn()
    tmp = path + ".tmp"          # a run killed mid-write must not leave a broken cache
    with open(tmp, "wb") as f:
        pickle.dump(v, f, protocol=5)
    os.replace(tmp, path)
    return v


def inputs_of(pbf):
    """A path, or [(Country, path)] / ["NZ=path", ...] -> [(Country, path)] in catalog order."""
    items = [pbf] if isinstance(pbf, (str, os.PathLike)) else list(pbf)
    out = [countries.parse_input(os.fspath(p)) if not isinstance(p, tuple) else p for p in items]
    codes = [c.code for c, _ in out]
    if len(set(codes)) != len(codes):
        raise ValueError(f"one extract per country, got {codes}")
    return sorted(out, key=lambda cp: cp[0].catalog)


def run(pbf, db, out, bbox=None, lookups=(), stage=None):
    """pbf: one Australian extract, or [(Country, path)] (see inputs_of).
    stage: optional callback, called with a name from STAGES as each stage starts."""
    os.makedirs(os.path.join(out, "db"), exist_ok=True)
    region = bbox or AU_BBOX
    stage = stage or (lambda name: None)
    inputs = inputs_of(pbf)
    log("countries: " + ", ".join(f"{c.title} ({os.path.basename(p)})" for c, p in inputs))

    def per_country(name, fn):
        """fn(country, path) for every extract, each cached as <name>_<CODE>.pickle."""
        return [(c, cached(os.path.join(out, f"{name}_{c.code}.pickle"), lambda c=c, p=p: fn(c, p)))
                for c, p in inputs]

    def raw(fn):
        d = open(os.path.join(db, fn), "rb").read()
        return flat.unpack(d) if d[:4] == b"zip\x01" else d

    # 1. roads
    stage("roads")
    # read only what lies on the map (the AU extract has Heard Island, NZ's the Chathams)
    c = osm_import.merge([(cn.catalog, col) for cn, col in
                          per_country("roads", lambda cn, p: osm_import.read(p, region))])
    log(f"roads: {len(c.ways):,} ways, {len(c.restrictions):,} restriction relations")
    stage("graph")
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
    stage("boundaries")
    areas, places = [], []
    for cn, a in per_country("boundaries", lambda cn, p: osm_names.read_boundaries(
            p, region, cn.boundary_levels, cn.catalog)):
        areas += a
    for cn, (a, pts) in per_country("places", lambda cn, p: osm_names.read_places(p, region, cn.catalog)
                                    if cn.localities == "places" else ([], [])):
        areas += a
        places += pts
    log(f"boundaries: {len(areas):,} areas, {len(places):,} place points")
    factory = {"stt": raw("auz.stt"), "cny": raw("auz.cny"), "cal": raw("au.cal"), "cat": raw("au.cat")}
    for k, fn in (("str_header", "auz.str"), ("cty_header", "auz.cty"), ("rd_header", "auz.rd"),
                  ("rt_header", "auz.rt"), ("rl_header", "auz.rl"), ("cl_header", "auz.cl"),
                  ("ct_header", "auz.ct")):
        factory[k] = raw(fn)[:0x5E]
    stage("names")
    tables, nst = osm_names.build(g, g.seg_label, areas, factory, places=places)
    log(f"names/search: {dict(nst)}")

    # 3. seginfo
    tpl = raw("auz.si")[:0x5E]
    tables["auz.si"] = build_header(tpl, len(si), 0x5E + 4 * len(si)) + struct.pack(f"<{len(si)}I", *si)

    # 4. map layers
    stage("layers")
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
    stage("fea_read")
    fc = osm_fea.Collected()
    for cn, part in per_country("fea", lambda cn, p: osm_fea.read(p, region)):
        fc.areas += part.areas
        fc.lines += part.lines
        fc.coast += part.coast
        fc.places += part.places
    log(f"background: {len(fc.areas):,} areas, {len(fc.lines):,} lines, {len(fc.coast):,} coastline ways")

    def fea_level_done(k):
        log(f"  fea level {k} done")
        stage(f"fea{k + 1}" if k < 4 else "fea_pack")

    stage("fea0")
    fea, fst = osm_fea.build(fc, open(os.path.join(db, "auz.fea"), "rb").read(), region, road_lines,
                             progress=fea_level_done)
    out_files["auz.fea"] = fea
    log(f"fea: {len(fea):,} bytes; dropped for size: "
        f"{sum(v for k, v in fst.items() if 'dropped' in k):,}")

    # 6. points of interest
    stage("pois_read")
    pois = []
    for cn, part in per_country("pois", lambda cn, p: osm_poi.read(p, region)):
        for cat, x, y, tags in part:
            tags[osm_poi.COUNTRY_TAG] = cn.catalog
            pois.append((cat, x, y, tags))
    stage("pois")
    # POIs within 10 km of the road map only (card_check's rule): tramping huts deep in
    # Fiordland are no use to a car and lie beyond every map parcel
    xs = [p[0] for p in g.node_xy]
    ys = [p[1] for p in g.node_xy]
    m = 10000
    poi_box = (max(region[0], min(xs) - m), max(region[1], min(ys) - m),
               min(region[2], max(xs) + m), min(region[3], max(ys) + m))
    poi_path = os.path.join(out, "edb", "POI", "aunz_sql.db3")
    pst = osm_poi.build(pois, os.path.join(db, "..", "edb", "POI", "aunz_sql.db3"), poi_path, areas, poi_box,
                        places=places)
    log(f"POIs: {pst['pois']:,} written ({len(pois):,} read from OSM)")

    # 7. write: FLAT tables packed, others as-is
    stage("write")
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
            for cat, _ in enumerate(search.parse_trees(tables["auz.ct"])):     # every country's city tree
                r = lookup(tables, lookups[i], lookups[i + 1], cat)
                if isinstance(r, tuple):
                    break
            log("  lookup " + (r[0] if isinstance(r, tuple) else r))
    return out_files


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pbf")
    ap.add_argument("db")
    ap.add_argument("out")
    ap.add_argument("--extract", action="append", default=[], help="another country: NZ=<extract.osm.pbf>")
    ap.add_argument("--bbox", nargs=4, type=float)
    ap.add_argument("--lookup", nargs="*", default=[])
    a = ap.parse_args()
    bb = tuple(round(v * 1e5) for v in a.bbox) if a.bbox else None
    run([a.pbf] + a.extract, a.db, a.out, bb, a.lookup)
