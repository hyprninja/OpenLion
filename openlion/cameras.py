"""Fixed speed and red-light cameras: government lists first, OpenStreetMap elsewhere.

The unit warns about cameras from the POI database's "camera" category (report 23): one
POI per enforced direction, with Poi_AlertAttributes SpeedLimit, Direction (the travel
heading in degrees from north) and CameraType. This module decides which cameras go on
the card and works out their heading and speed limit; osm_poi.build writes them.

Sources, per state (per country for New Zealand):
  - a government list (gov_cameras.py) where one is published openly: that state's OSM
    cameras are dropped, since the official list is complete and current
  - otherwise OSM highway=speed_camera nodes and type=enforcement relations

Heading: each camera is snapped onto the nearest drivable road. On a one-way carriageway
(divided roads, tunnels) that gives the direction by itself. A camera with a known
direction (VIC ID suffix, "Northbound", OSM direction=*, an enforcement relation's
from/to) picks the carriageway going that way. A two-way road with no known direction
gets one POI each way. Speed limit: the road's OSM maxspeed, else the camera's own.
"""
import collections
import math
import re

from . import gov_cameras, osm_import

STATES = {"New South Wales": "NSW", "Victoria": "VIC", "Queensland": "QLD", "South Australia": "SA",
          "Western Australia": "WA", "Tasmania": "TAS", "Northern Territory": "NT",
          "Australian Capital Territory": "ACT", "Jervis Bay Territory": "ACT"}
AU_STATES = ("NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT")
NAMES = {"NSW": "New South Wales", "VIC": "Victoria", "QLD": "Queensland", "SA": "South Australia",
         "WA": "Western Australia", "TAS": "Tasmania", "NT": "Northern Territory",
         "ACT": "Australian Capital Territory", "NZ": "New Zealand"}

# factory CameraType (report 23): 1 fixed speed (freeways, 100-110 km/h; point-to-point
# sites too), 2 and 0 intersections; read as red light + speed and red light only
CAMERA_TYPE = {"speed": 1, "p2p": 1, "redlight_speed": 2, "redlight": 0}

M_PER_UNIT = 1.11195          # metres per 1e-5 degree of latitude
CELL = 100                    # snapping grid: 0.001 degree (~90-110 m)
SNAP_GOV = 60.0               # metres: official points may sit at the kerb or the junction centre
SNAP_OSM = 35.0
MAX_TURN = 60.0               # a wanted heading must be this close to the road's direction
DEDUP_M = 30.0
SKIP_ROADS = {"service", "track", "busway"}
COMPASS = {"N": 0, "NNE": 22, "NE": 45, "ENE": 67, "E": 90, "ESE": 112, "SE": 135, "SSE": 157, "S": 180,
           "SSW": 202, "SW": 225, "WSW": 247, "W": 270, "WNW": 292, "NW": 315, "NNW": 337}


# ---- OSM read --------------------------------------------------------------------

def _osm_direction(v):
    """OSM direction=* -> degrees, "forward" / "backward", or None."""
    if not v:
        return None
    v = v.strip().split(";")[0].strip()
    if v.lower() in ("forward", "backward"):
        return v.lower()
    if v.upper() in COMPASS:
        return COMPASS[v.upper()]
    try:
        return float(v) % 360
    except ValueError:
        return None


