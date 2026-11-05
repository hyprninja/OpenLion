"""OSM extract + factory card copy -> a finished, checked navigation/ folder.

The one command the Windows app runs to build maps:

  python -m openlion.make_card <extract.osm.pbf> <factory navigation/> <work dir> <out navigation/>
         [--extract NZ=<extract.osm.pbf> ...] [--manifest <manifest.tsv>] [--bbox lon0 lat0 lon1 lat1] [--json]

The first extract may name its country (AU=..., NZ=...); a bare path is Australia.
Add --extract for each further country. Countries left out get no cities on the card.

1. openlion.pipeline builds every map table and the POI database in <work dir>
   (slow OSM reads are cached there and reused while the extract is unchanged).
2. openlion.build copies the factory folder with the new tables into <out>.partial,
   verifying every untouched file.
3. tests/card_check.py checks the result (against --manifest: the user's own copy).
4. <out>.partial is renamed to <out>. <out> must not exist yet.

With --json, prints one JSON object per line, mixed with plain log lines:
  {"event": "start", "stages": [{"id": ..., "label": ..., "weight": seconds}, ...]}
  {"event": "stage", "stage": id}
  {"event": "result", "ok": bool, "card": path, "bytes": n, "files": n, "replaced": n,
   "stage": failed stage, "error": ..., "error_type": ..., "traceback": ...}
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

if not __package__:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import build, pipeline  # noqa: E402
from openlion.compat import error_name, safe_traceback  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CARD_CHECK = os.path.join(ROOT, "tests", "card_check.py")

# (stage, label, rough seconds for all of Australia on a fast PC with nothing cached;
#  scaled by the total extract size for other countries)
STAGES = [
    ("roads", "Reading roads", 240),
    ("graph", "Building the road network", 130),
    ("boundaries", "Reading suburbs, towns and postcodes", 150),
    ("names", "Street names and address search", 300),
    ("layers", "Routing layers", 160),
    ("fea_read", "Reading water, parks and land use", 300),
    ("fea0", "Drawing the background map (level 1 of 5)", 760),
    ("fea1", "Drawing the background map (level 2 of 5)", 475),
    ("fea2", "Drawing the background map (level 3 of 5)", 200),
    ("fea3", "Drawing the background map (level 4 of 5)", 110),
    ("fea4", "Drawing the background map (level 5 of 5)", 20),
    ("fea_pack", "Packing the background map", 50),
    ("pois_read", "Reading points of interest", 180),
    ("pois", "Building points of interest", 815),
    ("write", "Writing map tables", 20),
    ("card", "Assembling the card", 90),
    ("check", "Checking the new card", 120),
]
assert [s for s, _, _ in STAGES][:len(pipeline.STAGES)] == list(pipeline.STAGES)

CACHE_KEY = "cache_key.json"
AU_EXTRACT_BYTES = 972_670_217    # the extract the stage times were measured with


class Failed(Exception):
    pass


class Out:
    def __init__(self, as_json):
        self.as_json = as_json
        self.stage = None

    def emit(self, event, **kw):
        if self.as_json:
            print(json.dumps({"event": event, **kw}), flush=True)

    def start_stage(self, name):
        self.stage = name
        if self.as_json:
            self.emit("stage", stage=name)
        else:
            print(f"== {dict((s, l) for s, l, _ in STAGES)[name]}", flush=True)


def reset_cache(inputs, work, bbox):
    """The pipeline caches OSM reads as pickles in work/. Throw them away when an extract,
    the set of countries or the area changes, so a new download is never mixed with an old one."""
    key = {"extracts": [], "bbox": list(bbox) if bbox else None}
    for country, path in inputs:
        st = os.stat(path)
        key["extracts"].append({"country": country.code, "size": st.st_size, "mtime": int(st.st_mtime)})
    path = os.path.join(work, CACHE_KEY)
    try:
        with open(path, encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        old = None
    if old != key:
        for fn in os.listdir(work):
            if fn.endswith((".pickle", ".pickle.tmp")):
                os.remove(os.path.join(work, fn))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(key, f)
    return old == key


def folder_size(path):
    n = b = 0
    for dp, _, fs in os.walk(path):
        for f in fs:
            n += 1
            b += os.path.getsize(os.path.join(dp, f))
    return n, b


def run(pbf, factory, work, out, manifest=None, bbox=None, as_json=False):
    """pbf: one Australian extract, or a list of "NZ=path" / (Country, path) (pipeline.inputs_of)."""
    o = Out(as_json)
    inputs = pipeline.inputs_of(pbf)
    total = sum(os.path.getsize(p) for _, p in inputs if os.path.isfile(p))
    scale = max(0.05, total / AU_EXTRACT_BYTES)
    o.emit("start", stages=[{"id": s, "label": l, "weight": w if s in ("card", "check") else round(w * scale)}
                            for s, l, w in STAGES])
    partial = out.rstrip("\\/") + ".partial"
    try:
        db = os.path.join(factory, "db_aunz", "db")
        for _, p in inputs:
            if not os.path.isfile(p):
                raise Failed(f"OSM extract not found: {p}")
        if not os.path.isdir(db):
            raise Failed(f"no db_aunz/db folder in the factory copy: {factory}")
        if os.path.exists(out):
            raise Failed(f"output folder already exists: {out}")
        os.makedirs(work, exist_ok=True)
        if reset_cache(inputs, work, bbox):
            print("reusing cached OSM reads from an earlier run", flush=True)

        pipeline.run(inputs, db, work, bbox, stage=o.start_stage)

        o.start_stage("card")
        if os.path.exists(partial):
            shutil.rmtree(partial)
        poi = os.path.join(work, "edb", "POI", "aunz_sql.db3")
        reps = build.table_replacements(factory, os.path.join(work, "db"), poi if os.path.isfile(poi) else None)
        rep = build.build(factory, partial, reps)
        print(f"copied {rep['copied']} files unchanged, replaced {len(rep['replaced'])}", flush=True)
        if rep["verify_failed"]:
            raise Failed(f"{len(rep['verify_failed'])} copied files don't match the factory copy: "
                         f"{rep['verify_failed'][:5]}")
        bad = build.check_headers(factory, partial, rep["replaced"])
        if bad:
            raise Failed("new map tables lost their factory header stamps: " + "; ".join(bad[:5]))

        o.start_stage("check")
        cmd = [sys.executable, "-X", "utf8", "-u", CARD_CHECK, partial, factory]
        if manifest:
            cmd += ["--manifest", manifest]
        if bbox:
            cmd.append("--skip-lookups")
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace")
        last = ""
        for line in p.stdout:
            line = line.rstrip("\n")
            print("  " + line, flush=True)
            last = line or last
        if p.wait() != 0:
            raise Failed(f"the finished card failed its check ({last.strip()})")

        os.replace(partial, out)
        files, size = folder_size(out)
        o.emit("result", ok=True, card=out, files=files, bytes=size, replaced=len(rep["replaced"]))
        if not as_json:
            print(f"done: {out} ({files} files, {size / 1e6:.0f} MB)")
        return 0
    except Exception as e:  # report every failure as a result line, the app shows it
        if os.path.exists(partial):
            shutil.rmtree(partial, ignore_errors=True)
        msg = str(e) if isinstance(e, Failed) else (
            "the PC ran out of memory" if isinstance(e, MemoryError) else f"{error_name(e)}: {e}")
        o.emit("result", ok=False, stage=o.stage, error=msg, error_type=error_name(e),
               traceback=None if isinstance(e, Failed) else safe_traceback())
        if not as_json:
            print(f"FAILED {'during ' + o.stage if o.stage else 'before starting'}: {msg}", file=sys.stderr)
            if not isinstance(e, Failed):
                print(safe_traceback(), file=sys.stderr)
        return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pbf", help="OSM extract; AU=<path> or NZ=<path> (a bare path is Australia)")
    ap.add_argument("factory", help="navigation/ folder of the factory card copy")
    ap.add_argument("work", help="working folder (cached OSM reads, new tables)")
    ap.add_argument("out", help="navigation/ folder to create")
    ap.add_argument("--manifest", help="manifest.tsv of the factory copy (default: the CD_7606 reference)")
    ap.add_argument("--bbox", nargs=4, type=float, help="build only this area (lon0 lat0 lon1 lat1), for testing")
    ap.add_argument("--extract", action="append", default=[], help="another country: NZ=<extract.osm.pbf>")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    bb = tuple(round(v * 1e5) for v in a.bbox) if a.bbox else None
    t0 = time.time()
    code = run([a.pbf] + a.extract, a.factory, a.work, a.out, a.manifest, bb, a.json)
    if not a.json:
        print(f"{time.time() - t0:.0f}s")
    sys.exit(code)
