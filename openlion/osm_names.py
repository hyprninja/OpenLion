"""OSM -> street names and address search (road records, cities, lists, speller trees).

Model (matches the factory conventions, report 10 / 13 / 18):
- city   = Australia: OSM admin_level=9 suburb/locality area. New Zealand: the nearest
           place=* point (or place area), suburbs listed under their town / city.
  county = admin_level=6 LGA / district; state = admin_level=4 (mapped onto the
           factory state list by name). One search catalog per country (countries.py).
- road record = one per (normalised street name, city), bbox over its segments, plus
           one nameless record per city for unnamed segments (factory: roadInfo 0).
- names  = base + rotations of the leading words (WARNER GLEN MILL ROAD ->
           GLEN MILL ROAD;WARNER, MILL ROAD;WARNER GLEN); city names likewise
           (NORTH MELBOURNE -> MELBOURNE;NORTH) plus "POSTCODE, NAME" when known.
- every segment links (abs_addr) to exactly one road record.
"""
import array
import collections
import struct
import unicodedata

from . import countries, roads, search
from .common import HDR
from .graph import Shape

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
# Cities the factory models as one municipality: its suburbs are named "SUBURB, CITY",
# point at it (cityIdMunicipality), and the city's own street list covers all of them
# (factory: CANBERRA with 123 suburbs; every NZ town with suburbs). Per country in
# countries.py; this is Australia's, kept for older callers.
MUNICIPALITIES = countries.AU.municipalities


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
    __slots__ = ("level", "name", "postcode", "rings", "bbox", "osm_id", "country", "kind", "_bands")

    def __init__(self, level, name, postcode, rings, osm_id, country=0, kind=None):
        """rings: [(outer, [inner, ...])], each a list of (x, y); stored as flat arrays."""
        self.level, self.name, self.postcode, self.osm_id = level, name, postcode, osm_id
        self.rings = [(Shape(o), [Shape(i) for i in inners]) for o, inners in rings]
        self.country, self.kind = country, kind     # catalog; place=* value for place areas
        xs = [x for r in rings for x, _ in r[0]]
        ys = [y for r in rings for _, y in r[0]]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))

    def contains(self, x, y):
        b = self.bbox
        if not (b[0] <= x <= b[2] and b[1] <= y <= b[3]):
            return False
        try:
            bands = self._bands
        except AttributeError:       # built on first use (not pickled with the read cache)
            bands = self._bands = [(_bands(o), [_bands(i) for i in inners]) for o, inners in self.rings]
        inside = False
        for (outer, inners), (bo, bi) in zip(self.rings, bands):
            if _pip(outer, x, y, bo) and not any(_pip(i, x, y, b) for i, b in zip(inners, bi)):
                inside = not inside
        return inside

    def __getstate__(self):
        return (None, {k: getattr(self, k) for k in self.__slots__ if k != "_bands" and hasattr(self, k)})

    def centroid(self):
        outer = max((r[0] for r in self.rings), key=len)
        n = len(outer)
        return (sum(x for x, _ in outer) // n, sum(y for _, y in outer) // n)


BAND_MIN_POINTS = 64     # rings smaller than this are tested edge by edge


def _bands(ring):
    """Edge index of a big ring by y band: bands[b] = indices i of the edges (i-1 -> i)
    whose y range touches band b. Only those can change the even-odd count for a point
    in band b, so _pip gives the same answer while testing a few edges, not all."""
    xy = ring.xy
    m = len(xy) // 2
    if m < BAND_MIN_POINTS:
        return None
    ys = xy[1::2]
    y0, y1 = min(ys), max(ys)
    n = max(1, min(4096, m // 8))
    h = (y1 - y0) // n + 1
    out = [array.array("i") for _ in range(n)]
    yj = xy[-1]
    for i in range(m):
        yi = xy[2 * i + 1]
        lo, hi = (yi, yj) if yi < yj else (yj, yi)
        if lo != hi:                 # a horizontal edge never counts
            for b in range((lo - y0) // h, (hi - y0) // h + 1):
                out[b].append(i)
        yj = yi
    return (y0, h, out)


def _pip(ring, x, y, bands=None):
    """Point in ring (a Shape: flat x/y array). Even-odd rule."""
    xy = ring.xy
    c = False
    if bands is not None:
        y0, h, out = bands
        b = int((y - y0) // h)
        if not 0 <= b < len(out):
            return False
        m2 = len(xy)
        for i in out[b]:
            xi, yi = xy[2 * i], xy[2 * i + 1]
            j = 2 * i - 2 if i else m2 - 2
            xj, yj = xy[j], xy[j + 1]
            if (yi > y) != (yj > y) and x < xi + (y - yi) * (xj - xi) / (yj - yi):
                c = not c
        return c
    xj, yj = xy[-2], xy[-1]
    for i in range(0, len(xy), 2):
        xi, yi = xy[i], xy[i + 1]
        if (yi > y) != (yj > y) and x < xi + (y - yi) * (xj - xi) / (yj - yi):
            c = not c
        xj, yj = xi, yi
    return c


def read_boundaries(pbf_path, bbox=None, levels=("4", "6", "9"), country=0, node_index="flex_mem"):
    """Admin areas at the given admin_levels (Australia: 4 state, 6 LGA, 9 suburb /
    locality) plus postcode areas (level 0 here), overlapping bbox."""
    import osmium
    import osmium.filter
    kinds = (("boundary", "administrative"), ("boundary", "postal_code"))
    fp = (osmium.FileProcessor(pbf_path, osmium.osm.NODE | osmium.osm.WAY | osmium.osm.RELATION | osmium.osm.AREA)
          .with_locations(node_index)
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
                  rings, a.orig_id(), country)
        if bbox:
            b = ar.bbox
            if b[2] < bbox[0] or b[0] > bbox[2] or b[3] < bbox[1] or b[1] > bbox[3]:
                continue
        out.append(ar)
    return out


class Place:
    """A place=* point used as a locality (level 9) where there are no boundaries."""
    __slots__ = ("level", "name", "postcode", "x", "y", "bbox", "osm_id", "country", "kind")

    def __init__(self, name, x, y, kind, osm_id, country):
        self.level, self.name, self.postcode, self.x, self.y = 9, name, None, x, y
        self.kind, self.osm_id, self.country = kind, osm_id, country
        self.bbox = (x, y, x, y)        # widened to its streets by build()

    def centroid(self):
        return (self.x, self.y)


def read_places(pbf_path, bbox=None, country=0, node_index="flex_mem"):
    """place=* localities -> (areas as level-9 Area, points as Place). A point inside an
    area of the same name is dropped: the area is the better shape."""
    import osmium
    import osmium.filter
    kinds = [("place", k) for k in countries.PLACE_WEIGHT]
    fp = (osmium.FileProcessor(pbf_path, osmium.osm.NODE | osmium.osm.WAY | osmium.osm.RELATION | osmium.osm.AREA)
          .with_locations(node_index)
          .with_areas(osmium.filter.TagFilter(*kinds))
          .with_filter(osmium.filter.EntityFilter(osmium.osm.NODE | osmium.osm.AREA))
          .with_filter(osmium.filter.TagFilter(*kinds)))
    areas, points = [], []
    for o in fp:
        tags = {t.k: t.v for t in o.tags}
        name, kind = tags.get("name"), tags.get("place")
        if not name or kind not in countries.PLACE_WEIGHT:
            continue
        if o.is_node():
            x, y = round(o.location.lon * 1e5), round(o.location.lat * 1e5)
            if bbox and not (bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]):
                continue
            points.append(Place(name, x, y, kind, o.id, country))
            continue
        rings = []
        for outer in o.outer_rings():
            r = [(round(n.lon * 1e5), round(n.lat * 1e5)) for n in outer]
            inners = [[(round(n.lon * 1e5), round(n.lat * 1e5)) for n in inner] for inner in o.inner_rings(outer)]
            rings.append((r, inners))
        if not rings:
            continue
        a = Area(9, name, tags.get("postal_code") or tags.get("addr:postcode"), rings, o.orig_id(), country, kind)
        b = a.bbox
        if bbox and (b[2] < bbox[0] or b[0] > bbox[2] or b[3] < bbox[1] or b[1] > bbox[3]):
            continue
        areas.append(a)
    idx = AreaIndex(areas)
    keep = []
    for p in points:
        i = idx.find(p.x, p.y)
        if i is not None and normalise(areas[i].name) == normalise(p.name):
            continue
        keep.append(p)
    return areas, keep


class PlaceIndex:
    """Nearest place point. Plain distance with accept= (a filter); otherwise distance
    divided by PLACE_WEIGHT[kind], so a town's streets reach further than a hamlet's."""

    def __init__(self, places, cell=2000):
        self.cell, self.grid, self.places = cell, collections.defaultdict(list), places
        for i, p in enumerate(places):
            self.grid[(p.x // cell, p.y // cell)].append(i)
        self.wmax = max((countries.PLACE_WEIGHT[p.kind] for p in places), default=1.0)
        if self.grid:
            gx = [k[0] for k in self.grid]
            gy = [k[1] for k in self.grid]
            self.lo, self.hi = (min(gx), min(gy)), (max(gx), max(gy))
        else:
            self.lo = self.hi = (0, 0)

    def nearest(self, x, y, accept=None, max_dist=None):
        if not self.places:
            return None
        cx, cy = x // self.cell, y // self.cell
        # rings needed to cover every point from here
        span = max(abs(cx - self.lo[0]), abs(cx - self.hi[0]), abs(cy - self.lo[1]), abs(cy - self.hi[1])) + 1
        weighted = accept is None
        best, best_d = None, float("inf")
        for k in range(span + 1):
            for gx in range(cx - k, cx + k + 1):
                for gy in ((cy - k, cy + k) if gx not in (cx - k, cx + k) else range(cy - k, cy + k + 1)):
                    for i in self.grid.get((gx, gy), ()):
                        p = self.places[i]
                        if accept and not accept(p):
                            continue
                        d = ((p.x - x) ** 2 + (p.y - y) ** 2) ** 0.5
                        if max_dist is not None and d > max_dist:
                            continue
                        if weighted:
                            d /= countries.PLACE_WEIGHT[p.kind]
                        if d < best_d:
                            best, best_d = i, d
            # every point beyond ring k is at least k cells away
            reach = k * self.cell / (self.wmax if weighted else 1.0)
            if best is not None and reach > best_d:
                break
            if max_dist is not None and k * self.cell > max_dist:
                break
        return best


def _box_area(a):
    b = a.bbox
    return (b[2] - b[0]) * (b[3] - b[1])


def dedupe_places(areas, points, margin=5000):
    """One locality per place: OSM often maps a place twice (Christchurch is a city area
    and a city point; Lyttelton a suburb area inside a town area of the same name).
    An area inside a bigger area of the same name is dropped, and so is a point within
    `margin` of an area of the same name."""
    by_name = collections.defaultdict(list)
    drop = set()
    for a in sorted(areas, key=_box_area, reverse=True):
        n = normalise(a.name)
        if any(k.contains(*a.centroid()) for k in by_name[n]):
            drop.add(id(a))
            continue
        by_name[n].append(a)
    kept_points = []
    for p in points:
        if any(k.bbox[0] - margin <= p.x <= k.bbox[2] + margin and k.bbox[1] - margin <= p.y <= k.bbox[3] + margin
               for k in by_name[normalise(p.name)]):
            continue
        kept_points.append(p)
    return [a for a in areas if id(a) not in drop], kept_points


class Localities:
    """Every city-level locality of every country, and which one a point is in.

    loc: boundary areas (admin_level=9), then place areas, then place points.
    find(x, y, country): the boundary area containing the point (Australia); else the
    smallest place area containing it (place areas nest: suburbs inside a city); else,
    for a country whose localities are places, the nearest place point; else None."""

    def __init__(self, areas, places=()):
        lv9 = [a for a in areas if a.level == 9]
        bounds = [a for a in lv9 if a.kind is None]
        p_areas, places = dedupe_places([a for a in lv9 if a.kind is not None], list(places))
        self.n_bounds, self.n_areas = len(bounds), len(bounds) + len(p_areas)
        self.loc = bounds + p_areas + places
        self.area_idx = AreaIndex(bounds)
        self.place_area_idx = AreaIndex(p_areas)
        by_c = collections.defaultdict(list)
        for k, p in enumerate(places):
            by_c[p.country].append(k)
        self.point_idx = {c: (PlaceIndex([places[k] for k in ks]), ks) for c, ks in by_c.items()}

    def find(self, x, y, country=0):
        i = self.area_idx.find(x, y)
        if i is not None:
            return i
        j = self.place_area_idx.find_smallest(x, y)
        if j is not None:
            return self.n_bounds + j
        pi = self.point_idx.get(country)
        if pi is None:
            return None
        j = pi[0].nearest(x, y)
        return None if j is None else self.n_areas + pi[1][j]


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

    def find_smallest(self, x, y):
        """The smallest (by bounding box) area containing the point, for nested areas."""
        hits = [i for i in self.grid.get((x // self.cell, y // self.cell), []) if self.areas[i].contains(x, y)]
        return min(hits, key=lambda i: _box_area(self.areas[i])) if hits else None


# ---- tables -------------------------------------------------------------------

def segment_point(g, s):
    sh = g.seg_shape[s]
    if sh:
        return sh[len(sh) // 2]
    (x1, y1), (x2, y2) = g.node_xy[g.seg_a[s]], g.node_xy[g.seg_b[s]]
    return ((x1 + x2) // 2, (y1 + y2) // 2)


def build(g, seg_labels, areas, factory, catalog_id=0, places=()):
    """Build road records, cities and search tables for graph g.

    seg_labels: per segment (name, ref) from OSM. areas: read_boundaries() output (plus
    read_places() areas), each tagged with its country's catalog. places: read_places()
    points. g.seg_country: catalog per segment (default: all catalog_id).
    factory: dict of factory raw tables used as templates (headers, states, time info).
    Every catalog in the factory list gets a city tree; one without roads gets an empty one.
    Fills g.seg_names in place. Returns {table name: raw bytes (uncompressed)} and stats."""
    stats = collections.Counter()
    seg_country = getattr(g, "seg_country", None) or [catalog_id] * g.n_segs
    by_level = {lv: [a for a in areas if a.level == lv] for lv in (0, 4, 6)}
    idx = {lv: AreaIndex(v) for lv, v in by_level.items()}
    locs = Localities(areas, places)
    loc = locs.loc

    def profile(c):
        return countries.BY_CATALOG.get(c, countries.AU)

    # states: map OSM admin_level=4 names onto the factory state list (IDs kept)
    f_recs = search.records(factory["stt"], 152)
    f_states = [search._name(r[25:61]) for r in f_recs]
    f_state_country = [struct.unpack_from("<H", r, 98)[0] for r in f_recs]

    def default_state(c):
        return next((k for k, fc in enumerate(f_state_country) if fc == c), 0)

    def factory_state(a):
        nm = normalise(a.name)
        nm = profile(a.country).state_alias.get(nm, nm)
        for cand in (nm, nm.replace("-", " "), nm.replace(" REGION", "")):
            if cand in f_states and f_state_country[f_states.index(cand)] == a.country:
                return f_states.index(cand)
        stats["states not in the factory list"] += 1
        return default_state(a.country)

    state_id = {i: factory_state(a) for i, a in enumerate(by_level[4])}

    # counties = LGAs / districts; time zone copied from a factory county of the same state
    f_cny = [search.parse_county(r) for r in search.records(factory["cny"], 106)]
    tz_of_state = {}
    for c in f_cny:
        tz_of_state.setdefault(c["stateID"], c["timeinfoID"])
    counties = []
    lga_cid = {}
    for i, a in enumerate(by_level[6]):
        cx, cy = a.centroid()
        si_ = idx[4].find(cx, cy)
        st = state_id[si_] if si_ is not None else default_state(a.country)
        lga_cid[i] = len(counties)
        counties.append({"countyID": len(counties), "stateID": st, "zoneID": 0, "start_cityID": 0,
                         "end_cityID": 0, "cover": (0, 0, 0, 0), "trans1": 4,
                         "name": normalise(a.name), "trans2": 5, "phonetic": "",
                         "timeinfoID": tz_of_state.get(st, 0)})

    # cities = localities; assign every segment to one. A segment no locality claims
    # goes to its country's catch-all city, key ("none", catalog).
    city_of_seg = []
    extent = {}           # city key -> bbox of its segment points (covers for place points)
    cache = {}            # ~50 m grid: nearby segments share one lookup
    for s in range(g.n_segs):
        x, y = segment_point(g, s)
        c = seg_country[s]
        key = (c, x // 50, y // 50)
        i = cache.get(key, -1)
        if i == -1:
            i = cache[key] = locs.find(x, y, c)
        if i is None:
            stats["segments outside any locality"] += 1
            i = ("none", c)
        city_of_seg.append(i)
        e = extent.get(i)
        extent[i] = (x, y, x, y) if e is None else (min(e[0], x), min(e[1], y), max(e[2], x), max(e[3], y))
    stats["locality lookups"] = len(cache)
    cache = None

    def country_of(key):
        return key[1] if isinstance(key, tuple) else loc[key].country

    def cover_of(i):
        return extent.get(i, loc[i].bbox) if isinstance(loc[i], Place) else loc[i].bbox

    def state_of(i):
        return idx[4].find(*loc[i].centroid())

    used = sorted({c for c in city_of_seg if not isinstance(c, tuple)},
                  key=lambda i: (loc[i].country, normalise(loc[i].name)))

    # municipalities. muni_of: locality -> key; ("name", catalog, NAME) for the fixed ones
    # (Canberra), ("loc", j) where suburbs are grouped under town / city j.
    muni_of = {}
    for i in used:
        prof = profile(loc[i].country)
        if not prof.municipalities:
            continue
        sti = state_of(i)
        stn = normalise(by_level[4][sti].name) if sti is not None else None
        for m, st, _ in prof.municipalities:
            if st == stn:
                muni_of[i] = ("name", loc[i].country, m)
    for c in sorted({loc[i].country for i in used}):
        if not profile(c).suburbs_in_towns:
            continue
        towns = [Place(loc[j].name, *loc[j].centroid(), loc[j].kind, j, c) for j in range(len(loc))
                 if loc[j].country == c and loc[j].kind in countries.TOWN_KINDS]
        t_county = {t.osm_id: idx[6].find(t.x, t.y) for t in towns}
        t_idx = PlaceIndex(towns)
        for i in used:
            if loc[i].country != c or loc[i].kind not in countries.SUBURB_KINDS or i in muni_of:
                continue
            x, y = loc[i].centroid()
            county = idx[6].find(x, y)
            k = t_idx.nearest(x, y, accept=lambda t: t_county[t.osm_id] == county,
                              max_dist=countries.SUBURB_TOWN_MAX)
            if k is not None and normalise(towns[k].name) != normalise(loc[i].name):
                muni_of[i] = ("loc", towns[k].osm_id)
        stats[f"{profile(c).code} suburbs under a town"] = sum(1 for i in muni_of if loc[i].country == c)

    # disambiguate duplicate names with the state, like the factory "(VICTORIA)"
    names_count = collections.Counter((loc[i].country, normalise(loc[i].name)) for i in used if i not in muni_of)
    city_name = {}
    for i in used:
        nm = normalise(loc[i].name)
        if i not in muni_of and names_count[(loc[i].country, nm)] > 1:
            sti = state_of(i)
            nm = normalise(f"{nm} ({by_level[4][sti].name if sti is not None else ''})")
        city_name[i] = nm

    def muni_name(key):
        if key[0] == "name":
            return key[2]
        j = key[1]
        return city_name.get(j) or normalise(loc[j].name)

    # strings: build with de-duplication; offsets returned by encode_strings
    strings, str_index = [], {}

    def sid(text, trans=4):
        k = (trans, text)
        if k not in str_index:
            str_index[k] = len(strings)
            strings.append((trans, text.encode("latin-1")))
        return str_index[k]

    def union(boxes):
        boxes = list(boxes)
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    # cities table, one country after the other (factory: Australia, then New Zealand)
    cities, city_rec, city_country = [], {}, []
    present = sorted({country_of(k) for k in city_of_seg}) if city_of_seg else []
    for c in present:
        mine = [i for i in used if loc[i].country == c]
        for i in mine:
            a = loc[i]
            cx, cy = a.centroid()
            lga = idx[6].find(cx, cy)
            name = city_name[i]
            variants = [name] + [v for v in rotations(name)]
            pci = idx[0].find(cx, cy)
            postcode = a.postcode or (by_level[0][pci].postcode if pci is not None else None)
            m = muni_of.get(i)
            if m:   # factory style: "ISABELLA PLAINS, CANBERRA", "PLAINS;ISABELLA, CANBERRA", "2905, CANBERRA"
                mn = muni_name(m)
                variants = [f"{v}, {mn}"[:MAXLEN] for v in variants]
                if postcode:
                    variants.append(f"{postcode}, {mn}")
            elif postcode:
                variants.append(f"{postcode}, {name}"[:MAXLEN])
            city_rec[i] = len(cities)
            city_country.append(c)
            cities.append({"cityID": None, "countyID": lga_cid.get(lga, 0), "cover": cover_of(i),
                           "cataloglistIdFirst": c, "centre": (cx, cy), "cityIdMunicipality": 0,
                           "variants": variants})
        # municipalities: one city over all their suburbs (a town's own record where it has one)
        keys = sorted({m for i, m in muni_of.items() if loc[i].country == c}, key=lambda m: str(m))
        for m in keys:
            kids = [i for i in mine if muni_of.get(i) == m]
            if m[0] == "loc" and m[1] in city_rec:
                rec = cities[city_rec[m[1]]]
                rec["cover"] = union([rec["cover"]] + [cover_of(i) for i in kids])
                city_rec[m] = city_rec[m[1]]
                continue
            if m[0] == "loc":
                centre = loc[m[1]].centroid()
            else:
                cbd = next(cbd for name, _, cbd in profile(c).municipalities if name == m[2])
                centre = loc[next((i for i in kids if city_name[i] == cbd), kids[0])].centroid()
            lga = idx[6].find(*centre)
            city_rec[m] = len(cities)
            city_country.append(c)
            cities.append({"cityID": None, "countyID": lga_cid.get(lga, cities[city_rec[kids[0]]]["countyID"]),
                           "cover": union(cover_of(i) for i in kids), "cataloglistIdFirst": c,
                           "centre": centre, "cityIdMunicipality": 0, "variants": [muni_name(m)]})
            if m[0] == "name":
                stats[f"{m[2].lower()} suburbs"] = len(kids)
        # segments no locality claims go to a catch-all city so every road has one
        none = ("none", c)
        if none in extent:
            b = extent[none]
            city_rec[none] = len(cities)
            city_country.append(c)
            cities.append({"cityID": None, "countyID": 0, "cover": b, "cataloglistIdFirst": c,
                           "centre": ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2),
                           "cityIdMunicipality": 0, "variants": ["UNKNOWN LOCALITY"]})

    # road records grouped by (city, name), in city-table order
    groups = collections.defaultdict(list)
    for s in range(g.n_segs):
        name, ref = seg_labels[s]
        nm = normalise(name) or normalise(ref)
        groups[(city_of_seg[s], nm)].append(s)
        stats["named segments" if nm else "unnamed segments"] += 1
    road_keys = sorted(groups, key=lambda k: (city_rec[k[0]], search.collate_key(k[1])))
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
        records.append({"countryID": city_country[city_rec[key[0]]], "cityID": key[0],
                        "roadInfo": 0x76 if variants else 0,
                        "min_long": min(xs), "min_lat": min(ys), "delta_long": max(xs) - min(xs),
                        "delta_lat": max(ys) - min(ys), "maxNumber": 0, "minNumber": 0,
                        "intersectionCount": min(inter, 0xFFFF), "names": variants})
        rec_names.append(variants)

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
    for i, m in muni_of.items():
        if city_rec[i] != city_rec[m]:
            cities[city_rec[i]]["cityIdMunicipality"] = cities[city_rec[m]]["cityID"]
    cty_raw, _ = search.encode_cities(factory["cty_header"], cities)

    # road records -> byte offsets (roadID), names -> string offsets
    rd_body = bytearray()
    road_id = []
    for r, key in zip(records, road_keys):
        road_id.append(len(rd_body))
        r["cityID"] = cities[city_rec[key[0]]]["cityID"]
        r["names"] = [(str_offs[str_index[(4, t)]], 0) for t in r["names"]]
        rd_body += roads.encode_road(r)   # layout of the active datamodel
    from .common import build_header
    n_records = len(records)
    rd_raw = build_header(factory["rd_header"], n_records, HDR + len(rd_body)) + bytes(rd_body)
    records = rd_body = None

    # segment -> road link: one shared (read-only) link per road for all its segments
    for k, key in enumerate(road_keys):
        link = (("abs_addr", 0, road_id[k], b""),)
        for s in groups[key]:
            g.seg_names[s] = link
    groups = None

    # road lists + road trees, one tree per city in city-table order
    roads_of_city = collections.defaultdict(list)
    for k, key in enumerate(road_keys):
        for q, t in enumerate(rec_names[k]):
            roads_of_city[city_rec[key[0]]].append((t, road_id[k], q))
            m = muni_of.get(key[0]) if not isinstance(key[0], tuple) else None
            if m and city_rec[m] != city_rec[key[0]]:   # the municipality lists every suburb's streets too
                roads_of_city[city_rec[m]].append((t, road_id[k], q))
    rl_recs, trees = [], []
    for ci in range(len(cities)):
        entries = sorted(roads_of_city.get(ci, []), key=lambda e: search.collate_key(e[0]))
        start = len(rl_recs)
        rl_recs += [struct.pack("<IB", rid, q) for _, rid, q in entries]
        trees.append(search.build_tree([e[0] for e in entries], start, root_key=b"1", leaf_max=8))
    rt_raw, rt_offs = search.encode_trees(factory["rt_header"], trees)
    rl_raw = search.encode_records(factory["rl_header"], rl_recs)

    # city list + one city tree per catalog (factory: Australia's list, then New Zealand's)
    n_catalogs = len(search.records(factory["cal"], 48)) if "cal" in factory else max(present, default=0) + 1
    cl_entries, ct_trees = [], []
    for cat in range(n_catalogs):
        mine = []
        for ci, c in enumerate(cities):
            if city_country[ci] == cat:
                for q, t in enumerate(c["variants"]):
                    mine.append((t, c["cityID"], q, rt_offs[ci]))
        mine.sort(key=lambda e: search.collate_key(e[0]))
        start = len(cl_entries)
        cl_entries += mine
        if mine:
            ct_trees.append(search.build_tree([e[0] for e in mine], start, root_key=b"2", leaf_max=8))
        else:   # a country left off the card: a root-only tree (end is unsigned: 0 when first)
            ct_trees.append({"listID_start": start, "listID_end": max(start - 1, 0),
                             "nodes": [(b"*", 1, 1, 0, 0), (b"2", 0, 0, 0, 0)]})
        stats[f"{profile(cat).code} cities"] = sum(1 for x in city_country if x == cat)
    cl_raw = search.encode_records(factory["cl_header"],
                                   [struct.pack("<IHi", cid, q, o) for _, cid, q, o in cl_entries])
    ct_raw, ct_offs = search.encode_trees(factory["ct_header"], ct_trees)

    cny_raw = search.encode_records(factory["cny"][:HDR], [search.encode_county(c) for c in counties])

    out = {"auz.str": str_raw, "auz.rd": rd_raw, "auz.cty": cty_raw, "auz.cl": cl_raw, "auz.ct": ct_raw,
           "auz.rl": rl_raw, "auz.rt": rt_raw, "auz.cny": cny_raw}
    # catalog list: each catalog's city tree offset
    if "cal" in factory:
        recs = [bytearray(r) for r in search.records(factory["cal"], 48)]
        for k, r in enumerate(recs):
            struct.pack_into("<i", r, 40, ct_offs[k] if k < len(ct_offs) else -1)
        out["au.cal"] = search.encode_records(factory["cal"][:HDR], [bytes(r) for r in recs])
    # catalogs: bounding box of each country's roads (factory box kept for an empty one)
    if "cat" in factory:
        recs = [bytearray(r) for r in search.records(factory["cat"], 19)]
        boxes = {}
        for s in range(g.n_segs):
            for n in (g.seg_a[s], g.seg_b[s]):
                x, y = g.node_xy[n]
                b = boxes.get(seg_country[s])
                boxes[seg_country[s]] = (x, y, x, y) if b is None else (
                    min(b[0], x), min(b[1], y), max(b[2], x), max(b[3], y))
        for cat, b in boxes.items():
            if cat < len(recs):
                struct.pack_into("<4i", recs[cat], 3, *b)
        out["au.cat"] = search.encode_records(factory["cat"][:HDR], [bytes(r) for r in recs])

    stats.update({"cities": len(cities), "counties": len(counties), "road records": n_records,
                  "strings": len(strings), "city list": len(cl_entries), "road list": len(rl_recs)})
    return out, stats


def search_encode_strings(template, entries):
    from .roads import encode_strings
    return encode_strings(template, entries)
