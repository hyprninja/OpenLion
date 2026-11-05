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
import hashlib
import os
import pickle
import struct
import sys
import time

if not __package__:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import (countries, flat, graph, layers, mapfile, memory, osm_fea, osm_import,  # noqa: E402
                      osm_names, osm_poi, resources, roads, schema, search, tiler)
from openlion.common import DamagedTable, build_header  # noqa: E402

AU_BBOX = (9000000, -4810918, 17998594, -726427)   # factory map extent (deg * 1e5)


# Stage names passed to run()'s stage callback, in order.
STAGES = ("roads", "graph", "boundaries", "names", "layers", "fea_read", "fea0", "fea1", "fea2",
          "fea3", "fea4", "fea_pack", "pois_read", "pois", "write")


# stage and read processes log against the build's start, not their own
os.environ.setdefault("OPENLION_T0", str(time.time()))


def log(msg, t0=[float(os.environ["OPENLION_T0"])]):
    p = memory.process()["private"]
    print(f"[{time.time() - t0[0]:6.0f}s] {msg}  (mem {memory.gb(p)}, free {memory.gb(memory.available())})",
          flush=True)


# Cache files are pickles, and unpickling can run code, so only files this pipeline
# wrote are loaded: each has a SHA-256 sidecar written with it. A file without one, or
# that no longer matches it (corrupt, hand-edited, older version), is deleted and re-read.

def _sidecar(path):
    return path + ".sha256"


def _file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def cache_ok(path):
    """True if the cache file exists and matches its sidecar; otherwise removes both."""
    if not os.path.exists(path):
        return False
    try:
        with open(_sidecar(path), encoding="ascii") as f:
            want = f.read().strip()
    except (OSError, ValueError):
        want = ""
    if want and want == _file_sha256(path):
        return True
    for p in (path, _sidecar(path)):
        try:
            os.remove(p)
        except OSError:
            pass
    return False


def save_cache(path, v):
    tmp = path + ".tmp"          # a run killed mid-write must not leave a broken cache
    with open(tmp, "wb") as f:
        pickle.dump(v, f, protocol=5)
    with open(_sidecar(path) + ".tmp", "w", encoding="ascii") as f:
        f.write(_file_sha256(tmp))
    os.replace(_sidecar(path) + ".tmp", _sidecar(path))
    os.replace(tmp, path)


def load_cache(path):
    if not cache_ok(path):
        raise RuntimeError(f"cache file {os.path.basename(path)} is missing or was changed; run the build again")
    with open(path, "rb") as f:
        return pickle.load(f)


def cached(path, fn):
    if cache_ok(path):
        with open(path, "rb") as f:
            return pickle.load(f)
    v = fn()
    save_cache(path, v)
    return v


def inputs_of(pbf):
    """A path, or [(Country, path)] / ["NZ=path", ...] -> [(Country, path)] in catalog order."""
    items = [pbf] if isinstance(pbf, (str, os.PathLike)) else list(pbf)
    out = [countries.parse_input(os.fspath(p)) if not isinstance(p, tuple) else p for p in items]
    codes = [c.code for c, _ in out]
    if len(set(codes)) != len(codes):
        raise ValueError(f"one extract per country, got {codes}")
    return sorted(out, key=lambda cp: cp[0].catalog)


def run(pbf, db, out, bbox=None, lookups=(), stage=None, memory_mode=None):
    """pbf: one Australian extract, or [(Country, path)] (see inputs_of).
    stage: optional callback, called with a name from STAGES as each stage starts.
    memory_mode: "low", "high" or None (decide from free RAM; see resources.py)."""
    os.makedirs(os.path.join(out, "db"), exist_ok=True)
    sch = schema.use_card(db)   # write tables in the card's own datamodel
    log(f"datamodel: Arriba {sch.name}")
    region = bbox or AU_BBOX
    app_stage = stage or (lambda name: None)
    sampler = memory.Sampler()
    log(f"memory: {memory.gb(memory.total())} installed, {memory.gb(memory.available())} free")
    plan = resources.make_plan(memory_mode)
    log(plan.describe())

    def stage(name):
        """App progress stage (from STAGES) and a memory-profile step."""
        sampler.stage(name)
        if name in STAGES:
            app_stage(name)

    try:
        return _run(pbf, db, out, bbox, lookups, stage, region, plan)
    finally:
        sampler.stop()
        log("peak memory per step: " + ", ".join(f"{k} {memory.gb(v)}" for k, v in sampler.peaks().items()))
        log(f"lowest free RAM during the build: {memory.gb(sampler.min_avail or 0)}")


