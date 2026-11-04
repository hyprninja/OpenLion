"""OSM -> street names and address search (road records, cities, lists, speller trees).

Model (matches the factory conventions, report 10 / 13):
- city   = OSM admin_level=9 suburb/locality (Australia); county = admin_level=6 LGA;
           state = admin_level=4 (mapped onto the factory state list by name).
- road record = one per (normalised street name, city), bbox over its segments, plus
           one nameless record per city for unnamed segments (factory: roadInfo 0).
- names  = base + rotations of the leading words (WARNER GLEN MILL ROAD ->
           GLEN MILL ROAD;WARNER, MILL ROAD;WARNER GLEN); city names likewise
           (NORTH MELBOURNE -> MELBOURNE;NORTH) plus "POSTCODE, NAME" when known.
- every segment links (abs_addr) to exactly one road record.
"""
import collections
import struct
import unicodedata

from . import search
from .common import HDR
from .roads import REC, NAME

ALLOWED = set(search.ABC_ORDER)
TYPE_WORDS = {
    "ROAD", "STREET", "AVENUE", "DRIVE", "COURT", "PLACE", "CRESCENT", "LANE", "WAY", "HIGHWAY",
    "FREEWAY", "CLOSE", "PARADE", "TERRACE", "BOULEVARD", "GROVE", "CIRCUIT", "PARKWAY",
    "MOTORWAY", "TRACK", "TRAIL", "WALK", "RISE", "VIEW", "ESPLANADE", "SQUARE", "LOOP",
    "HIGHWAY", "ROUTE", "LINK", "BYPASS", "CIRCLE", "MEWS", "GARDENS", "GLADE", "GATE",
    "ARCADE", "ALLEY", "PATHWAY", "PROMENADE", "RIDGE", "VISTA", "BEND", "CHASE", "QUAY",
    "RAMBLE", "RETREAT", "ROW", "STRAND", "WYND", "CUTTING", "EXPRESSWAY", "TOLLWAY",
}
DIRECTIONS = {"N", "S", "E", "W", "NORTH", "SOUTH", "EAST", "WEST", "NE", "NW", "SE", "SW"}
MAXLEN = 35


