"""Road-sense alerts for school zones and railway level crossings (report 27).

The factory POI database has road-sense categories besides "camera" (report 23):

  school zone    one POI per zone, SpeedLimit 40, no Direction (MY12: 6,687)
  school point   one per way into a zone, SpeedLimit 40, Direction = the travel heading
                 into the zone (MY12: 24,586, 40-200 m from the zone's own POI)
  railway        one per level crossing approach, no SpeedLimit, Direction = travel
                 heading (MY10: a pair per crossing, 90 / 270; MY12 mostly one)

Sources, like the cameras: a government list where one is published openly, else
OpenStreetMap.

School zones
  NSW  Transport for NSW "School Zones data" (CC BY 4.0): 3,300 zone polygons drawn
       around the roads, with speed and times. The zone's entries are where a road
       crosses into the polygon. NSW's OSM school zones are dropped.
  else OSM ways with a school-hours maxspeed:conditional ("40 @ (Mo-Fr 08:00-09:30,
       14:30-16:00; SH off)") or hazard=school_zone. Touching ways make one zone; its
       entries are where another road joins it (or it carries on as a normal road).

Level crossings
  OSM railway=level_crossing nodes everywhere, kept where the node is on a working
  railway (not disused / abandoned) and on a road on the map: the node gives the exact
  spot and the road gives the headings. Then the official lists add the crossings OSM
  is missing (none within MATCH_M):
  NSW  Transport for NSW "NSW Level Crossings on Public Roads" (ALCAM, CC BY 4.0)
  WA   Main Roads WA "Rail Crossings" (CC BY 4.0): public crossings still in use
"""
import collections
import datetime
import io
import json
import math
import os
import re
import urllib.parse
import urllib.request
import zipfile

from . import cameras, osm_import

UA = "OpenLion (https://github.com/hyprninja/OpenLion)"
TIMEOUT = 120

SOURCES = {
    ("NSW", "school_zones"): {
        "name": "New South Wales school zones",
        "provider": "Transport for NSW, CC BY 4.0",
        "url": "https://opendata.transport.nsw.gov.au/data/dataset/4253a054-b377-4b5b-83d1-71385bb6ff33/"
               "resource/b6d7d02c-5625-461c-8b44-bba1a6ef3e0f/download/schoolzones.zip",
    },
    ("NSW", "crossings"): {
        "name": "New South Wales level crossings",
        "provider": "Transport for NSW (ALCAM), CC BY 4.0",
        "url": "https://opendata.transport.nsw.gov.au/data/dataset/0647e183-24d9-4914-8420-a8ac4bde62a3/"
               "resource/1b347345-1d23-4833-95eb-c7a44be5d27a/download/level_crossings.geojson",
    },
    ("WA", "crossings"): {
        "name": "Western Australia rail crossings",
        "provider": "Main Roads Western Australia, CC BY 4.0",
        "url": "https://gisservices.mainroads.wa.gov.au/arcgis/rest/services/OpenData/"
               "RoadAssets_DataPortal/MapServer/15",
    },
}

# factory category names (looked up per card) and the POI name each gets
CATEGORY = {"school_zone": "school zone", "school_point": "school point", "railway": "railway"}
POI_NAME = {"school_zone": "SchoolZone", "school_point": "SchoolPoint", "railway": "Railway"}

ACTIVE_RAIL = {"rail", "light_rail", "narrow_gauge", "preserved", "subway", "monorail", "funicular"}
SCHOOL_COND = re.compile(r"^\s*(\d+)\s*@\s*\((.*)")     # "40 @ (Mo-Fr 08:00-09:30,...; SH off)"
SNAP_GOV = 40.0          # metres from an official crossing point to its road
MATCH_M = 100.0          # an OSM crossing this close is the same crossing
RAIL_CELL = 100          # deg * 1e5 (~100 m): grid of the working railways (read_osm rail_cells)
DEDUP_M = 25.0           # entries to one zone closer than this, same heading: one
DEFAULT_SCHOOL_SPEED = 40


# ---- downloads -----------------------------------------------------------------

def _http(url, params=None, raw=False):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = r.read()
    return data if raw else json.loads(data.decode("utf-8"))


