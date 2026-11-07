"""Speed limits from government open data, for roads where OSM has no maxspeed.

Victoria (Department of Transport and Planning, "Speed Zones", CC BY 4.0, monthly):
every road in the state as GeoJSON lines with speed_limit, zone_conditions and a
compass direction. Most roads come as two features with the same geometry, one per
travel direction; they disagree on speed in about 0.5% of pairs. The card stores one
limit per segment, so a line keeps the lower of its directions.

Zones that only apply some of the time (school, shopping strip, seasonal, side-road
activated) are left out: the dataset records their lowest value, which would be wrong
most of the day. Such roads keep whatever OSM says, or no limit.

New South Wales (Transport for NSW, "Speed Zones", CC BY, updated as zones change): every
road as GeoJSON lines with Type, Status, Direction and Speed ("60 km/h"). "Default" sections
carry the default limit Transport for NSW applies to that road (50 or 100), so unlike WA's
they're a definite value. School, School Bus, Variable, Wet Weather and Toll Plaza zones are
part-time or conditional and left out.

ACT (Transport Canberra and City Services, "Road Network Speed Zones", CC BY 4.0, a
yearly static view; "indicative only"): every road as lines, one per direction, with
SPEED_ZONE ("50", or "SCHOOL ZONE", which is left out).

Tasmania (Department of State Growth, "Speed Limits (on State Highways and Subsidiary
Roads)", CC BY 3.0 AU, reviewed at least yearly): only the state road network (~4,000 km), as
lines with PD and CD, the limit in the line's own direction and the other way (0 or empty for
one carriageway of a divided road); the lower is kept. Part-time limits (school and variable
zones) aren't in the data.

Western Australia (Main Roads WA, "Legal Speed Limits", CC BY 4.0, refreshed weekly):
every public road as lines with SPEED_LIMIT ("60km/h") and CWY (Single, or Left/Right
for each side of a divided road). About three quarters of the sections only say "50km/h
applies in built up areas or 110km/h outside built up areas": that's the default rule,
not a signed limit, and the data doesn't say which half applies, so those are left out.
Part-time zones (school, children's crossing) are a separate layer and aren't used.

New Zealand (NZTA, National Speed Limit Register, CC BY 4.0): the legal register itself,
as polygons around road corridors (typically ~23 m wide) on top of large "Default Area"
polygons (all 100 km/h). The smallest zone holding a point wins. The register keeps its
history, so only zones in force now are fetched, and only permanent ones. Zones are
areas, not lines, so they get their own file type and AreaIndex.

Queensland publishes speed signs (points), not zones: see gov_signs.py. Its prepared file
is a third type (SignSet), worked out over the whole road graph rather than per piece.

prepare() turns the downloaded GeoJSON into a small binary file once (deduplicated lines,
integer coordinates); Index loads that file and answers lookup(xy) for a road piece.
"""
import array
import json
import math
import os
import re
import struct

MAGIC = b"OLGSPD1\0"
MAGIC_AREAS = b"OLGSPA1\0"
SOURCES = {"VIC": "Victoria Department of Transport and Planning, Speed Zones (CC BY 4.0)",
           "WA": "Main Roads Western Australia, Legal Speed Limits (CC BY 4.0)",
           "NZ": "NZ Transport Agency Waka Kotahi, National Speed Limit Register (CC BY 4.0)",
           "NSW": "Transport for NSW, Speed Zones (CC BY)",
           "TAS": "Tasmania Department of State Growth, Speed Limits on State Highways and Subsidiary Roads (CC BY 3.0 AU)",
           "QLD": "Queensland Department of Transport and Main Roads, Speed Limits for state and local roads (CC BY 4.0)",
           "ACT": "ACT Government (Transport Canberra and City Services), Road Network Speed Zones (CC BY 4.0)"}
NORMAL = "Normal Operation"
# NSW zone types that always apply. Left out: School, School Bus, Variable, Wet Weather and
# Toll Plaza (part-time or conditional), and Truck & bus (not for cars).
NSW_KEEP = {"Ordinary Permanent", "Default", "High Pedestrian", "Shared", "Local Traffic"}
WA_SIGNED = re.compile(r"^\s*(\d{1,3})\s*km/h\s*$", re.IGNORECASE)

CELL = 7            # grid cell = 2**7 units of 1e-5 degree (~140 m x 110 m in Victoria)
RADIUS_M = 12.0     # a sample must be this close to a zone line
MAX_TURN = 30.0     # and run along it (axis difference in degrees)
M_PER_UNIT = 1.11195  # metres per 1e-5 degree of latitude
SIMPLIFY = 2        # units (~2 m): zone lines are densely digitised