def normalise(name):
    """Uppercase Latin, characters limited to the au.abc set, max 35 chars."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").upper()
    s = s.replace("’", "'").replace('"', "'")
    s = "".join(c if c in ALLOWED else " " for c in s)
    return " ".join(s.split())[:MAXLEN]


def rotations(name):
    """Factory-style search variants: move leading words behind ';'."""
    w = name.split(" ")
    tail = 0
    while tail < len(w) - 1 and (w[-1 - tail] in DIRECTIONS):
        tail += 1
    if len(w) - tail >= 2 and w[-1 - tail] in TYPE_WORDS:
        tail += 1
    core = w[:len(w) - tail]
    typ = w[len(w) - tail:]
    if len(core) < 2:
        return []
    out = []
    for k in range(1, len(core)):
        v = " ".join(core[k:] + typ) + ";" + " ".join(core[:k])
        if len(v) <= MAXLEN:
            out.append(v)
    return out


# ---- boundaries --------------------------------------------------------------

class Area:
    __slots__ = ("level", "name", "postcode", "rings", "bbox", "osm_id")

    def __init__(self, level, name, postcode, rings, osm_id):
        self.level, self.name, self.postcode, self.rings, self.osm_id = level, name, postcode, rings, osm_id
        xs = [x for r in rings for x, _ in r[0]]
        ys = [y for r in rings for _, y in r[0]]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))

    def contains(self, x, y):
        b = self.bbox
        if not (b[0] <= x <= b[2] and b[1] <= y <= b[3]):
            return False
        inside = False
        for outer, inners in self.rings:
            if _pip(outer, x, y) and not any(_pip(i, x, y) for i in inners):
                inside = not inside
        return inside

    def centroid(self):
        outer = max((r[0] for r in self.rings), key=len)
        n = len(outer)
        return (sum(x for x, _ in outer) // n, sum(y for _, y in outer) // n)


def _pip(ring, x, y):
    c = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < xi + (y - yi) * (xj - xi) / (yj - yi):
            c = not c
        j = i
    return c


def read_boundaries(pbf_path, bbox=None, levels=("4", "6", "9")):
    """Admin areas at the given admin_levels (Australia: 4 state, 6 LGA, 9 suburb /
    locality) plus postcode areas (level 0 here), overlapping bbox."""
    import osmium
    import osmium.filter
    kinds = (("boundary", "administrative"), ("boundary", "postal_code"))
    fp = (osmium.FileProcessor(pbf_path, osmium.osm.NODE | osmium.osm.WAY | osmium.osm.RELATION | osmium.osm.AREA)
          .with_areas(osmium.filter.TagFilter(*kinds))
          .with_filter(osmium.filter.EntityFilter(osmium.osm.AREA))
          .with_filter(osmium.filter.TagFilter(*kinds)))
    out = []
    for a in fp:
        tags = {t.k: t.v for t in a.tags}
        if tags.get("boundary") == "postal_code":
            pc = tags.get("postal_code") or tags.get("ref") or tags.get("name")
            if not pc:
                continue
            tags = {"admin_level": "0", "name": pc, "postal_code": pc}
            lv = "0"
        else:
            lv = tags.get("admin_level")
        if lv not in levels + ("0",) or "name" not in tags:
            continue
        rings = []
        for outer in a.outer_rings():
            o = [(round(n.lon * 1e5), round(n.lat * 1e5)) for n in outer]
            inners = [[(round(n.lon * 1e5), round(n.lat * 1e5)) for n in inner] for inner in a.inner_rings(outer)]
            rings.append((o, inners))
        if not rings:
            continue
        ar = Area(int(lv), tags["name"], tags.get("postal_code") or tags.get("addr:postcode"),
                  rings, a.orig_id())
        if bbox:
            b = ar.bbox
            if b[2] < bbox[0] or b[0] > bbox[2] or b[3] < bbox[1] or b[1] > bbox[3]:
                continue
        out.append(ar)
    return out


class AreaIndex:
    """Grid index for point-in-area lookups at one admin level."""

    def __init__(self, areas, cell=5000):
        self.cell, self.grid = cell, collections.defaultdict(list)
        for i, a in enumerate(areas):
            b = a.bbox
            for gx in range(b[0] // cell, b[2] // cell + 1):
                for gy in range(b[1] // cell, b[3] // cell + 1):
                    self.grid[(gx, gy)].append(i)
        self.areas = areas

    def find(self, x, y):
        for i in self.grid.get((x // self.cell, y // self.cell), []):
            if self.areas[i].contains(x, y):
                return i
        return None


# ---- tables -------------------------------------------------------------------

def segment_point(g, s):
    sh = g.seg_shape[s]
    if sh:
        return sh[len(sh) // 2]
    (x1, y1), (x2, y2) = g.node_xy[g.seg_a[s]], g.node_xy[g.seg_b[s]]
    return ((x1 + x2) // 2, (y1 + y2) // 2)


def build(g, seg_labels, areas, factory, catalog_id=0):
    """Build road records, cities and search tables for graph g.

    seg_labels: per segment (name, ref) from OSM. areas: read_boundaries() output.
    factory: dict of factory raw tables used as templates (headers, states, time info).
    Fills g.seg_names in place. Returns {table name: raw bytes (uncompressed)} and stats."""
    stats = collections.Counter()
    by_level = {lv: [a for a in areas if a.level == lv] for lv in (0, 4, 6, 9)}
    idx = {lv: AreaIndex(v) for lv, v in by_level.items()}
    CITY = 9

    # states: map OSM admin_level=4 names onto the factory state list (IDs kept)
    f_states = [search._name(r[25:61]) for r in search.records(factory["stt"], 152)]
    state_id = {}
    for i, a in enumerate(by_level[4]):
        nm = normalise(a.name)
        state_id[i] = f_states.index(nm) if nm in f_states else 0

    # counties = LGAs; time zone copied from a factory county of the same state
    f_cny = [search.parse_county(r) for r in search.records(factory["cny"], 106)]
    tz_of_state = {}
    for c in f_cny:
        tz_of_state.setdefault(c["stateID"], c["timeinfoID"])
    counties = []
    lga_cid = {}
    for i, a in enumerate(by_level[6]):
        cx, cy = a.centroid()
        si_ = idx[4].find(cx, cy)
        st = state_id.get(si_, 0)
        lga_cid[i] = len(counties)
        counties.append({"countyID": len(counties), "stateID": st, "zoneID": 0, "start_cityID": 0,
                         "end_cityID": 0, "cover": (0, 0, 0, 0), "trans1": 4,
                         "name": normalise(a.name), "trans2": 5, "phonetic": "",
                         "timeinfoID": tz_of_state.get(st, 0)})

    # cities = localities; assign every segment to one
    city_of_seg = []
    loc = by_level[CITY]
    for s in range(g.n_segs):
        x, y = segment_point(g, s)
        i = idx[CITY].find(x, y)
        if i is None:
            stats["segments outside any locality"] += 1
        city_of_seg.append(i)
    used = sorted({c for c in city_of_seg if c is not None}, key=lambda i: normalise(loc[i].name))
    # disambiguate duplicate names with the state, like the factory "(VICTORIA)"
    names_count = collections.Counter(normalise(loc[i].name) for i in used)
    city_name = {}
    for i in used:
        nm = normalise(loc[i].name)
        if names_count[nm] > 1:
            cx, cy = loc[i].centroid()
            sti = idx[4].find(cx, cy)
            nm = normalise(f"{nm} ({by_level[4][sti].name if sti is not None else ''})")
        city_name[i] = nm

    # strings: build with de-duplication; offsets returned by encode_strings
    strings, str_index = [], {}

    def sid(text, trans=4):
        k = (trans, text)
        if k not in str_index:
            str_index[k] = len(strings)
            strings.append((trans, text.encode("latin-1")))
        return str_index[k]

    # road records grouped by (city, name)
    groups = collections.defaultdict(list)
    for s in range(g.n_segs):
        name, ref = seg_labels[s]
        nm = normalise(name) or normalise(ref)
        groups[(city_of_seg[s], nm)].append(s)
        stats["named segments" if nm else "unnamed segments"] += 1
    road_keys = sorted(groups, key=lambda k: (k[0] if k[0] is not None else -1, search.collate_key(k[1])))
    records, rec_names = [], []
    for key in road_keys:
        segs = groups[key]
        xs, ys = [], []
        for s in segs:
            for n in (g.seg_a[s], g.seg_b[s]):
                xs.append(g.node_xy[n][0])
                ys.append(g.node_xy[n][1])
        variants = [key[1]] + rotations(key[1]) if key[1] else []
        nodes = collections.Counter(n for s in segs for n in (g.seg_a[s], g.seg_b[s]))
        inter = sum(1 for n in nodes if len(g.node_arms[n]) >= 3)
        records.append({"countryID": catalog_id, "cityID": key[0], "roadInfo": 0x76 if variants else 0,
                        "min_long": min(xs), "min_lat": min(ys), "delta_long": max(xs) - min(xs),
                        "delta_lat": max(ys) - min(ys), "maxNumber": 0, "minNumber": 0,
                        "intersectionCount": min(inter, 0xFFFF), "names": variants})
        rec_names.append(variants)

    # cities table (needs county per city)
    cities = []
    city_rec = {}
    for i in used:
        a = loc[i]
        cx, cy = a.centroid()
        lga = idx[6].find(cx, cy)
        name = city_name[i]
        variants = [name] + [v for v in rotations(name)]
        pci = idx[0].find(cx, cy)
        postcode = a.postcode or (by_level[0][pci].postcode if pci is not None else None)
        if postcode:
            variants.append(f"{postcode}, {name}"[:MAXLEN])
        city_rec[i] = len(cities)
        cities.append({"cityID": None, "countyID": lga_cid.get(lga, 0), "cover": a.bbox,
                       "cataloglistIdFirst": catalog_id, "centre": (cx, cy), "cityIdMunicipality": 0,
                       "variants": variants})
    # unplaced segments go to a catch-all city so every road has one
    if stats["segments outside any locality"]:
        city_rec[None] = len(cities)
        xs = [p[0] for p in g.node_xy]
        ys = [p[1] for p in g.node_xy]
        cities.append({"cityID": None, "countyID": 0, "cover": (min(xs), min(ys), max(xs), max(ys)),
                       "cataloglistIdFirst": catalog_id, "centre": ((min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2),
                       "cityIdMunicipality": 0, "variants": ["UNKNOWN LOCALITY"]})

    # string table: road names first, then city names
    for v in rec_names:
        for t in v:
            sid(t)
    for c in cities:
        for t in c["variants"]:
            sid(t)
    str_raw, str_offs = search_encode_strings(factory["str_header"], strings)

    # cities -> byte offsets (cityID)
    for c in cities:
        c["name_offsets"] = [str_offs[str_index[(4, t)]] for t in c["variants"]]
    pos = 0
    for c in cities:
        c["cityID"] = pos
        c["cityIdMunicipality"] = pos
        pos += search.CITY.size + 4 * len(c["name_offsets"])
    cty_raw, _ = search.encode_cities(factory["cty_header"], cities)

    # road records -> byte offsets (roadID), names -> string offsets
    rd_body = bytearray()
    road_id = []
    for r, key in zip(records, road_keys):
        road_id.append(len(rd_body))
        r["cityID"] = cities[city_rec[key[0]]]["cityID"]
        r["names"] = [(str_offs[str_index[(4, t)]], 0) for t in r["names"]]
        rd_body += REC.pack(r["countryID"], r["cityID"], r["roadInfo"], r["min_long"], r["min_lat"],
                            r["delta_long"], r["delta_lat"], r["maxNumber"], r["minNumber"],
                            len(r["names"]), r["intersectionCount"])
        rd_body += b"".join(NAME.pack(o, q) for o, q in r["names"])
    from .common import build_header
    rd_raw = build_header(factory["rd_header"], len(records), HDR + len(rd_body)) + bytes(rd_body)

    # segment -> road link
    rid_of = {key: road_id[k] for k, key in enumerate(road_keys)}
    for key, segs in groups.items():
        for s in segs:
            g.seg_names[s] = [("abs_addr", 0, rid_of[key], b"")]

    # road lists + road trees, one tree per city in city-table order
    roads_of_city = collections.defaultdict(list)
    for k, key in enumerate(road_keys):
        for q, t in enumerate(rec_names[k]):
            roads_of_city[city_rec[key[0]]].append((t, road_id[k], q))
    rl_recs, trees = [], []
    tree_off_of_city = []
    tree_pos = 0
    for ci in range(len(cities)):
        entries = sorted(roads_of_city.get(ci, []), key=lambda e: search.collate_key(e[0]))
        start = len(rl_recs)
        rl_recs += [struct.pack("<IB", rid, q) for _, rid, q in entries]
        t = search.build_tree([e[0] for e in entries], start, root_key=b"1", leaf_max=8)
        tree_off_of_city.append(tree_pos)
        tree_pos += 12 + 19 * len(t["nodes"])
        trees.append(t)
    rt_raw, rt_offs = search.encode_trees(factory["rt_header"], trees)
    rl_raw = search.encode_records(factory["rl_header"], rl_recs)

    # city list + city tree (catalog-wide)
    cl_entries = []
    for ci, c in enumerate(cities):
        for q, t in enumerate(c["variants"]):
            cl_entries.append((t, c["cityID"], q, rt_offs[ci]))
    cl_entries.sort(key=lambda e: search.collate_key(e[0]))
    cl_raw = search.encode_records(factory["cl_header"],
                                   [struct.pack("<IHi", cid, q, o) for _, cid, q, o in cl_entries])
    ct_tree = search.build_tree([e[0] for e in cl_entries], 0, root_key=b"2", leaf_max=8)
    # second catalog (NZ) left empty: a root-only tree after the AU one
    n = len(cl_entries)
    empty = {"listID_start": n, "listID_end": n - 1, "nodes": [(b"*", 1, 1, 0, 0), (b"2", 0, 0, 0, 0)]}
    ct_raw, ct_offs = search.encode_trees(factory["ct_header"], [ct_tree, empty])

    cny_raw = search.encode_records(factory["cny"][:HDR], [search.encode_county(c) for c in counties])

    # catalog list: AU city tree at ct_offs[0], NZ (empty) at ct_offs[1]
    out = {"auz.str": str_raw, "auz.rd": rd_raw, "auz.cty": cty_raw, "auz.cl": cl_raw, "auz.ct": ct_raw,
           "auz.rl": rl_raw, "auz.rt": rt_raw, "auz.cny": cny_raw}
    if "cal" in factory:
        recs = [bytearray(r) for r in search.records(factory["cal"], 48)]
        for k, r in enumerate(recs):
            struct.pack_into("<i", r, 40, ct_offs[k] if k < len(ct_offs) else -1)
        out["au.cal"] = search.encode_records(factory["cal"][:HDR], [bytes(r) for r in recs])
    if "cat" in factory:
        recs = [bytearray(r) for r in search.records(factory["cat"], 19)]
        xs = [p[0] for p in g.node_xy]
        ys = [p[1] for p in g.node_xy]
        struct.pack_into("<4i", recs[catalog_id], 3, min(xs), min(ys), max(xs), max(ys))
        out["au.cat"] = search.encode_records(factory["cat"][:HDR], [bytes(r) for r in recs])

    stats.update({"cities": len(cities), "counties": len(counties), "road records": len(records),
                  "strings": len(strings), "city list": len(cl_entries), "road list": len(rl_recs)})
    return out, stats


def search_encode_strings(template, entries):
    from .roads import encode_strings
    return encode_strings(template, entries)