# OSM reads, each cached per country as <name>_<CODE>.pickle. Bump a name when the
# reader's output format changes so stale caches are never loaded.
READS = ("roads2", "boundaries3", "places3", "fea2", "pois2")
CACHES = READS + ("overview2",)      # overview2: written alongside roads2


def _read(name, code, path, region, node_index):
    cn = countries.BY_CODE[code]
    if name == "roads2":
        # only what lies on the map (the AU extract has Heard Island, NZ's the Chathams)
        return osm_import.read(path, region, node_index=node_index)
    if name == "boundaries3":
        return osm_names.read_boundaries(path, region, cn.boundary_levels, cn.catalog, node_index=node_index)
    if name == "places3":
        if cn.localities != "places":
            return [], []
        return osm_names.read_places(path, region, cn.catalog, node_index=node_index)
    if name == "fea2":
        return osm_fea.read(path, region, node_index=node_index)
    if name == "pois2":
        return osm_poi.read(path, region, node_index=node_index)
    raise ValueError(name)


def _index_file(node_index):
    """The file behind an on-disk osmium index ("sparse_file_array,<path>"), else None."""
    kind, _, path = node_index.partition(",")
    return path if kind.endswith("_file_array") and path else None


def _read_job(job):
    """One OSM read in its own worker process: run it and write its cache file.
    The process ends after one job (max_tasks_per_child=1), which closes osmium's
    index file and hands all of the read's memory back to Windows."""
    name, code, path, region, cache, node_index = job
    t = time.time()
    res = cached(cache, lambda: _read(name, code, path, region, node_index))
    if name == "roads2":     # the background map's share, so it needn't load every road
        cached(_overview_cache(cache), lambda: _road_lines(res, with_ids=True))
    res = None
    gc.collect()
    idx = _index_file(node_index)
    if idx:
        _remove(idx)
    return name, code, time.time() - t, memory.process()["peak_private"]