def simplify(xy, tol):
    """Douglas-Peucker on a flat x/y array; keeps the ends."""
    n = len(xy) // 2
    if n <= 2:
        return xy
    keep = bytearray(n)
    keep[0] = keep[-1] = 1
    stack = [(0, n - 1)]
    t2 = tol * tol
    while stack:
        a, b = stack.pop()
        ax, ay, bx, by = xy[2 * a], xy[2 * a + 1], xy[2 * b], xy[2 * b + 1]
        dx, dy = bx - ax, by - ay
        ll = dx * dx + dy * dy
        far, far_d = -1, t2
        for k in range(a + 1, b):
            px, py = xy[2 * k] - ax, xy[2 * k + 1] - ay
            if ll:
                c = px * dy - py * dx
                d = c * c / ll
            else:
                d = px * px + py * py
            if d > far_d:
                far, far_d = k, d
        if far >= 0:
            keep[far] = 1
            stack += [(a, far), (far, b)]
    return array.array("i", (v for k in range(n) if keep[k] for v in (xy[2 * k], xy[2 * k + 1])))


def _key(cx, cy):
    return (cx << 18) + cy + (1 << 17)


def _conditions(p):
    zc = p.get("zone_conditions") or []
    return [zc] if isinstance(zc, str) else zc


def feature_speed(p):
    """(km/h, None) for a feature's properties, or (None, reason it's left out).
    Knows the fields of Victoria (speed_limit, zone_conditions), NSW, the ACT, NZ, Tasmania and WA."""
    if "sz_type" in p or ("Type" in p and "Speed" in p):   # New South Wales
        kind = p.get("sz_type") or p.get("Type")
        if kind not in NSW_KEEP:
            return None, "skipped (part-time or conditional zone)"
        if (p.get("sz_status") or p.get("Status")) not in (None, "Existing"):
            return None, "skipped (not existing)"
        m = WA_SIGNED.match(p.get("sz_speed") or p.get("Speed") or "")
        if not m:
            return None, "skipped (no speed)"
        speed = int(m.group(1))
        return (speed, None) if 5 <= speed <= 130 else (None, "skipped (no speed)")
    if "SPEED_ZONE" in p and "CARRIAGEWAY" in p:   # Australian Capital Territory
        v = (p.get("SPEED_ZONE") or "").strip()
        if not v.isdigit():
            return None, "skipped (part-time or conditional zone)" if "SCHOOL" in v.upper() else "skipped (no speed)"
        speed = int(v)
        return (speed, None) if 5 <= speed <= 130 else (None, "skipped (no speed)")
    if "PD" in p and "CD" in p and "CWAY_CODE" in p:  # Tasmania: each direction, keep the lower
        both = [v for v in (p.get("PD"), p.get("CD")) if isinstance(v, (int, float)) and v > 0]
        if not both:
            return None, "skipped (no speed)"
        speed = int(min(both))
        return (speed, None) if 5 <= speed <= 130 else (None, "skipped (no speed)")
    if "speedLimitZoneValue" in p:                  # New Zealand
        if p.get("speedCategoryName") not in (None, "Permanent"):
            return None, "skipped (not permanent)"
        try:
            speed = int(float(p.get("speedLimitZoneValue")))
        except (TypeError, ValueError):
            return None, "skipped (no speed)"
        return (speed, None) if 5 <= speed <= 130 else (None, "skipped (no speed)")
    if "SPEED_LIMIT" in p:                          # Western Australia
        v = p.get("SPEED_LIMIT") or ""
        m = WA_SIGNED.match(v)
        if not m:
            return None, "skipped (default rule, no signed limit)" if "built up" in v.lower() else "skipped (no speed)"
        speed = int(m.group(1))
    else:                                           # Victoria
        if NORMAL not in _conditions(p) or p.get("zone_conditions_other"):
            return None, "skipped (not normal operation)"
        try:
            speed = int(float(p.get("speed_limit")))
        except (TypeError, ValueError):
            return None, "skipped (no speed)"
    return (speed, None) if 5 <= speed <= 130 else (None, "skipped (no speed)")


