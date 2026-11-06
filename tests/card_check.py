"""Check an assembled card folder before it goes in the car.

  python tests/card_check.py <card navigation/> [<factory navigation/>]
         [--manifest <manifest.tsv>] [--skip-lookups]

1. Every file is compared against docs/reference/manifest_sha256.tsv (the MY12
   CD_7606 card), or the manifest given with --manifest (the app passes the one it
   wrote when copying the user's card): untouched files must match, only
   replaceable tables may differ.
2. The new tables are read back through the decoders: every map layer's parcels
   frame cleanly and the kd-tree reaches each parcel once, every name link lands on
   a road record, every .fea parcel parses, and CBD street lookups resolve
   (--skip-lookups for builds of a small area without the capitals).
3. A replaced POI database passes SQLite's integrity check, every row points at a
   real category and string, and every POI lies on the road map. With the factory
   folder given, its schema, compiled statements and categories must be unchanged.
"""
import argparse, csv, os, sqlite3, struct, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
from openlion import flat, mapfile, mapwrite, features, osm_poi, roads, schema, search
from openlion.build import POI_DB, REPLACEABLE, sha256
from osm_names_region import lookup

ap = argparse.ArgumentParser()
ap.add_argument("card")
ap.add_argument("factory", nargs="?")
ap.add_argument("--manifest", default=os.path.join(os.path.dirname(HERE), "docs", "reference", "manifest_sha256.tsv"))
ap.add_argument("--skip-lookups", action="store_true")
ARGS = ap.parse_args()
CARD = ARGS.card
FACTORY = ARGS.factory
DB = os.path.join(CARD, "db_aunz", "db")
print(f"datamodel: Arriba {schema.use_card(DB).name}")
MANIFEST = ARGS.manifest
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
rd = raw("auz.rd"); road_by_off = {r["offset"]: r for r in roads.iter_roads(rd)}; starts = set(road_by_off)
numbered = badnum = oddcode = outside = 0
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
                hn = mapwrite.decode_range(nl["kind"], nl["range"])
                if hn and nl["road_off"] in starts:
                    # a house-number range. Negative numbers mean a broken encoding; rare side
                    # codes and ranges past the street's max are info only (the factory has both)
                    numbered += 1
                    code, base, delta = hn
                    r = road_by_off[nl["road_off"]]
                    lo, hi = min(base, base + delta), max(base, base + delta)
                    inside = (r["minNumber"] <= lo and hi <= r["maxNumber"]) if "maxNumber" in r else r["hasNumbers"] == 1
                    badnum += lo < 0
                    oddcode += code not in mapwrite.HN_SIDE
                    outside += not inside
    kd_ok = sorted(leaves) == list(range(m.pcl_cnt))
    problems += (not kd_ok) + bad + badname + badnum
    extra = (f", {names:,} name links ({badname} not at a road record), {numbered:,} with house numbers "
             f"({badnum} negative; info: {oddcode} other side codes, {outside} past the street's numbers)"
             ) if layer == "mp0" else ""
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
# CBD streets of each country that has cities on the card (an empty catalog's tree is root-only)
LOOKUPS = {0: (("SYDNEY", "GEORGE STREET"), ("MELBOURNE", "SWANSTON STREET"), ("BRISBANE CITY", "QUEEN STREET"),
               ("PERTH (WESTERN AUSTRALIA)", "HAY STREET"), ("ADELAIDE", "KING WILLIAM STREET"),
               ("HOBART", "ELIZABETH STREET"), ("DARWIN CITY", "MITCHELL STREET"), ("CANBERRA", "NORTHBOURNE AVENUE"),
               ("BRADDON, CANBERRA", "LONSDALE STREET")),
           1: (("AUCKLAND", "QUEEN STREET"), ("WELLINGTON", "LAMBTON QUAY"), ("CHRISTCHURCH", "COLOMBO STREET"),
               ("HAMILTON", "VICTORIA STREET"), ("DUNEDIN", "GEORGE STREET"), ("PONSONBY, AUCKLAND", "PONSONBY ROAD"))}
city_trees = list(search.parse_trees(tables["auz.ct"]).values())
for cat, queries in LOOKUPS.items():
    if cat >= len(city_trees) or len(city_trees[cat]["nodes"]) <= 2:
        print(f"  catalog {cat}: no cities on this card")
        continue
    for city, street in queries:
        r = lookup(tables, city, street, cat)
        print("  lookup", r[0] if isinstance(r, tuple) else r)
        problems += not isinstance(r, tuple) and not ARGS.skip_lookups   # printed, not counted, for small areas
if POI_DB in changed:
    con = sqlite3.connect(f"file:{os.path.join(CARD, POI_DB)}?mode=ro", uri=True)
    q = lambda sql: con.execute(sql).fetchall()
    integ = q("pragma integrity_check")[0][0]
    n = q("select count(*) from Poi_BaseAttributes")[0][0]
    orphans = q("select count(*) from Poi_BaseAttributes where PoiPartition_ID not in "
                "(select PoiPartition_ID from PoiPartition_BaseAttributes)")[0][0]
    cols = ("HouseNumber_String_ID", "Street_String_ID", "Village_String_ID", "City_String_ID", "Region_String_ID",
            "Country_String_ID", "IsoCountry_String_ID", "PhoneNumber_String_ID")
    dangling = q("select count(*) from Poi_BaseAttributes where Name_String_ID not in (select String_ID from String_BaseAttributes)")[0][0]
    for c in cols:
        dangling += q(f"select count(*) from Poi_AddressAttributes where {c} is not null and "
                      f"{c} not in (select String_ID from String_BaseAttributes)")[0][0]
    no_alert = q("select count(*) from Poi_BaseAttributes where Poi_ID not in (select Poi_ID from Poi_AlertAttributes)")[0][0]
    mp0 = mapfile.MapFile(open(os.path.join(DB, "auz.mp0"), "rb").read())
    outside = 0
    for (v,) in con.execute("select Coordinate from Poi_BaseAttributes"):
        x, y = osm_poi.unmorton(v)
        m = 10000   # 10 km: coastal POIs can lie a little beyond the last road
        outside += not (mp0.min_long - m <= x <= mp0.max_long + m and mp0.min_lat - m <= y <= mp0.max_lat + m)
    con.close()
    kept = osm_poi.compare_schema(os.path.join(FACTORY, POI_DB), os.path.join(CARD, POI_DB)) if FACTORY else None
    print(f"POIs: {n:,}, integrity {integ}, bad category {orphans}, dangling strings {dangling}, "
          f"missing alert rows {no_alert}, beyond the road map {outside}, "
          f"factory structure changed: {kept if kept is not None else 'not checked'}")
    problems += (integ != "ok") + orphans + dangling + no_alert + outside + len(kept or [])
print(f"{problems} problems, {time.time() - t0:.0f}s")
sys.exit(1 if problems else 0)
