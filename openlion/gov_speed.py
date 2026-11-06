"""Speed limits from government open data, for roads where OSM has no maxspeed.

Victoria (Department of Transport and Planning, "Speed Zones", CC BY 4.0, monthly):
every road in the state as GeoJSON lines with speed_limit, zone_conditions and a
compass direction. Most roads come as two features with the same geometry, one per
travel direction; they disagree on speed in about 0.5% of pairs. The card stores one
limit per segment, so a line keeps the lower of its directions.

Zones that only apply some of the time (school, shopping strip, seasonal, side-road
activated) are left out: the dataset records their lowest value, which would be wrong
most of the day. Such roads keep whatever OSM says, or no limit.

Western Australia (Main Roads WA, "Legal Speed Limits", CC BY 4.0, refreshed weekly):
every public road as lines with SPEED_LIMIT ("60km/h") and CWY (Single, or Left/Right
for each side of a divided road). About three quarters of the sections only say "50km/h
applies in built up areas or 110km/h outside built up areas": that's the default rule,
not a signed limit, and the data doesn't say which half applies, so those are left out.
Part-time zones (school, children's crossing) are a separate layer and aren't used.

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
SOURCES = {"VIC": "Victoria Department of Transport and Planning, Speed Zones (CC BY 4.0)",
           "WA": "Main Roads Western Australia, Legal Speed Limits (CC BY 4.0)"}
NORMAL = "Normal Operation"
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
    Knows Victoria's (speed_limit, zone_conditions) and WA's (SPEED_LIMIT) fields."""
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
    """Speed zone GeoJSON (Victoria or WA) -> compact binary. Returns stats."""
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

    def lookup(self, xy):
        """xy: the piece's points (1e-5 degree units). Returns km/h when most of the
        piece lies on zone lines that agree, else None."""
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


def load(path, work):
    """An Index from a prepared .bin, or from a .geojson (prepared into work/ once,
    keyed on the file's name, size and time)."""
    if not path.lower().endswith((".geojson", ".json")):
        return Index(path)
    stat = os.stat(path)
    base = os.path.splitext(os.path.basename(path))[0]
    out = os.path.join(work, f"speedzones_{base}_{stat.st_size}_{int(stat.st_mtime)}.bin")
    if not os.path.exists(out):
        prepare(path, out)
    return Index(out)


def first_of(indexes):
    """One speed_fill callable over several indexes: the first that answers."""
    def fill(xy):
        for ix in indexes:
            if ix.covers(xy):
                v = ix.lookup(xy)
                if v:
                    return v
        return None
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


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "fetch-vic":
        print(*fetch_vic(sys.argv[2]), sep="\n")
    elif len(sys.argv) == 3 and sys.argv[1] == "fetch-wa":
        print(*fetch_wa(sys.argv[2], lambda n: print(f"progress {n}", flush=True)), sep="\n")
    elif len(sys.argv) == 4 and sys.argv[1] == "prepare":
        print(prepare(sys.argv[2], sys.argv[3]))
    else:
        sys.exit("usage: python -m openlion.gov_speed fetch-vic <folder> | fetch-wa <folder> | "
                 "prepare <in.geojson> <out.bin>")