def _features(path):
    """Yield features from a FeatureCollection one at a time (the file is read as one
    string, but only one feature is a Python object at a time)."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    dec = json.JSONDecoder()
    i = text.index("[", text.index('"features"')) + 1
    n = len(text)
    while i < n:
        while text[i] in " \r\n\t,":
            i += 1
        if text[i] == "]":
            return
        obj, i = dec.raw_decode(text, i)
        yield obj


def prepare(geojson, out):
    """Speed zone GeoJSON (Victoria, WA: lines; NZ: polygons), or Queensland's speed sign CSV,
    -> compact binary. Returns stats."""
    if geojson.lower().endswith(".csv"):
        from openlion import gov_signs
        return gov_signs.prepare_signs(geojson, out)
    first = next(_features(geojson), None)
    if first and (first.get("geometry") or {}).get("type") in ("Polygon", "MultiPolygon"):
        return prepare_areas(geojson, out)
    lines = {}      # geometry key -> [speed, xy array]
    st = {"features": 0, "skipped (no speed)": 0, "skipped (no geometry)": 0, "direction pairs disagree": 0}
    for f in _features(geojson):
        st["features"] += 1
        p, g = f.get("properties") or {}, f.get("geometry")
        if not g or not g.get("coordinates"):
            st["skipped (no geometry)"] += 1
            continue
        speed, why = feature_speed(p)
        if speed is None:
            st[why] = st.get(why, 0) + 1
            continue
        parts = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
        xy = array.array("i")
        for part in parts:
            for lon, lat in part:
                x, y = round(lon * 1e5), round(lat * 1e5)
                if len(xy) < 2 or (xy[-2], xy[-1]) != (x, y):
                    xy.append(x)
                    xy.append(y)
        if len(xy) < 4:
            st["skipped (no geometry)"] += 1
            continue
        xy = simplify(xy, SIMPLIFY)
        # a direction pair shares its geometry, possibly digitised the other way round
        rev = array.array("i", (v for k in range(len(xy) - 2, -1, -2) for v in (xy[k], xy[k + 1])))
        key = min(xy.tobytes(), rev.tobytes())
        have = lines.get(key)
        if have is None:
            lines[key] = [speed, xy]
        else:
            if have[0] != speed:
                st["direction pairs disagree"] += 1
            have[0] = min(have[0], speed)
    speeds = array.array("B")
    offs = array.array("I", [0])
    pts = array.array("i")
    for speed, xy in lines.values():
        speeds.append(speed)
        pts.extend(xy)
        offs.append(len(pts) // 2)
    st["lines"] = len(speeds)
    st["points"] = len(pts) // 2
    tmp = out + ".part"
    with open(tmp, "wb") as fh:
        fh.write(MAGIC + struct.pack("<II", len(speeds), len(pts) // 2))
        speeds.tofile(fh)
        offs.tofile(fh)
        pts.tofile(fh)
    os.replace(tmp, out)
    return st


class Index:
    """Zone lines in a grid; lookup() gives the limit for a road piece, or None."""

    def __init__(self, path):
        with open(path, "rb") as fh:
            if fh.read(8) != MAGIC:
                raise ValueError(f"{path}: not a prepared speed zone file")
            n_lines, n_pts = struct.unpack("<II", fh.read(8))
            self.speeds = array.array("B")
            self.speeds.fromfile(fh, n_lines)
            self.offs = array.array("I")
            self.offs.fromfile(fh, n_lines + 1)
            self.xy = array.array("i")
            self.xy.fromfile(fh, 2 * n_pts)
        xy = self.xy
        self.bbox = (min(xy[0::2]), min(xy[1::2]), max(xy[0::2]), max(xy[1::2]))
        # sub-segment k runs from point k to k+1; seg_line maps it back to its line
        self.seg_speed = array.array("B", bytes(n_pts))
        valid = bytearray(n_pts)        # 1 where point k starts a sub-segment
        for ln in range(n_lines):
            a, b = self.offs[ln], self.offs[ln + 1]
            for k in range(a, b - 1):
                valid[k] = 1
                self.seg_speed[k] = self.speeds[ln]
        cells = {}
        # a query looks only at its own cell, so a sub-segment goes into every cell
        # within PAD of it: its padded bbox when short, padded steps along it when long
        size = 1 << CELL
        pad = 16                    # units; > RADIUS_M even at 45 deg south
        for k in range(n_pts - 1):
            if not valid[k]:
                continue
            x1, y1, x2, y2 = xy[2 * k], xy[2 * k + 1], xy[2 * k + 2], xy[2 * k + 3]
            span = max(abs(x2 - x1), abs(y2 - y1))
            mine = set()
            if span <= size:
                for cx in range((min(x1, x2) - pad) >> CELL, ((max(x1, x2) + pad) >> CELL) + 1):
                    for cy in range((min(y1, y2) - pad) >> CELL, ((max(y1, y2) + pad) >> CELL) + 1):
                        mine.add(_key(cx, cy))
            else:
                steps = int(span // (size // 4)) + 1
                reach = size // 8 + pad
                for s in range(steps + 1):
                    x = x1 + (x2 - x1) * s // steps
                    y = y1 + (y2 - y1) * s // steps
                    for cx in range((x - reach) >> CELL, ((x + reach) >> CELL) + 1):
                        for cy in range((y - reach) >> CELL, ((y + reach) >> CELL) + 1):
                            mine.add(_key(cx, cy))
            for c in mine:
                lst = cells.get(c)
                if lst is None:
                    cells[c] = array.array("I", [k])
                else:
                    lst.append(k)
        self.cells = cells

    def covers(self, xy):
        b = self.bbox
        return any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for x, y in (xy[0], xy[-1]))

    def _sample(self, x, y, ux, uy, kx):
        """Speed of the nearest zone line running along (ux, uy) near (x, y), or None."""
        cand = self.cells.get(_key(x >> CELL, y >> CELL))
        if cand is None:
            return None
        xy = self.xy
        cos_max = math.cos(math.radians(MAX_TURN))
        best, best_d = None, RADIUS_M * RADIUS_M
        for k in cand:
            x1, y1 = xy[2 * k], xy[2 * k + 1]
            dx, dy = (xy[2 * k + 2] - x1) * kx, (xy[2 * k + 3] - y1) * M_PER_UNIT
            ll = dx * dx + dy * dy
            if ll == 0:
                continue
            px, py = (x - x1) * kx, (y - y1) * M_PER_UNIT
            t = (px * dx + py * dy) / ll
            t = 0.0 if t < 0 else 1.0 if t > 1 else t
            ex, ey = px - t * dx, py - t * dy
            d = ex * ex + ey * ey
            if d >= best_d:
                continue
            if abs(dx * ux + dy * uy) < cos_max * math.sqrt(ll):
                continue
            best, best_d = k, d
        return None if best is None else self.seg_speed[best]

    def lookup(self, xy, highway=None):
        """xy: the piece's points (1e-5 degree units); highway: its OSM highway=* value, if
        known. Returns km/h when most of the piece lies on zone lines that agree, else None."""
        kx = M_PER_UNIT * math.cos(math.radians(xy[0][1] / 1e5))
        legs, total = [], 0.0
        for (x1, y1), (x2, y2) in zip(xy, xy[1:]):
            d = math.hypot((x2 - x1) * kx, (y2 - y1) * M_PER_UNIT)
            if d > 0:
                legs.append((x1, y1, x2, y2, d))
                total += d
        if not legs:
            return None
        # samples away from the ends (cross streets meet there): 3 for a short piece,
        # one per 100 m for a long one (at most 10)
        n = 3 if total <= 300 else min(10, int(total // 100))
        if total < 20:
            n = 1
        want = [total * (i + 1) / (n + 1) for i in range(n)]
        votes = {}
        matched = 0
        acc, li = 0.0, 0
        for w in want:
            while acc + legs[li][4] < w and li < len(legs) - 1:
                acc += legs[li][4]
                li += 1
            x1, y1, x2, y2, d = legs[li]
            t = min(1.0, max(0.0, (w - acc) / d))
            ux, uy = (x2 - x1) * kx / d, (y2 - y1) * M_PER_UNIT / d
            s = self._sample(round(x1 + (x2 - x1) * t), round(y1 + (y2 - y1) * t), ux, uy, kx)
            if s is not None:
                matched += 1
                votes[s] = votes.get(s, 0) + 1
        if not votes:
            return None
        speed, top = max(votes.items(), key=lambda kv: (kv[1], -kv[0]))
        if top >= 0.6 * n and top >= 0.75 * matched:
            return speed
        return None


def _ring_area(xy):
    """Area of a closed ring in square units (shoelace, unsigned)."""
    a = 0
    n = len(xy) // 2
    for k in range(n):
        x1, y1 = xy[2 * k], xy[2 * k + 1]
        x2, y2 = xy[(2 * k + 2) % (2 * n)], xy[(2 * k + 3) % (2 * n)]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2


def prepare_areas(geojson, out):
    """Speed zone polygons (NZ) -> compact binary: per zone its speed, its area (the
    smallest zone holding a point wins) and its rings. Returns stats."""
    speeds, areas, background = array.array("B"), array.array("d"), array.array("B")
    poly_rings, ring_pts = array.array("I", [0]), array.array("I", [0])
    pts = array.array("i")
    st = {"features": 0, "skipped (no speed)": 0, "skipped (no geometry)": 0}
    for f in _features(geojson):
        st["features"] += 1
        p, g = f.get("properties") or {}, f.get("geometry")
        if not g or g.get("type") not in ("Polygon", "MultiPolygon") or not g.get("coordinates"):
            st["skipped (no geometry)"] += 1
            continue
        speed, why = feature_speed(p)
        if speed is None:
            st[why] = st.get(why, 0) + 1
            continue
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        area, rings = 0.0, []
        for poly in polys:
            for k, ring in enumerate(poly):
                xy = array.array("i")
                for lon, lat in ring:
                    x, y = round(lon * 1e5), round(lat * 1e5)
                    if len(xy) < 2 or (xy[-2], xy[-1]) != (x, y):
                        xy.append(x)
                        xy.append(y)
                if len(xy) >= 2 and (xy[0], xy[1]) == (xy[-2], xy[-1]):
                    del xy[-2:]          # store rings open; edges close them
                xy = simplify(xy, SIMPLIFY) if len(xy) > 6 else xy
                if len(xy) < 6:
                    continue
                rings.append(xy)
                area += _ring_area(xy) * (1 if k == 0 else -1)   # holes come off
        if not rings:
            st["skipped (no geometry)"] += 1
            continue
        speeds.append(speed)
        background.append(1 if p.get("speedLimitZoneReasonName") == "Default Area" else 0)
        areas.append(max(area, 1.0))
        for xy in rings:
            pts.extend(xy)
            ring_pts.append(len(pts) // 2)
        poly_rings.append(len(ring_pts) - 1)
    st["zones"] = len(speeds)
    st["background zones"] = sum(background)
    st["points"] = len(pts) // 2
    tmp = out + ".part"
    with open(tmp, "wb") as fh:
        fh.write(MAGIC_AREAS + struct.pack("<III", len(speeds), len(ring_pts) - 1, len(pts) // 2))
        speeds.tofile(fh)
        background.tofile(fh)
        areas.tofile(fh)
        poly_rings.tofile(fh)
        ring_pts.tofile(fh)
        pts.tofile(fh)
    os.replace(tmp, out)
    return st


AREA_FINE, AREA_COARSE = 7, 10   # grid cells ~140 m and ~1.1 km; zones over ~4.5 km use the coarse grid
NEAR_M = 15.0                    # a point this close to a zone it isn't in is too unsure to call


class AreaIndex(Index):
    """Speed zone polygons in two grids. A point takes the speed of the smallest zone
    holding it. Each zone is in the cells its boundary passes through (checked exactly,
    by ray casting over the edges in that cell's row) and in the cells wholly inside it
    (no check needed). A point outside every specific zone but within NEAR_M of one is
    left unanswered, so a road traced a few metres off its corridor doesn't fall through
    to the 100 km/h background."""

    def __init__(self, path):
        with open(path, "rb") as fh:
            if fh.read(8) != MAGIC_AREAS:
                raise ValueError(f"{path}: not a prepared speed zone area file")
            n, n_rings, n_pts = struct.unpack("<III", fh.read(12))
            self.speeds = array.array("B")
            self.speeds.fromfile(fh, n)
            self.background = array.array("B")
            self.background.fromfile(fh, n)
            self.areas = array.array("d")
            self.areas.fromfile(fh, n)
            poly_rings = array.array("I")
            poly_rings.fromfile(fh, n + 1)
            ring_pts = array.array("I")
            ring_pts.fromfile(fh, n_rings + 1)
            xy = array.array("i")
            xy.fromfile(fh, 2 * n_pts)
        self.bbox = (min(xy[0::2]), min(xy[1::2]), max(xy[0::2]), max(xy[1::2]))
        self._allow_background = True
        self.shift = bytearray(n)
        self.bands = [None] * n          # zone -> {row: array of x1, y1, x2, y2}
        self.edge = {AREA_FINE: {}, AREA_COARSE: {}}
        self.inside = {AREA_FINE: {}, AREA_COARSE: {}}
        pad = 16
        for z in range(n):
            edges = []
            for r in range(poly_rings[z], poly_rings[z + 1]):
                a, b = ring_pts[r], ring_pts[r + 1]
                for k in range(a, b):
                    j = k + 1 if k + 1 < b else a
                    edges.append((xy[2 * k], xy[2 * k + 1], xy[2 * j], xy[2 * j + 1]))
            xs = [e[0] for e in edges]
            ys = [e[1] for e in edges]
            sh = AREA_COARSE if max(max(xs) - min(xs), max(ys) - min(ys)) > (1 << 12) else AREA_FINE
            self.shift[z] = sh
            size = 1 << sh
            bands = {}
            for e in edges:
                for row in range(min(e[1], e[3]) >> sh, (max(e[1], e[3]) >> sh) + 1):
                    lst = bands.get(row)
                    if lst is None:
                        bands[row] = lst = array.array("i")
                    lst.extend(e)
            self.bands[z] = bands
            # boundary cells: every cell within pad of an edge
            border = set()
            for x1, y1, x2, y2 in edges:
                steps = int(max(abs(x2 - x1), abs(y2 - y1)) // (size // 4)) + 1
                for s in range(steps + 1):
                    x = x1 + (x2 - x1) * s // steps
                    y = y1 + (y2 - y1) * s // steps
                    for cx in range((x - pad) >> sh, ((x + pad) >> sh) + 1):
                        for cy in range((y - pad) >> sh, ((y + pad) >> sh) + 1):
                            border.add((cx, cy))
            for cx, cy in border:
                _add(self.edge[sh], _key(cx, cy), z)
            # interior cells: centre inside (even-odd along the centre row), not on the boundary
            for row, lst in bands.items():
                yc = (row << sh) + size // 2
                cross = []
                for k in range(0, len(lst), 4):
                    x1, y1, x2, y2 = lst[k], lst[k + 1], lst[k + 2], lst[k + 3]
                    if (y1 > yc) != (y2 > yc):
                        cross.append(x1 + (yc - y1) * (x2 - x1) / (y2 - y1))
                cross.sort()
                for i in range(0, len(cross) - 1, 2):
                    for cx in range(math.ceil((cross[i] - size / 2) / size), math.floor((cross[i + 1] - size / 2) / size) + 1):
                        if (cx, row) not in border:
                            _add(self.inside[sh], _key(cx, row), z)

    def _contains(self, z, x, y):
        lst = self.bands[z].get(y >> self.shift[z])
        if lst is None:
            return False
        inside = False
        for k in range(0, len(lst), 4):
            x1, y1, x2, y2 = lst[k], lst[k + 1], lst[k + 2], lst[k + 3]
            if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
        return inside

    def _near(self, z, x, y, kx):
        """Within NEAR_M of zone z's boundary."""
        r = int(NEAR_M / M_PER_UNIT) + 1
        lim = NEAR_M * NEAR_M
        sh = self.shift[z]
        seen = set()
        for row in range((y - r) >> sh, ((y + r) >> sh) + 1):
            lst = self.bands[z].get(row)
            if lst is None or row in seen:
                continue
            seen.add(row)
            for k in range(0, len(lst), 4):
                x1, y1 = lst[k], lst[k + 1]
                dx, dy = (lst[k + 2] - x1) * kx, (lst[k + 3] - y1) * M_PER_UNIT
                px, py = (x - x1) * kx, (y - y1) * M_PER_UNIT
                ll = dx * dx + dy * dy
                t = 0.0 if ll == 0 else max(0.0, min(1.0, (px * dx + py * dy) / ll))
                ex, ey = px - t * dx, py - t * dy
                if ex * ex + ey * ey < lim:
                    return True
        return False

    def _sample(self, x, y, ux, uy, kx):
        best = None
        unsure = False
        for sh in (AREA_FINE, AREA_COARSE):
            key = _key(x >> sh, y >> sh)
            for z in self.inside[sh].get(key, ()):
                if best is None or self.areas[z] < self.areas[best]:
                    best = z
            for z in self.edge[sh].get(key, ()):
                if self._contains(z, x, y):
                    if best is None or self.areas[z] < self.areas[best]:
                        best = z
                elif sh == AREA_FINE and not unsure and self._near(z, x, y, kx):
                    unsure = True
        if best is None:
            return None
        if self.background[best] and (unsure or not self._allow_background):
            return None      # only the background holds it, and a specific zone is metres away (or it's a track)
        return self.speeds[best]

    def lookup(self, xy, highway=None):
        """As Index.lookup. The 100 km/h background isn't used for tracks: it covers every farm
        and forestry track, most of them private, which the register doesn't really set."""
        self._allow_background = highway != "track"
        try:
            return super().lookup(xy, highway)
        finally:
            self._allow_background = True


def _add(grid, key, v):
    lst = grid.get(key)
    if lst is None:
        grid[key] = array.array("I", [v])
    else:
        lst.append(v)


def open_index(path):
    """Index, AreaIndex or gov_signs.SignSet, by the prepared file's type."""
    with open(path, "rb") as fh:
        magic = fh.read(8)
    if magic == MAGIC_AREAS:
        return AreaIndex(path)
    from openlion import gov_signs
    if magic == gov_signs.MAGIC_SIGNS:
        return gov_signs.SignSet(path)
    return Index(path)


def load(path, work):
    """An Index from a prepared .bin, or from a .geojson (prepared into work/ once,
    keyed on the file's name, size and time)."""
    if not path.lower().endswith((".geojson", ".json", ".csv")):
        return open_index(path)
    stat = os.stat(path)
    base = os.path.splitext(os.path.basename(path))[0]
    out = os.path.join(work, f"speedzones_{base}_{stat.st_size}_{int(stat.st_mtime)}.bin")
    if not os.path.exists(out):
        prepare(path, out)
    return open_index(out)


def first_of(indexes):
    """One speed_fill callable over several indexes: the first that answers. (A SignSet never
    answers here: it fills the finished graph, see sign_fill.)"""
    def fill(xy, highway=None):
        for ix in indexes:
            if ix.covers(xy):
                v = ix.lookup(xy, highway)
                if v:
                    return v
        return None
    return fill


def sign_fill(sign_sets):
    """build_graph's sign_fill over several SignSets: fills pieces still without a limit."""
    def fill(g, highway, stats):
        for ss in sign_sets:
            for s, v in ss.speeds_for(g, highway, stats).items():
                if g.seg_adas[s] is None:
                    yield s, v
    return fill


VIC_PACKAGE = "https://discover.data.vic.gov.au/api/3/action/package_show?id=speed-zones"


def latest_vic():
    """(name, url) of the newest monthly GeoJSON in the Victorian Speed Zones dataset."""
    import urllib.request
    req = urllib.request.Request(VIC_PACKAGE, headers={"User-Agent": "OpenLion"})
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.load(r)["result"]["resources"]
    geo = [x for x in res if (x.get("format") or "").lower() == "geojson" and x.get("url")]
    if not geo:
        raise RuntimeError("no GeoJSON resource in the Victorian Speed Zones dataset")
    best = max(geo, key=lambda x: x.get("last_modified") or x.get("created") or "")
    return best["name"], best["url"]


def fetch_vic(folder):
    """Download the newest Victorian file into folder (skipped if already there)."""
    import urllib.request
    name, url = latest_vic()
    dest = os.path.join(folder, os.path.basename(url.split("?")[0]))
    if not os.path.exists(dest):
        os.makedirs(folder, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "OpenLion"})
        with urllib.request.urlopen(req, timeout=120) as r, open(dest + ".part", "wb") as fh:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                fh.write(b)
        os.replace(dest + ".part", dest)
    return name, dest