def _remove(path, tries=20):
    """Delete a file; on Windows a just-ended worker may hold it for a moment."""
    for _ in range(tries):
        try:
            os.remove(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            time.sleep(0.5)
    return False


# ---- stage processes ---------------------------------------------------------
# Each heavy stage runs in a process of its own and leaves its results on disk. When
# it ends, all its memory goes back to Windows (Python keeps freed memory for itself
# while it runs), so the build's peak is the biggest single stage, not the sum. The
# main process only coordinates. Stages talk to it through a queue:
#   ("stage", name)        progress, a name from STAGES
#   ("poi_box", box)       the road map's extent, for the POI stage
#   ("peaks", label, {})   the stage's memory profile, at its end
#   ("done", label)        the stage finished; ("error", label, text) it failed


class _Ctx:
    """What a stage process needs to know (picklable)."""

    def __init__(self, inputs, db, out, region, plan, lookups):
        self.inputs = [(cn.code, p) for cn, p in inputs]
        self.db, self.out, self.region, self.plan, self.lookups = db, out, region, plan, list(lookups)
        self.poi_box = None      # set from the roads stage's message before the POI stage starts

    def countries(self):
        return [(countries.BY_CODE[code], p) for code, p in self.inputs]

    def cache(self, name, cn):
        return os.path.join(self.out, f"{name}_{cn.code}.pickle")

    def per_country(self, name):
        """[(country, read result)] from the cache files the read workers wrote."""
        res = []
        for cn, _ in self.countries():
            res.append((cn, load_cache(self.cache(name, cn))))
        return res

    def raw(self, fn):
        d = open(os.path.join(self.db, fn), "rb").read()
        try:
            return flat.unpack(d) if d[:4] == b"zip\x01" else d
        except DamagedTable as e:
            raise DamagedTable(f"{fn}: {e}") from None

    def write(self, name, blob):
        if not name.startswith("au.") and name not in MAP_FILES:
            blob = flat.pack(blob)
        with open(os.path.join(self.out, "db", name), "wb") as f:
            f.write(blob)


MAP_FILES = ("auz.mp0", "auz.mg1", "auz.mg2", "auz.mg3", "auz.mg4", "auz.fea")


def _stage_main(fn, label, ctx, q):
    """Entry point of a stage process."""
    schema.use_card(ctx.db)
    sampler = memory.Sampler()

    def stage(name):
        sampler.stage(name)
        q.put(("stage", name))

    try:
        fn(ctx, stage, q)
        sampler.stop()
        q.put(("peaks", label, sampler.peaks()))
        q.put(("done", label))
    except DamagedTable as e:
        q.put(("damaged", label, str(e)))
    except BaseException:
        import traceback
        q.put(("error", label, traceback.format_exc()))


def _road_lines(c, with_ids=False):
    """The overview roads for the background map: (rank, class, ref, xy, bbox), plus the
    way id first when with_ids (to drop ways two extracts share, as osm_import.merge does)."""
    out = []
    for wid, tags, pts in c.ways:
        r = osm_import.road_rank(tags)
        if r >= 2:
            xy = pts.xy
            item = (r, osm_import.HIGHWAY[tags["highway"]][1], tags.get("ref"), xy,
                    (min(xy[0::2]), min(xy[1::2]), max(xy[0::2]), max(xy[1::2])))
            out.append((wid,) + item if with_ids else item)
    return out


def _overview_cache(roads_cache):
    return roads_cache.replace("roads2_", "overview2_")


def _roads_stage(ctx, stage, q):
    """Road network -> names and search tables, seginfo, routing layers mp0, mg1-4."""
    stage("roads")
    c = osm_import.merge([(cn.catalog, col) for cn, col in ctx.per_country("roads2")])
    log(f"roads: {len(c.ways):,} ways, {len(c.restrictions):,} restriction relations")
    stage("graph")
    g, si, st = osm_import.build_graph(c, consume=True)
    del c
    gc.collect()
    # POIs only need the road map's extent: within 10 km of it (card_check's rule), so
    # tramping huts deep in Fiordland, beyond every map parcel, stay out
    m, region = 10000, ctx.region
    xs = [p[0] for p in g.node_xy]
    ys = [p[1] for p in g.node_xy]
    q.put(("poi_box", (max(region[0], min(xs) - m), max(region[1], min(ys) - m),
                       min(region[2], max(xs) + m), min(region[3], max(ys) + m))))
    del xs, ys
    stage("ranks")
    rank = layers.node_ranks(g, si)
    for n in range(g.n_nodes):
        g.node_info[n] = graph.shared({**g.node_info[n], "rank": rank[n]})
    del rank
    log(f"graph: {dict(st)}")

    stage("boundaries")
    areas, places = [], []
    for cn, a in ctx.per_country("boundaries3"):
        areas += a
    for cn, (a, pts) in ctx.per_country("places3"):
        areas += a
        places += pts
    log(f"boundaries: {len(areas):,} areas, {len(places):,} place points")
    factory = {"stt": ctx.raw("auz.stt"), "cny": ctx.raw("auz.cny"), "cal": ctx.raw("au.cal"),
               "cat": ctx.raw("au.cat")}
    for k, fn in (("str_header", "auz.str"), ("cty_header", "auz.cty"), ("rd_header", "auz.rd"),
                  ("rt_header", "auz.rt"), ("rl_header", "auz.rl"), ("cl_header", "auz.cl"),
                  ("ct_header", "auz.ct")):
        factory[k] = ctx.raw(fn)[:0x5E]
    stage("names")
    tables, nst = osm_names.build(g, g.seg_label, areas, factory, places=places)
    log(f"names/search: {dict(nst)}")
    g.seg_label = None
    del areas, places
    tpl = ctx.raw("auz.si")[:0x5E]
    tables["auz.si"] = build_header(tpl, len(si), 0x5E + 4 * len(si)) + struct.pack(f"<{len(si)}I", *si)
    for name, blob in tables.items():
        ctx.write(name, blob)
    if ctx.lookups:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"))
        from osm_names_region import lookup
        lk = ctx.lookups
        for i in range(0, len(lk), 2):
            for cat, _ in enumerate(search.parse_trees(tables["auz.ct"])):     # every country's city tree
                r = lookup(tables, lk[i], lk[i + 1], cat)
                if isinstance(r, tuple):
                    break
            log("  lookup " + (r[0] if isinstance(r, tuple) else r))
    del tables

    stage("layers")
    data = tiler.tile(g, 0, open(os.path.join(ctx.db, "auz.mp0"), "rb").read(0x5E))
    ctx.write("auz.mp0", data)
    log(f"mp0: {len(data):,} bytes, {mapfile.MapFile(data).pcl_cnt:,} parcels")
    del data
    for k in (1, 2, 3, 4):
        stage(f"mg{k}")
        gk = layers.derive(g, k, si)
        dk = tiler.tile(gk, k, open(os.path.join(ctx.db, f"auz.mg{k}"), "rb").read(0x5E))
        ctx.write(f"auz.mg{k}", dk)
        log(f"mg{k}: {gk.n_segs:,} segments, {len(dk):,} bytes")
        del gk, dk


def _fea_stage(ctx, stage, q):
    """Background map (auz.fea): water, parks, land use, coast and the overview roads."""
    stage("fea_read")
    road_lines, seen = [], set()
    for cn, _ in ctx.countries():      # catalog order, first extract wins: as osm_import.merge
        oc = _overview_cache(ctx.cache("roads2", cn))
        roads_cache = ctx.cache("roads2", cn)
        lines = cached(oc, lambda p=roads_cache: _road_lines(load_cache(p), with_ids=True))
        for item in lines:
            if item[0] not in seen:
                seen.add(item[0])
                road_lines.append(item[1:])
        lines = None
    seen = None
    fc = osm_fea.Collected()
    for cn, part in ctx.per_country("fea2"):
        fc.areas += part.areas
        fc.lines += part.lines
        fc.coast += part.coast
        fc.places += part.places
    log(f"background: {len(fc.areas):,} areas, {len(fc.lines):,} lines, {len(fc.coast):,} coastline ways")

    def level_done(k):
        log(f"  fea level {k} done")
        stage(f"fea{k + 1}" if k < 4 else "fea_pack")

    stage("fea0")
    fea, fst = osm_fea.build(fc, open(os.path.join(ctx.db, "auz.fea"), "rb").read(), ctx.region, road_lines,
                             progress=level_done, workers=ctx.plan.fea_workers)
    ctx.write("auz.fea", fea)
    log(f"fea: {len(fea):,} bytes; dropped for size: {sum(v for k, v in fst.items() if 'dropped' in k):,}")


def _pois_stage(ctx, stage, q):
    """Points of interest database (edb/POI/aunz_sql.db3)."""
    stage("pois_read")
    pois = []
    for cn, part in ctx.per_country("pois2"):
        for cat, x, y, tags in part:
            tags[osm_poi.COUNTRY_TAG] = cn.catalog
            pois.append((cat, x, y, tags))
    areas, places = [], []
    for cn, a in ctx.per_country("boundaries3"):
        areas += a
    for cn, (a, pts) in ctx.per_country("places3"):
        areas += a
        places += pts
    stage("pois")
    db = ctx.db
    poi_path = os.path.join(ctx.out, "edb", "POI", "aunz_sql.db3")
    pst = osm_poi.build(pois, os.path.join(db, "..", "edb", "POI", "aunz_sql.db3"), poi_path, areas,
                        ctx.poi_box, places=places)
    log(f"POIs: {pst['pois']:,} written ({len(pois):,} read from OSM)")


def _run(pbf, db, out, bbox, lookups, stage, region, plan):
    import multiprocessing as mp
    inputs = inputs_of(pbf)
    log("countries: " + ", ".join(f"{c.title} ({os.path.basename(p)})" for c, p in inputs))
    ctx = _Ctx(inputs, db, out, region, plan, lookups)

    q = None
    order = {s: i for i, s in enumerate(STAGES)}
    started, forwarded = set(), [-1]

    def forward(name):
        """Pass progress to the app in STAGES order, even when stages overlap."""
        started.add(name)
        while forwarded[0] + 1 < len(STAGES) and STAGES[forwarded[0] + 1] in started:
            forwarded[0] += 1
            stage(STAGES[forwarded[0]])
        if name not in order:
            stage(name)          # finer memory-profile steps (ranks, mg1, ...)

    # 0. OSM reads, each in a worker process of its own: one at a time on a small PC
    # (node locations in a file on disk), several at once on a big one
    forward("roads")
    jobs = [(name, cn.code, p, region, ctx.cache(name, cn), plan.node_index(out, f"nodes_{name}_{cn.code}"))
            for name in READS for cn, p in inputs if not cache_ok(ctx.cache(name, cn))]
    if jobs:
        import concurrent.futures as cf
        # biggest first: Australia before New Zealand, roads and background before the rest
        jobs.sort(key=lambda j: (j[1] != "AU", READS.index(j[0]) not in (0, 3)))
        n = min(plan.read_workers, len(jobs))
        log(f"reading OSM: {len(jobs)} reads, {n} at a time")
        try:
            with cf.ProcessPoolExecutor(max_workers=n, max_tasks_per_child=1) as ex:
                futs = {ex.submit(_read_job, j): j for j in jobs}
                for fut in cf.as_completed(futs):
                    name, code, secs, peak = fut.result()
                    log(f"  read {name} {code}: {secs:.0f}s, worker peak {memory.gb(peak)}")
        finally:
            for j in jobs:       # index files a worker couldn't delete itself
                idx = _index_file(j[5])
                if idx and os.path.exists(idx) and not _remove(idx):
                    log(f"  could not delete {idx}")

    # 1-3. stage processes. Low plan: one after another. High plan: the background map
    # runs alongside the roads stage, and the POIs start once the road map's extent is known.
    q = mp.get_context("spawn").Queue()
    procs, peaks = {}, {}

    def start(label, fn):
        p = mp.get_context("spawn").Process(target=_stage_main, args=(fn, label, ctx, q), name=label)
        p.start()
        procs[label] = p
        log(f"stage {label}: started")

    def wait(labels):
        """Handle queue messages until every stage in labels is done."""
        pending = set(labels)
        while pending:
            try:
                msg = q.get(timeout=5)
            except Exception:       # queue.Empty: check for a stage that died without a word
                for lb in list(pending):
                    if not procs[lb].is_alive() and procs[lb].exitcode not in (0, None):
                        raise RuntimeError(f"stage {lb} ended with exit code {procs[lb].exitcode} "
                                           "(out of memory?)")
                continue
            kind = msg[0]
            if kind == "stage":
                forward(msg[1])
            elif kind == "poi_box":
                ctx.poi_box = msg[1]
                if plan.mode == "high" and "pois" not in procs:
                    start("pois", _pois_stage)
                    pending.add("pois")
            elif kind == "peaks":
                peaks[msg[1]] = msg[2]
            elif kind == "done":
                pending.discard(msg[1])
                procs[msg[1]].join()
                log(f"stage {msg[1]}: done, peak {memory.gb(max(peaks.get(msg[1], {0: 0}).values()))}")
            elif kind == "damaged":
                raise DamagedTable(msg[2])
            elif kind == "error":
                raise RuntimeError(f"stage {msg[1]} failed:\n{msg[2]}")

    try:
        if plan.mode == "high":
            start("roads", _roads_stage)
            start("fea", _fea_stage)
            wait(["roads", "fea"])
            if "pois" in procs and procs["pois"].is_alive():
                wait(["pois"])
        else:
            start("roads", _roads_stage)
            wait(["roads"])
            start("fea", _fea_stage)
            wait(["fea"])
            start("pois", _pois_stage)
            wait(["pois"])
    finally:
        for p in procs.values():
            if p.is_alive():
                p.terminate()
    for label, pk in peaks.items():
        log(f"stage {label} peak memory per step: " + ", ".join(f"{k} {memory.gb(v)}" for k, v in pk.items()))

    # 4. the tables are on disk already; check the routing layer's framing
    forward("write")
    written = sorted(os.listdir(os.path.join(out, "db")))
    log(f"wrote {len(written)} tables to {os.path.join(out, 'db')}: {written}")
    forward("checks")
    m = mapfile.MapFile(open(os.path.join(out, "db", "auz.mp0"), "rb").read())
    bad = 0
    for i in range(m.pcl_cnt):
        p = mapfile.Parcel(m.parcel(i))
        P = p.positions()
        ns = p.nodes()
        p.segments()
        bad += not (p._node_end == p.offs["seg"][0] and p._seg_end == p.offs["attr"][0] and len(P) == len(ns))
    log(f"mp0 framing: {m.pcl_cnt - bad:,} ok, {bad} bad")
    return written


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pbf")
    ap.add_argument("db")
    ap.add_argument("out")
    ap.add_argument("--extract", action="append", default=[], help="another country: NZ=<extract.osm.pbf>")
    ap.add_argument("--bbox", nargs=4, type=float)
    ap.add_argument("--lookup", nargs="*", default=[])
    ap.add_argument("--memory", choices=("auto", "low", "high"), default=None,
                    help="memory plan (default: decide from free RAM)")
    a = ap.parse_args()
    bb = tuple(round(v * 1e5) for v in a.bbox) if a.bbox else None
    run([a.pbf] + a.extract, a.db, a.out, bb, a.lookup, memory_mode=a.memory)