def parse_nsw_school_zones(blob):
    """The zip's SchoolZones.json: [{"generated"}, zone, ...], polygons in WGS 84."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".json"))
        rows = json.load(z.open(name))
    out = []
    for r in rows:
        g = r.get("json_geometry") if isinstance(r, dict) else None
        if not g or (r.get("SZ_STATUS") or "Existing") != "Existing":
            continue
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"] if g["type"] == "MultiPolygon" else []
        rings = [[(round(p[0], 6), round(p[1], 6)) for p in ring] for poly in polys for ring in poly if len(ring) >= 4]
        if not rings:
            continue
        m = re.match(r"\s*(\d+)", r.get("SZ_SPEED") or "")
        out.append({"id": r.get("SZ_ZONE_ID"), "name": r.get("SCL_NAME"),
                    "speed": int(m.group(1)) if m else DEFAULT_SCHOOL_SPEED,
                    "oneway": (r.get("SZ_DIRECTION") or "").lower().startswith("one"), "rings": rings})
    return out


def parse_nsw_crossings(doc):
    out = []
    for f in doc.get("features", []):
        p, g = f.get("properties") or {}, f.get("geometry") or {}
        if g.get("type") != "Point" or p.get("ACTIVE", "Y") != "Y":
            continue
        if (p.get("LOC_ERROR") or "").strip().upper() in ("NO ROAD", "NO ROADS", "NO RAIL"):   # not "NO ERROR"
            continue
        out.append({"id": p.get("CROSSING_NUMBER") or p.get("OBJECTID"), "lon": g["coordinates"][0],
                    "lat": g["coordinates"][1], "road": p.get("ROAD_NAME")})
    return out


def parse_wa_crossings(features):
    out = []
    for f in features:
        p, g = f.get("properties") or {}, f.get("geometry") or {}
        if g.get("type") != "Point":
            continue
        if (p.get("IN_USE") or "").lower() == "no" or (p.get("XING_TYPE") or "Public") != "Public":
            continue
        out.append({"id": p.get("XING_NO") or p.get("OBJECTID"), "lon": g["coordinates"][0],
                    "lat": g["coordinates"][1], "road": p.get("COMMON_USAGE_NAME") or p.get("ROAD_NAME")})
    return out


def fetch_one(state, kind):
    src = SOURCES[(state, kind)]
    if (state, kind) == ("NSW", "school_zones"):
        return parse_nsw_school_zones(_http(src["url"], raw=True))
    if (state, kind) == ("NSW", "crossings"):
        return parse_nsw_crossings(_http(src["url"]))
    if (state, kind) == ("WA", "crossings"):
        feats, off = [], 0
        while True:
            d = _http(src["url"] + "/query", {"where": "1=1", "outFields": "*", "outSR": "4326", "f": "geojson",
                                              "resultOffset": off, "resultRecordCount": 1000,
                                              "orderByFields": "OBJECTID"})
            got = d.get("features", [])
            feats += got
            if len(got) < 1000:
                break
            off += len(got)
        return parse_wa_crossings(feats)
    raise ValueError((state, kind))


def folder_of(gov_dir):
    return os.path.join(gov_dir, "alerts")


def path_of(gov_dir, state, kind):
    return os.path.join(folder_of(gov_dir), f"{state}_{kind}.json")


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def fetch(gov_dir, log=print, today=None):
    """Download every list into gov_dir/alerts. A failed or tiny download keeps the last
    good copy. Returns {"NSW school_zones": {"ok", "error", "fetched", "count"}, ...}."""
    os.makedirs(folder_of(gov_dir), exist_ok=True)
    today = today or datetime.date.today().isoformat()
    res = {}
    for (state, kind), src in SOURCES.items():
        path = path_of(gov_dir, state, kind)
        key = f"{state} {kind}"
        try:
            items = fetch_one(state, kind)
            if len(items) < 50:
                raise ValueError(f"only {len(items)} in the download")
            doc = {"state": state, "kind": kind, "name": src["name"], "provider": src["provider"],
                   "url": src["url"], "fetched": today, "items": items}
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                json.dump(doc, f, separators=(",", ":"))
            os.replace(path + ".tmp", path)
            res[key] = {"ok": True, "error": None, "fetched": today, "count": len(items)}
            log(f"{src['name']}: {len(items):,} from {src['provider']}")
        except Exception as e:  # keep the last copy, else OSM
            old = load(path) if os.path.isfile(path) else None
            res[key] = {"ok": False, "error": f"{type(e).__name__}: {e}",
                        "fetched": old["fetched"] if old else None, "count": len(old["items"]) if old else None}
            log(f"{src['name']}: download failed ({type(e).__name__}: {e}); "
                + (f"using the copy from {old['fetched']}" if old else "using OpenStreetMap"))
    return res


def available(gov_dir):
    """[path] of every list in gov_dir (what a build should use)."""
    if not gov_dir:
        return []
    return [path_of(gov_dir, s, k) for s, k in SOURCES if os.path.isfile(path_of(gov_dir, s, k))]


# ---- OSM read ------------------------------------------------------------------

def school_speed(tags):
    """A school zone way's speed, or None: a maxspeed:conditional for school hours
    (with 'SH off' or a morning time) or hazard=school_zone / school_zone=*."""
    cond = tags.get("maxspeed:conditional") or ""
    sp = None
    if cond:
        m = SCHOOL_COND.match(cond)
        if m:
            when = m.group(2)
            if "SH off" in when or re.search(r"\b0?[78]:[0-5]\d\s*-\s*0?9:[0-5]\d", when) or "school" in when.lower():
                sp = int(m.group(1))
    if sp is None and (tags.get("hazard") == "school_zone" or "school_zone" in tags
                       or tags.get("maxspeed:variable") == "school_zone"):
        sp = DEFAULT_SCHOOL_SPEED
    return sp if sp is None or 10 <= sp <= 60 else None


def read_osm(pbf_path, bbox=None):
    """-> {"crossings": [(node id, x, y)] on working railways, "school": {way id: speed},
    "rail_cells": {(x // RAIL_CELL, y // RAIL_CELL)} along working railways}.
    rail_cells lets an official crossing be checked against the railways that are still
    there: lists keep crossings on closed lines that OSM has as disused or removed."""
    import array
    import osmium
    import osmium.filter
    nodes, rail_nodes, school = {}, set(), {}
    rail_ways = []            # node refs of each working main line
    fp = (osmium.FileProcessor(pbf_path, osmium.osm.NODE | osmium.osm.WAY)
          .with_filter(osmium.filter.KeyFilter("railway", "maxspeed:conditional", "hazard", "school_zone",
                                               "maxspeed:variable")))
    for o in fp:
        if o.is_node():
            if o.tags.get("railway") == "level_crossing" and o.location.valid():
                x, y = round(o.location.lon * 1e5), round(o.location.lat * 1e5)
                if not bbox or (bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]):
                    nodes[o.id] = (x, y)
            continue
        tags = {t.k: t.v for t in o.tags}
        if tags.get("railway") in ACTIVE_RAIL and tags.get("service") not in ("yard", "siding", "spur"):
            refs = array.array("q", (n.ref for n in o.nodes))
            rail_nodes.update(r for r in refs if r in nodes)
            rail_ways.append(refs)
        elif tags.get("railway") in ACTIVE_RAIL:
            rail_nodes.update(n.ref for n in o.nodes if n.ref in nodes)
        if "highway" in tags:
            sp = school_speed(tags)
            if sp:
                school[o.id] = sp
    # the working lines as a coarse grid: every cell a line passes through
    cells = set()
    if rail_ways:
        want = set()
        for refs in rail_ways:
            want.update(refs)
        loc = {}
        fp = osmium.FileProcessor(pbf_path, osmium.osm.NODE).with_filter(osmium.filter.IdFilter(want))
        for n in fp:
            if n.location.valid():
                loc[n.id] = (round(n.location.lon * 1e5), round(n.location.lat * 1e5))
        want = None
        for refs in rail_ways:
            pts = [loc[r] for r in refs if r in loc]
            for a, b in zip(pts, pts[1:]):
                steps = max(1, int(max(abs(b[0] - a[0]), abs(b[1] - a[1])) // (RAIL_CELL // 2)))
                for k in range(steps + 1):
                    cells.add(((a[0] + (b[0] - a[0]) * k // steps) // RAIL_CELL,
                               (a[1] + (b[1] - a[1]) * k // steps) // RAIL_CELL))
    return {"crossings": [(n, x, y) for n, (x, y) in nodes.items() if n in rail_nodes], "school": school,
            "rail_cells": cells}


def near_rail(cells, x, y):
    """Within about a cell (100-200 m) of a working railway line."""
    cx, cy = x // RAIL_CELL, y // RAIL_CELL
    return any((cx + i, cy + j) in cells for i in (-1, 0, 1) for j in (-1, 0, 1))


# ---- geometry --------------------------------------------------------------------

def _m(a, b):
    k = math.cos(math.radians(a[1] / 1e5))
    return math.hypot((a[0] - b[0]) * k, a[1] - b[1]) * cameras.M_PER_UNIT


class _Grid:
    """Points by ~1 km cell, for 'anything within r metres' tests."""

    def __init__(self, cell=1000):
        self.cell, self.g = cell, collections.defaultdict(list)

    def add(self, x, y, v=None):
        self.g[(x // self.cell, y // self.cell)].append((x, y, v))

    def near(self, x, y, r):
        cx, cy = x // self.cell, y // self.cell
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for p in self.g.get((gx, gy), ()):
                    if _m((x, y), p) <= r:
                        yield p


class _Dedup:
    def __init__(self, metres=DEDUP_M):
        self.grid, self.metres = _Grid(), metres

    def seen(self, x, y, h):
        for _, _, oh in self.grid.near(x, y, self.metres):
            if h is None or oh is None or cameras.turn(h, oh) < 30:
                return True
        self.grid.add(x, y, h)
        return False


def _heads_at(tags, xy, p):
    """Travel headings arriving at vertex p of a way: from p-1 (forward) and p+1 (backward)."""
    ow = osm_import.oneway_dir(tags)
    n = len(xy) // 2
    out = []
    if p > 0 and ow != -1:
        out.append(cameras.bearing((xy[2 * p - 2], xy[2 * p - 1]), (xy[2 * p], xy[2 * p + 1])))
    if p < n - 1 and ow != 1:
        out.append(cameras.bearing((xy[2 * p + 2], xy[2 * p + 3]), (xy[2 * p], xy[2 * p + 1])))
    return out


# ---- level crossings -------------------------------------------------------------

def _crossings(ways, osm_by_country, gov, st_idx, bbox, stats):
    wanted = [(x, y) for cr in osm_by_country.values() for _, x, y in cr["crossings"]]
    for d in gov:
        wanted += [(round(c["lon"] * 1e5), round(c["lat"] * 1e5)) for c in d["items"]]
    idx = cameras.RoadIndex(ways, wanted)
    out, dedup, found = [], _Dedup(), _Grid()
    rail_cells = None
    for cr in osm_by_country.values():
        if "rail_cells" in cr:
            rail_cells = (rail_cells or set()) | cr["rail_cells"]
    counts = collections.Counter()
    for cc, cr in osm_by_country.items():
        for nid, x, y in cr["crossings"]:
            on = idx.on_way.get(nid)
            if not on:
                stats["railway: OSM crossing not on a drivable road (path, cycleway, service road)"] += 1
                continue
            wi, p = on[0]
            tags, pts = idx.ways[wi]
            st = "NZ" if cc == "NZ" else cameras.state_of(st_idx, x, y)
            found.add(x, y)
            for h in _heads_at(tags, pts.xy, p) or [None]:
                if not dedup.seen(x, y, h):
                    out.append({"cat": "railway", "x": x, "y": y, "heading": h, "speed": None,
                                "road": tags.get("name"), "state": st})
            counts[st or "?"] += 1
    gov_added = collections.Counter()
    for d in gov:
        for c in d["items"]:
            x, y = round(c["lon"] * 1e5), round(c["lat"] * 1e5)
            if bbox and not (bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]):
                continue
            if next(found.near(x, y, MATCH_M), None) is not None:
                stats[f"railway: {d['state']} list, already in OSM"] += 1
                continue
            if rail_cells is not None and not near_rail(rail_cells, x, y):
                stats[f"railway: {d['state']} list, not on a working railway (closed line)"] += 1
                continue
            snapped = cameras._snap(idx, {"x": x, "y": y, "dir": None, "road": c.get("road"), "maxspeed": None},
                                    SNAP_GOV)
            if not snapped or snapped[0][2] is None:
                stats[f"railway: {d['state']} list, no road within {SNAP_GOV:.0f} m"] += 1
                continue
            found.add(x, y)
            for sx, sy, h, _, road in snapped:
                if not dedup.seen(sx, sy, h):
                    out.append({"cat": "railway", "x": sx, "y": sy, "heading": h, "speed": None,
                                "road": road or c.get("road"), "state": d["state"]})
            gov_added[d["state"]] += 1
            counts[d["state"]] += 1
    for s, n in gov_added.items():
        stats[f"railway: {s} list, added (OSM didn't have them)"] = n
    return out, counts


# ---- school zones ----------------------------------------------------------------

def _crossings_along(a, b, rings):
    """Parameters t (0..1) where segment a-b crosses the rings' edges, in order."""
    ts = []
    dx, dy = b[0] - a[0], b[1] - a[1]
    for ring in rings:
        for (px, py), (qx, qy) in zip(ring, ring[1:]):
            ex, ey = qx - px, qy - py
            den = dx * ey - dy * ex
            if den == 0:
                continue
            t = ((px - a[0]) * ey - (py - a[1]) * ex) / den
            u = ((px - a[0]) * dy - (py - a[1]) * dx) / den
            if 0 <= t <= 1 and 0 <= u < 1:
                ts.append(t)
    return sorted(ts)


def _zone_entries_polygon(idx, area, rings_xy):
    """Where mapped roads cross into a zone polygon: [(x, y, heading, road name)]. A
    segment can cross the boundary several times (in one side and out the other)."""
    out, seen = [], set()
    bx0, by0, bx1, by1 = area.bbox
    for ring in rings_xy:
        for x, y in ring:
            cx, cy = x // cameras.CELL, y // cameras.CELL
            for gx in (cx - 1, cx, cx + 1):
                for gy in (cy - 1, cy, cy + 1):
                    for wi, s in idx.grid.get((gx, gy), ()):
                        if (wi, s) in seen:
                            continue
                        seen.add((wi, s))
                        tags, pts = idx.ways[wi]
                        xy = pts.xy
                        a, b = (xy[2 * s], xy[2 * s + 1]), (xy[2 * s + 2], xy[2 * s + 3])
                        if max(a[0], b[0]) < bx0 or min(a[0], b[0]) > bx1 or max(a[1], b[1]) < by0 \
                                or min(a[1], b[1]) > by1:
                            continue
                        ts = _crossings_along(a, b, rings_xy)
                        if not ts:
                            continue
                        heads = idx.directions(wi, s)
                        fwd, back = cameras.bearing(a, b), cameras.bearing(b, a)
                        ok_f = any(cameras.turn(fwd, d) < 1 for d in heads)
                        ok_b = any(cameras.turn(back, d) < 1 for d in heads)
                        inside = area.contains(*a)
                        for t in ts:
                            inside = not inside
                            p = (round(a[0] + (b[0] - a[0]) * t), round(a[1] + (b[1] - a[1]) * t))
                            if inside and ok_f:          # going a -> b, into the zone here
                                out.append((p[0], p[1], fwd, tags.get("name")))
                            elif not inside and ok_b:    # going b -> a, into the zone here
                                out.append((p[0], p[1], back, tags.get("name")))
    return out


def _gov_school_zones(ways, d, bbox, stats):
    from .osm_names import Area
    zones = []
    for z in d["items"]:
        rings = [[(round(p[0] * 1e5), round(p[1] * 1e5)) for p in r] for r in z["rings"]]
        a = Area(9, z.get("name") or "", None, [(r, []) for r in rings], z.get("id"))
        b = a.bbox
        if bbox and (b[2] < bbox[0] or b[0] > bbox[2] or b[3] < bbox[1] or b[1] > bbox[3]):
            continue
        zones.append((z, a, rings))
    pts = [p for _, _, rings in zones for r in rings for p in r[::2]]
    pts += [a.centroid() for _, a, _ in zones]
    idx = cameras.RoadIndex(ways, pts)
    out = []
    for z, a, rings in zones:
        entries = _zone_entries_polygon(idx, a, rings)
        if not entries:
            stats[f"school zones: {d['state']} zone with no mapped road into it"] += 1
            continue
        cx, cy = a.centroid()
        near = idx.near(cx, cy, 300)
        if near:
            _, wi, _, qx, qy = near[0]
            cx, cy, road = round(qx), round(qy), idx.ways[wi][0].get("name")
        else:
            road = entries[0][3]
        out.append(({"x": cx, "y": cy, "speed": z["speed"], "road": road, "state": d["state"]},
                    [(x, y, h, rd) for x, y, h, rd in entries]))
    return out


def _osm_school_zones(ways, osm_by_country, st_idx, skip_states, stats):
    """Touching school-zone ways -> zones; entries where other mapped roads join them."""
    school = {}
    for cc, r in osm_by_country.items():
        for wid, sp in r["school"].items():
            school[wid] = (sp, cc)
    if not school:
        return []
    zw = [(wid, tags, pts) for wid, tags, pts in ways if wid in school]
    # zones: union-find over shared nodes
    parent = list(range(len(zw)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    first = {}
    for i, (_, _, pts) in enumerate(zw):
        for nid in pts.ids:
            j = first.setdefault(nid, i)
            if j != i:
                parent[find(i)] = find(j)
    zone_nodes = set(first)
    # every mapped road at a zone node
    at = collections.defaultdict(list)          # node id -> [(tags, pts, position, in zone)]
    for wid, tags, pts in ways:
        if tags.get("highway") in cameras.SKIP_ROADS or zone_nodes.isdisjoint(pts.ids):
            continue
        inz = wid in school
        for p, nid in enumerate(pts.ids):
            if nid in zone_nodes:
                at[nid].append((tags, pts, p, inz))
    groups = collections.defaultdict(list)
    for i in range(len(zw)):
        groups[find(i)].append(i)
    out = []
    for members in groups.values():
        wid0, tags0, pts0 = max((zw[i] for i in members), key=lambda w: len(w[2].ids))
        sp, cc = school[wid0]
        n0 = len(pts0.ids) // 2
        cx, cy = pts0.xy[2 * n0], pts0.xy[2 * n0 + 1]
        st = "NZ" if cc == "NZ" else cameras.state_of(st_idx, cx, cy)
        if st in skip_states:
            stats[f"school zones: OSM zone in {st} (official list used)"] += 1
            continue
        entries = []
        for i in members:
            for nid in zw[i][2].ids:
                for tags, pts, p, inz in at.get(nid, ()):
                    if inz:
                        continue
                    x, y = pts.xy[2 * p], pts.xy[2 * p + 1]
                    entries += [(x, y, h, tags.get("name")) for h in _heads_at(tags, pts.xy, p)]
        if not entries:
            stats["school zones: OSM zone with no road into it"] += 1
            continue
        out.append(({"x": cx, "y": cy, "speed": sp, "road": tags0.get("name"), "state": st}, entries))
    return out


# ---- choose and place ------------------------------------------------------------

def place(ways, osm_by_country, gov_files, st_idx, countries_built, bbox=None):
    """-> (alerts, report). alerts: [dict(cat, x, y, heading, speed, road, state)] with cat
    "school_zone" / "school_point" / "railway". osm_by_country: {code: read_osm() result};
    gov_files: the lists from fetch(). report: per state, source and counts."""
    stats = collections.Counter()
    gov = [load(p) for p in gov_files]
    gov_cross = [d for d in gov if d["kind"] == "crossings"]
    gov_zones = [d for d in gov if d["kind"] == "school_zones"]
    rail, rail_counts = _crossings(ways, osm_by_country, gov_cross, st_idx, bbox, stats)
    zones = []
    for d in gov_zones:
        zones += _gov_school_zones(ways, d, bbox, stats)
    zones += _osm_school_zones(ways, osm_by_country, st_idx, {d["state"] for d in gov_zones}, stats)
    out = list(rail)
    zone_counts, point_counts = collections.Counter(), collections.Counter()
    for z, entries in zones:
        st = z["state"] or "?"
        out.append({"cat": "school_zone", "x": z["x"], "y": z["y"], "heading": None, "speed": z["speed"],
                    "road": z["road"], "state": z["state"]})
        zone_counts[st] += 1
        dedup = _Dedup()
        for x, y, h, road in entries:
            if dedup.seen(x, y, h):
                continue
            out.append({"cat": "school_point", "x": x, "y": y, "heading": h, "speed": z["speed"],
                        "road": road, "state": z["state"]})
            point_counts[st] += 1
    for a in out:
        if a["heading"] is not None:
            a["heading"] = round(a["heading"]) % 360
    codes = [s for s in cameras.AU_STATES if "AU" in countries_built] + (["NZ"] if "NZ" in countries_built else [])
    gz = {d["state"]: d for d in gov_zones}
    gc = {d["state"]: d for d in gov_cross}
    states = []
    for s in codes:
        states.append({"code": s, "name": cameras.NAMES[s],
                       "school_zones": {"source": "gov" if s in gz else "osm",
                                        "provider": gz[s]["provider"] if s in gz else "OpenStreetMap contributors (ODbL)",
                                        "fetched": gz[s]["fetched"] if s in gz else None,
                                        "zones": zone_counts.get(s, 0), "points": point_counts.get(s, 0)},
                       "crossings": {"source": "osm+gov" if s in gc else "osm",
                                     "provider": ("OpenStreetMap contributors (ODbL) + " + gc[s]["provider"])
                                     if s in gc else "OpenStreetMap contributors (ODbL)",
                                     "fetched": gc[s]["fetched"] if s in gc else None,
                                     "crossings": rail_counts.get(s, 0)}})
    return out, {"states": states, "stats": dict(stats)}