WA_PACKAGE = "https://catalogue.data.wa.gov.au/api/3/action/package_show?id=mrwa-legal-speed-limits"
WA_LAYER = ("https://gisservices.mainroads.wa.gov.au/arcgis/rest/services/OpenData/"
            "RoadAssets_DataPortal/MapServer/8/query")
WA_PAGE = 2000      # the server's maxRecordCount


def _get_json(url, timeout=120):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "OpenLion"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def latest_wa():
    """(name, file name) of the current WA data. The catalogue's last-updated date names
    it, since the map server itself gives no date."""
    import datetime
    meta = _get_json(WA_PACKAGE, 60)["result"]
    day = (meta.get("metadata_modified") or "")[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise RuntimeError("the WA data catalogue gave no date for Legal Speed Limits")
    d = datetime.date.fromisoformat(day)
    return f"Legal Speed Limits {d.day} {d:%B %Y}", f"wa_legal_speed_limits_{day}.geojson"


def fetch_wa(folder, progress=None):
    """Download WA's signed speed limits into one GeoJSON file in folder, page by page.
    The default-rule sections are filtered out on the server. Returns (name, path)."""
    import urllib.parse
    name, fname = latest_wa()
    dest = os.path.join(folder, fname)
    if os.path.exists(dest):
        return name, dest
    os.makedirs(folder, exist_ok=True)
    feats, offset = [], 0
    while True:
        q = urllib.parse.urlencode({
            "where": "SPEED_LIMIT NOT LIKE '%built up%'", "outFields": "SPEED_LIMIT,CWY,ROAD_NAME",
            "outSR": "4326", "orderByFields": "OBJECTID", "resultOffset": offset,
            "resultRecordCount": WA_PAGE, "f": "geojson"})
        page = _get_json(WA_LAYER + "?" + q)
        if "error" in page:
            raise RuntimeError(f"WA map server: {page['error'].get('message', page['error'])}")
        got = page.get("features") or []
        feats += got
        offset += len(got)
        if progress:
            progress(offset)
        more = page.get("exceededTransferLimit") or (page.get("properties") or {}).get("exceededTransferLimit")
        if not got or (not more and len(got) < WA_PAGE):
            break
    if not feats:
        raise RuntimeError("the WA map server sent no speed limits")
    with open(dest + ".part", "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": feats}, fh)
    os.replace(dest + ".part", dest)
    return name, dest