def read_osm(pbf_path, bbox=None):
    """OSM cameras: [dict(id, x, y, kind, dir, maxspeed)] with x/y in degrees * 1e5.
    dir: degrees, "forward" / "backward" (along the way the node is on) or None."""
    import osmium
    import osmium.filter
    # 1. enforcement relations: which device, what it enforces, from/to points
    rels = []
    fp = (osmium.FileProcessor(pbf_path, osmium.osm.RELATION)
          .with_filter(osmium.filter.KeyFilter("enforcement")))
    for r in fp:
        tags = {t.k: t.v for t in r.tags}
        if tags.get("type") != "enforcement":
            continue
        enf = tags.get("enforcement", "")
        kind = ("redlight" if enf == "traffic_signals" else "p2p" if enf in ("average_speed", "section_control")
                else "speed" if enf in ("maxspeed", "mindistance") else None)
        if kind is None:
            continue
        mem = collections.defaultdict(list)
        for m in r.members:
            if m.type == "n":
                mem[m.role].append(m.ref)
        if mem.get("device"):
            rels.append((kind, mem["device"], (mem.get("from") or [None])[0], (mem.get("to") or [None])[0],
                         tags.get("maxspeed")))
    # 2. camera nodes and every node the relations name
    want = {n for _, dev, f, t, _ in rels for n in dev + [f, t] if n}
    loc, cams = {}, {}
    fp = (osmium.FileProcessor(pbf_path, osmium.osm.NODE)
          .with_filter(osmium.filter.KeyFilter("highway")))
    for n in fp:
        if n.tags.get("highway") == "speed_camera" and n.location.valid():
            tags = {t.k: t.v for t in n.tags}
            cams[n.id] = {"id": n.id, "x": round(n.location.lon * 1e5), "y": round(n.location.lat * 1e5),
                          "kind": "speed",
                          "dir": _osm_direction(tags.get("direction") or tags.get("camera:direction")),
                          "maxspeed": osm_import.parse_speed(tags.get("maxspeed"))}
    rest = want - set(cams)
    if rest:
        fp = osmium.FileProcessor(pbf_path, osmium.osm.NODE).with_filter(osmium.filter.IdFilter(rest))
        for n in fp:
            if n.location.valid():
                loc[n.id] = (round(n.location.lon * 1e5), round(n.location.lat * 1e5))
    for c in cams.values():
        loc[c["id"]] = (c["x"], c["y"])
    for kind, devs, f, t, ms in rels:
        for d in devs:
            if d not in loc:
                continue
            c = cams.get(d)
            if c is None:      # e.g. a red-light camera mapped as the signals node
                c = cams[d] = {"id": d, "x": loc[d][0], "y": loc[d][1], "kind": kind, "dir": None,
                               "maxspeed": None}
            elif kind != "speed":
                c["kind"] = kind
            a = loc.get(f) or loc.get(d)
            b = loc.get(t) if t else None
            if c["dir"] is None and a and b and a != b:
                c["dir"] = bearing(a, b)
            c["maxspeed"] = c["maxspeed"] or osm_import.parse_speed(ms)
    out = list(cams.values())
    if bbox:
        out = [c for c in out if bbox[0] <= c["x"] <= bbox[2] and bbox[1] <= c["y"] <= bbox[3]]
    return out


# ---- geometry --------------------------------------------------------------------

def bearing(a, b):
    """Compass bearing from a to b (x/y in degrees * 1e5)."""
    dx = (b[0] - a[0]) * math.cos(math.radians(a[1] / 1e5))
    dy = b[1] - a[1]
    return math.degrees(math.atan2(dx, dy)) % 360


