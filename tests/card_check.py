"""Check an assembled card folder before it goes in the car.

  python tests/card_check.py <card navigation/>

1. Every file is compared against docs/reference/manifest_sha256.tsv (the MY12
   CD_7606 card): untouched files must match, only replaceable tables may differ.
2. The new tables are read back through the decoders: every map layer's parcels
   frame cleanly and the kd-tree reaches each parcel once, every name link lands on
   a road record, every .fea parcel parses, and CBD street lookups resolve.
"""
import csv, os, struct, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
from openlion import flat, mapfile, features, roads
from openlion.build import REPLACEABLE, sha256
from osm_names_region import lookup

CARD = sys.argv[1]
DB = os.path.join(CARD, "db_aunz", "db")
MANIFEST = os.path.join(os.path.dirname(HERE), "docs", "reference", "manifest_sha256.tsv")
t0 = time.time()

man = {r["path"][len("navigation/"):]: r["sha256"] for r in csv.DictReader(open(MANIFEST), delimiter="	")}
files = set()
for dp, _, fs in os.walk(CARD):
    for f in fs:
        files.add(os.path.relpath(os.path.join(dp, f), CARD).replace(os.sep, "/"))
changed = {p for p in files & set(man) if sha256(os.path.join(CARD, p)) != man[p]}
protected = sorted(changed - REPLACEABLE)
size = sum(os.path.getsize(os.path.join(CARD, p)) for p in files)
print(f"files: {len(files)} ({size / 1e6:.0f} MB), missing {len(set(man) - files)}, extra {len(files - set(man))}")
print(f"untouched and identical to factory: {len(files & set(man)) - len(changed)}; replaced map tables: {len(changed)}")
print(f"protected files changed: {protected or 'none'}")


def raw(fn):
    d = open(os.path.join(DB, fn), "rb").read()
    return flat.unpack(d) if d[:4] == bytes([0x7A, 0x69, 0x70, 0x01]) else d

problems = len(protected)
rd = raw("auz.rd"); starts = {r["offset"] for r in roads.iter_roads(rd)}
print(f"road records: {len(starts):,}")
for layer in ("mp0", "mg1", "mg2", "mg3", "mg4"):
    m = mapfile.MapFile(open(os.path.join(DB, f"auz.{layer}"), "rb").read())
    leaves = []
    for (edge, lo, hi, info) in m.kd:
        if info & 8: leaves.append(lo)
        if info & 128: leaves.append(hi)
    bad = segs = names = badname = 0
    for i in range(m.pcl_cnt):
        p = mapfile.Parcel(m.parcel(i))
        P = p.positions(); ns = p.nodes(); S = p.segments()
        p.shapes(); p.speed_limits(); p.turn_restrictions()
        bad += not (p._node_end == p.offs["seg"][0] and p._seg_end == p.offs["attr"][0] and len(P) == len(ns))
        segs += len(S)
        if layer == "mp0":
            for nl in p.name_links():
                names += 1; badname += nl["road_off"] not in starts
    kd_ok = sorted(leaves) == list(range(m.pcl_cnt))
    problems += (not kd_ok) + bad + badname
    extra = f", {names:,} name links ({badname} not at a road record)" if layer == "mp0" else ""
    print(f"{layer}: {m.pcl_cnt:,} parcels, kd tree reaches each once: {kd_ok}, framing bad: {bad}, segments {segs:,}{extra}")

ff = features.FeatureFile(open(os.path.join(DB, "auz.fea"), "rb").read())
nf = errs = 0; mx_raw = 0
for k, lv in enumerate(ff.levels):
    for iy in range(lv["cnt_y"]):
        for ix in range(lv["cnt_x"]):
            try:
                r = ff.parcel_raw(k, ix, iy)
                mx_raw = max(mx_raw, len(r) if r else 0)
                nf += len(ff.features(k, ix, iy))
            except Exception:
                errs += 1
problems += errs
print(f"fea: {len(ff.levels)} levels, {nf:,} features, parse errors {errs}, largest raw parcel {mx_raw:,} B (factory limit 116,000)")

tables = {fn: raw(fn) for fn in ("auz.str", "auz.cty", "auz.cl", "auz.ct", "auz.rl", "auz.rt", "auz.rd")}
for city, street in (("SYDNEY", "GEORGE STREET"), ("MELBOURNE", "SWANSTON STREET"), ("BRISBANE CITY", "QUEEN STREET"),
                     ("PERTH (WESTERN AUSTRALIA)", "HAY STREET"), ("ADELAIDE", "KING WILLIAM STREET"),
                     ("HOBART", "ELIZABETH STREET"), ("DARWIN CITY", "MITCHELL STREET"), ("CITY", "NORTHBOURNE AVENUE")):
    r = lookup(tables, city, street)
    print("  lookup", r[0] if isinstance(r, tuple) else r)
    problems += not isinstance(r, tuple)
print(f"{problems} problems, {time.time() - t0:.0f}s")
sys.exit(1 if problems else 0)