NZ_LAYER = ("https://services.arcgis.com/CXBb7LAjgIIdcsPt/ArcGIS/rest/services/"
            "SpeedLimitZoneFull__View/FeatureServer/0")
# The register keeps its history: a zone that was replaced has whenIneffective set. Only
# zones in force now, and only permanent ones (not variable school zones, seasonal or
# temporary limits).
NZ_WHERE = ("speedCategoryName = 'Permanent' AND (whenIneffective IS NULL OR whenIneffective > CURRENT_TIMESTAMP) "
            "AND (whenEffective IS NULL OR whenEffective <= CURRENT_TIMESTAMP)")


def latest_nz():
    """(name, file name) of the register as it is now, named by its last edit date."""
    import datetime
    info = _get_json(NZ_LAYER + "?f=json", 60)
    ms = ((info.get("editingInfo") or {}).get("dataLastEditDate")
          or (info.get("editingInfo") or {}).get("lastEditDate"))
    if not ms:
        raise RuntimeError("the NZ speed limit register gave no last-edit date")
    d = datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).date()
    return f"National Speed Limit Register {d.day} {d:%B %Y}", f"nz_speed_limits_{d.isoformat()}.geojson"


def _fetch_pages(url, where, fields, dest, progress=None, page_size=2000):
    """Every feature matching where, as one GeoJSON FeatureCollection in dest."""
    import urllib.parse
    feats, offset = [], 0
    while True:
        q = urllib.parse.urlencode({
            "where": where, "outFields": fields, "outSR": "4326", "orderByFields": "OBJECTID",
            "resultOffset": offset, "resultRecordCount": page_size, "f": "geojson"})
        page = _get_json(url + "?" + q)
        if "error" in page:
            raise RuntimeError(f"map server: {page['error'].get('message', page['error'])}")
        got = page.get("features") or []
        feats += got
        offset += len(got)
        if progress:
            progress(offset)
        more = page.get("exceededTransferLimit") or (page.get("properties") or {}).get("exceededTransferLimit")
        if not got or (not more and len(got) < page_size):
            break
    if not feats:
        raise RuntimeError("the map server sent no speed limits")
    with open(dest + ".part", "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": feats}, fh)
    os.replace(dest + ".part", dest)
    return len(feats)