def turn(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def _project(px, py, ax, ay, bx, by, k):
    """Closest point on segment ab to p, and its distance in metres (k = cos(lat))."""
    dx, dy = (bx - ax) * k, by - ay
    ll = dx * dx + dy * dy
    t = 0.0 if ll == 0 else max(0.0, min(1.0, ((px - ax) * k * dx + (py - ay) * dy) / ll))
    qx, qy = ax + (bx - ax) * t, ay + (by - ay) * t
    return qx, qy, math.hypot((px - qx) * k, py - qy) * M_PER_UNIT


def _norm(s):
    s = (s or "").lower()
    s = re.sub(r"\b(road|rd|street|st|avenue|ave|drive|dr|highway|hwy|freeway|fwy|parade|pde|"
               r"terrace|tce|boulevard|bvd|way|lane|crescent|cres|place|pl|court|ct|the)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


class RoadIndex:
    """Road segments near the cameras, for snapping."""

    def __init__(self, ways, points):
        need = set()
        for x, y in points:
            cx, cy = x // CELL, y // CELL
            need.update((cx + i, cy + j) for i in (-1, 0, 1) for j in (-1, 0, 1))
        coarse = {(cx // 10, cy // 10) for cx, cy in need}
        self.grid = collections.defaultdict(list)
        self.ways = []          # (tags, WayPoints)
        self.on_way = {}        # node id -> [(way index, position)]
        for wid, tags, pts in ways:
            hw = tags.get("highway")
            if hw in SKIP_ROADS:
                continue
            xy = pts.xy
            xs, ys = xy[0::2], xy[1::2]
            x0, x1, y0, y1 = min(xs) // CELL, max(xs) // CELL, min(ys) // CELL, max(ys) // CELL
            if not any((gx, gy) in coarse for gx in range(x0 // 10, x1 // 10 + 1)
                       for gy in range(y0 // 10, y1 // 10 + 1)):
                continue
            wi = None
            for k in range(len(xs) - 1):
                sx0, sx1 = sorted((xs[k] // CELL, xs[k + 1] // CELL))
                sy0, sy1 = sorted((ys[k] // CELL, ys[k + 1] // CELL))
                cells = [(gx, gy) for gx in range(sx0, sx1 + 1) for gy in range(sy0, sy1 + 1) if (gx, gy) in need]
                if cells:
                    if wi is None:
                        wi = len(self.ways)
                        self.ways.append((tags, pts))
                    for c in cells:
                        self.grid[c].append((wi, k))
            if wi is not None:
                for k, nid in enumerate(pts.ids):
                    self.on_way.setdefault(nid, []).append((wi, k))

    def near(self, x, y, radius):
        """[(metres, way index, segment, qx, qy)] within radius, nearest first."""
        k = math.cos(math.radians(y / 1e5))
        seen, out = set(), []
        cx, cy = x // CELL, y // CELL
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for wi, s in self.grid.get((gx, gy), ()):
                    if (wi, s) in seen:
                        continue
                    seen.add((wi, s))
                    xy = self.ways[wi][1].xy
                    qx, qy, d = _project(x, y, xy[2 * s], xy[2 * s + 1], xy[2 * s + 2], xy[2 * s + 3], k)
                    if d <= radius:
                        out.append((d, wi, s, qx, qy))
        out.sort()
        return out

    def directions(self, wi, s):
        """Travel bearings allowed on segment s of way wi."""
        tags, pts = self.ways[wi]
        xy = pts.xy
        b = bearing((xy[2 * s], xy[2 * s + 1]), (xy[2 * s + 2], xy[2 * s + 3]))
        ow = osm_import.oneway_dir(tags)
        return [b] if ow == 1 else [(b + 180) % 360] if ow == -1 else [b, (b + 180) % 360]


def _snap(idx, cam, radius):
    """[(x, y, heading, speed, road name)] for one camera: one per enforced direction."""
    x, y, want, hint = cam["x"], cam["y"], cam.get("dir"), _norm(cam.get("road"))
    cands = idx.near(x, y, radius)
    if not cands and cam.get("osm_node") is None:
        return [(x, y, want if isinstance(want, (int, float)) else None, cam.get("maxspeed"), cam.get("road"))]

    def score(c):
        d, wi, s = c[0], c[1], c[2]
        tags = idx.ways[wi][0]
        pen = 25.0 if tags.get("highway", "").endswith("_link") else 0.0
        if hint:
            name = _norm(tags.get("name"))
            ref = _norm(tags.get("ref"))
            pen -= 30.0 if (name and (hint in name or name in hint)) or (ref and ref in hint) else 0.0
        return d + pen

    def result(c, heads):
        d, wi, s, qx, qy = c
        tags = idx.ways[wi][0]
        sp = osm_import.parse_speed(tags.get("maxspeed")) or cam.get("maxspeed")
        return [(round(qx), round(qy), h, sp, tags.get("name") or cam.get("road")) for h in heads]

    # an OSM camera node on a way: that way, at that node
    on = idx.on_way.get(cam.get("osm_node")) if cam.get("osm_node") is not None else None
    if on:
        wi, k = on[0]
        n = len(idx.ways[wi][1])
        s = min(k, n - 2)
        c = (0.0, wi, s, x, y)
        heads = idx.directions(wi, s)
        if want in ("forward", "backward"):
            xy = idx.ways[wi][1].xy
            b = bearing((xy[2 * s], xy[2 * s + 1]), (xy[2 * s + 2], xy[2 * s + 3]))
            return result(c, [b if want == "forward" else (b + 180) % 360])
        if isinstance(want, (int, float)):
            fit = [h for h in heads if turn(h, want) <= MAX_TURN]
            return result(c, fit or [want])
        return result(c, heads)
    if not cands:
        return [(x, y, want if isinstance(want, (int, float)) else None, cam.get("maxspeed"), cam.get("road"))]
    if isinstance(want, (int, float)):
        best = None
        for c in cands:
            for h in idx.directions(c[1], c[2]):
                t = turn(h, want)
                if t <= MAX_TURN:
                    v = score(c) + t * 0.2
                    if best is None or v < best[0]:
                        best = (v, c, h)
        if best:
            return result(best[1], [best[2]])
        return result(min(cands, key=score), [want])
    c = min(cands, key=score)
    return result(c, idx.directions(c[1], c[2]))


# ---- choose and place ------------------------------------------------------------

def state_of(st_idx, x, y):
    i = st_idx.find(x, y) if st_idx else None
    return STATES.get(st_idx.areas[i].name) if i is not None else None


def place(ways, osm_by_country, gov_files, st_idx, countries_built, bbox=None):
    """-> (cameras, report). cameras: [dict(x, y, heading, speed, type, road, state)].
    osm_by_country: {country code: read_osm() result}; gov_files: gov_cameras state files.
    bbox: the map's extent (deg * 1e5); official cameras outside it are left out.
    report: {"states": [{code, name, source "gov"/"osm", provider, fetched, cameras}]}."""
    gov = {}
    for p in gov_files:
        d = gov_cameras.load(p)
        gov[d["state"]] = d
    wanted = []           # (state, source rank, camera dict for _snap, kind)
    for code, d in gov.items():
        for c in d["cameras"]:
            x, y = round(c["lon"] * 1e5), round(c["lat"] * 1e5)
            if bbox and not (bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]):
                continue
            wanted.append((code, 0, {"x": x, "y": y, "dir": c.get("heading"),
                                     "road": c.get("road"), "maxspeed": None}, c["kind"], SNAP_GOV))
    dropped = collections.Counter()
    for cc, cams in osm_by_country.items():
        for c in cams:
            st = "NZ" if cc == "NZ" else state_of(st_idx, c["x"], c["y"])
            if st is None:
                dropped["outside any state"] += 1
                continue
            if st in gov:
                dropped[st] += 1
                continue
            wanted.append((st, 1, {"x": c["x"], "y": c["y"], "dir": c["dir"], "road": None,
                                   "maxspeed": c["maxspeed"], "osm_node": c["id"]}, c["kind"], SNAP_OSM))
    idx = RoadIndex(ways, [(w[2]["x"], w[2]["y"]) for w in wanted])
    out, kept = [], collections.defaultdict(list)
    counts = collections.Counter()
    for st, rank, cam, kind, radius in sorted(wanted, key=lambda w: w[1]):
        for x, y, h, sp, road in _snap(idx, cam, radius):
            k = math.cos(math.radians(y / 1e5))
            dup = any(math.hypot((x - ox) * k, y - oy) * M_PER_UNIT < DEDUP_M and
                      (h is None or oh is None or turn(h, oh) < 30) for ox, oy, oh in kept[(x // CELL, y // CELL)])
            if dup:
                continue
            for gx in (-1, 0, 1):
                for gy in (-1, 0, 1):
                    kept[(x // CELL + gx, y // CELL + gy)].append((x, y, h))
            out.append({"x": x, "y": y, "heading": None if h is None else round(h) % 360, "speed": sp,
                        "type": CAMERA_TYPE[kind], "road": road, "state": st})
            counts[st] += 1
    states = []
    codes = [s for s in AU_STATES if "AU" in countries_built] + (["NZ"] if "NZ" in countries_built else [])
    for s in codes:
        g = gov.get(s)
        states.append({"code": s, "name": NAMES[s], "source": "gov" if g else "osm",
                       "provider": g["provider"] if g else "OpenStreetMap contributors (ODbL)",
                       "fetched": g["fetched"] if g else None, "cameras": counts.get(s, 0)})
    return out, {"states": states, "osm_dropped": dict(dropped)}