TAS_LAYER = "https://data.stategrowth.tas.gov.au/ags/rest/services/PUBLIC/SPEEDLIMITS/FeatureServer/1"


def latest_tas():
    """(name, file name) of the Tasmanian state road limits, dated by their newest edit (the
    layer has no editing info, so it's asked for the largest LAST_UPDATED)."""
    import datetime
    import urllib.parse
    q = urllib.parse.urlencode({"where": "1=1", "f": "json", "outStatistics": json.dumps(
        [{"statisticType": "max", "onStatisticField": "LAST_UPDATED", "outStatisticFieldName": "newest"}])})
    reply = _get_json(TAS_LAYER + "/query?" + q, 60)
    ms = None
    for f in reply.get("features") or []:
        ms = next((v for v in (f.get("attributes") or {}).values() if isinstance(v, (int, float))), None)
    if not ms:
        raise RuntimeError("the Tasmanian speed limit service gave no last-edit date")
    d = datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).date()
    return f"State Road Speed Limits {d.day} {d:%B %Y}", f"tas_speed_limits_{d.isoformat()}.geojson"


def fetch_tas(folder, progress=None):
    """Download Tasmania's state road speed limits. Returns (name, path)."""
    name, fname = latest_tas()
    dest = os.path.join(folder, fname)
    if not os.path.exists(dest):
        os.makedirs(folder, exist_ok=True)
        _fetch_pages(TAS_LAYER + "/query", "1=1", "PD,CD,CWAY_CODE,DSG_NAME", dest, progress)
    return name, dest


def fetch_nz(folder, progress=None):
    """Download the zones in force from NZ's National Speed Limit Register. Returns (name, path)."""
    name, fname = latest_nz()
    dest = os.path.join(folder, fname)
    if not os.path.exists(dest):
        os.makedirs(folder, exist_ok=True)
        _fetch_pages(NZ_LAYER + "/query", NZ_WHERE,
                     "speedLimitZoneValue,speedCategoryName,speedLimitZoneReasonName,Shape__Area", dest, progress, 500)
    return name, dest


ACT_SERVICES = "https://services1.arcgis.com/E5n4f1VY84i0xSjy/ArcGIS/rest/services"
ACT_SERVICE = re.compile(r"^ACTGOV_Road_Network_(\d{4})_Speed_Zones_view$")


def latest_act():
    """(name, file name, layer URL) of the newest yearly ACT speed zone view. Each year is a
    new service ("ACTGOV_Road_Network_2025_Speed_Zones_view"), so the newest year is found
    in the service list."""
    import time
    years = []
    for attempt in range(3):    # the service list now and then comes back empty for a moment
        services = _get_json(ACT_SERVICES + "?f=json", 60).get("services") or []
        years = sorted(int(m.group(1)) for s in services if (m := ACT_SERVICE.match(s.get("name") or "")))
        if years:
            break
        time.sleep(2 * (attempt + 1))
    if not years:
        raise RuntimeError("the ACT map service lists no road network speed zones")
    y = years[-1]
    url = f"{ACT_SERVICES}/ACTGOV_Road_Network_{y}_Speed_Zones_view/FeatureServer/0"
    return f"Road Network Speed Zones {y}", f"act_speed_zones_{y}.geojson", url


def fetch_act(folder, progress=None):
    """Download the newest ACT speed zones, school zones left out. Returns (name, path)."""
    name, fname, url = latest_act()
    dest = os.path.join(folder, fname)
    if not os.path.exists(dest):
        os.makedirs(folder, exist_ok=True)
        _fetch_pages(url + "/query", "SPEED_ZONE <> 'SCHOOL ZONE'", "SPEED_ZONE,CARRIAGEWAY,ROAD_NAME", dest, progress)
    return name, dest


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "fetch-vic":
        print(*fetch_vic(sys.argv[2]), sep="\n")
    elif len(sys.argv) == 3 and sys.argv[1] == "fetch-wa":
        print(*fetch_wa(sys.argv[2], lambda n: print(f"progress {n}", flush=True)), sep="\n")
    elif len(sys.argv) == 3 and sys.argv[1] == "fetch-act":
        print(*fetch_act(sys.argv[2], lambda n: print(f"progress {n}", flush=True)), sep=chr(10))
    elif len(sys.argv) == 3 and sys.argv[1] == "fetch-tas":
        print(*fetch_tas(sys.argv[2], lambda n: print(f"progress {n}", flush=True)), sep=chr(10))
    elif len(sys.argv) == 3 and sys.argv[1] == "fetch-qld":
        from openlion import gov_signs
        print(*gov_signs.fetch_qld(sys.argv[2], lambda n: print(f"progress {n}", flush=True)), sep=chr(10))
    elif len(sys.argv) == 3 and sys.argv[1] == "fetch-nz":
        print(*fetch_nz(sys.argv[2], lambda n: print(f"progress {n}", flush=True)), sep=chr(10))
    elif len(sys.argv) == 4 and sys.argv[1] == "prepare":
        print(prepare(sys.argv[2], sys.argv[3]))
    else:
        sys.exit("usage: python -m openlion.gov_speed fetch-vic | fetch-wa | fetch-act | fetch-qld | fetch-tas | fetch-nz <folder> | "
                 "prepare <in.geojson|in.csv> <out.bin>")
