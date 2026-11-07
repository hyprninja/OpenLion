"""Speed limits from speed signs (points), for roads where OSM has no maxspeed.

Queensland (Department of Transport and Main Roads, "Speed Limits for state and local
roads", CC BY 4.0) has no zone data, only a survey of its speed signs: about 206,000
records from drives in 2019 and 2020, one CSV per council, never updated since. Each
record is a point with the speed, the sign type, the street name and the direction the
survey car was driving.

Only plain speed limit signs (R4-1A to R4-1D) with no extra text are used. Left out:
- "UNSIGNED" records (two thirds of the data): the default rule (50 in built-up areas,
  100 outside), not a signed limit, like WA's default sections;
- school zones (R4-Q01, or any sign with text), variable signs (TC1785), road works and
  ramp speeds, other temporary signs;
- area signs (R4-10/R4-11: "40 AREA" covers every street in an area) and shared zones.

A sign starts a limit in its direction of travel. fill() snaps each sign to an OSM
road piece with the same name within 20 m, running the sign's way, and walks forward
from it: at each junction onto the straightest continuation with the same name, until
the next sign on the road (either way round, if the other way's says a different speed),
a fork, the end of the name, or 5 km. Motorways (gantry signs, mostly variable),
ramps, service roads and tracks are never filled. A piece gets the speed covering most
of it (>= 60 % of its length, >= 75 % of what was covered) in a direction; two
directions that disagree give the lower.
"""
import collections
import csv
import io
import json
import math
import os
import re
import zlib

MAGIC_SIGNS = b"OLGSPS1\0"
M_PER_UNIT = 1.11195        # metres per 1e-5 degree of latitude
SNAP_M = 20.0
CAP_M = 5000.0
MAX_TURN = 60.0             # degrees: sharper is a turn off the road
FORK_GAP = 20.0             # degrees: two continuations closer than this is a fork
CELL = 6                    # grid cell = 2**6 units (~70 m)
NO_FILL = ("motorway", "service", "track")

QLD_PACKAGE = "https://www.data.qld.gov.au/api/3/action/package_show?id=speed-limits-for-state-and-local-roads"
QLD_HOSTS = ("www.data.qld.gov.au", "data.qld.gov.au")
QLD_SIGN = re.compile(r"R4-1[A-D]")
BEARING = {"NORTH": 0, "NORTHEAST": 45, "EAST": 90, "SOUTHEAST": 135, "SOUTH": 180,
           "SOUTHWEST": 225, "WEST": 270, "NORTHWEST": 315}
# street types as both OSM and the survey spell them, to one form
TYPES = {"ROAD": "RD", "STREET": "ST", "DRIVE": "DR", "AVENUE": "AVE", "AV": "AVE", "HIGHWAY": "HWY",
         "PARADE": "PDE", "TERRACE": "TCE", "COURT": "CT", "PLACE": "PL", "CRESCENT": "CRES",
         "CLOSE": "CL", "BOULEVARD": "BVD", "BLVD": "BVD", "BOULEVARDE": "BVD", "LANE": "LN",
         "MOTORWAY": "MWY", "ESPLANADE": "ESP", "CIRCUIT": "CCT", "GROVE": "GR", "PARKWAY": "PKWY",
         "FREEWAY": "FWY", "STR": "ST", "HWAY": "HWY"}
CSV_FIELDS = ("LONGITUDE", "LATITUDE", "SPEED", "DIRECTION", "ST_NAME", "ST_TYPE")


def norm_name(name):
    """'Stuart Drive' and 'STUART DR' -> 'STUART DR'; None for no name."""
    t = re.sub(r"[^A-Z0-9 ]", " ", (name or "").upper()).split()
    if not t:
        return None
    # a direction suffix ("ROAD EAST") stays, the type before it is normalised too
    for k in range(len(t) - 1, max(len(t) - 3, 0) - 1, -1):
        if t[k] in TYPES:
            t[k] = TYPES[t[k]]
            break
    return " ".join(t)


def sign_row(r):
    """A survey record -> (lon, lat, speed, bearing, street) or (None, why)."""
    if not QLD_SIGN.fullmatch((r.get("SIGN_NUM") or "").strip()):
        return None, "not a plain speed limit sign"
    if (r.get("SIGN_TXT") or "").strip():
        return None, "sign with text (school zone, road work, ramp...)"
    try:
        speed = int(r["SPEED"])
        lat, lon = float(r["LATITUDE"]), float(r["LONGITUDE"])
    except (KeyError, TypeError, ValueError):
        return None, "no speed or position"
    b = BEARING.get((r.get("DIRECTION") or "").strip().upper())
    if b is None or not 10 <= speed <= 130 or speed % 10 or not (-45 < lat < -5 and 110 < lon < 160):
        return None, "no speed or position"
    street = f"{r.get('ST_NAME') or ''} {r.get('ST_TYPE') or r.get('STREET_TYPE') or ''}".strip()
    if not street:
        return None, "no street name"
    return (lon, lat, speed, b, street), None


def prepare_signs(path, out):
    """Signs CSV (fetch_qld's, or a council file from the survey) -> compact file. Returns stats."""
    st = collections.Counter()
    names, name_ix, signs = [], {}, []
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        for r in csv.DictReader(fh):
            st["records"] += 1
            v, why = sign_row(r)
            if v is None:
                st[why] += 1
                continue
            lon, lat, speed, b, street = v
            n = norm_name(street)
            if n not in name_ix:
                name_ix[n] = len(names)
                names.append(n)
            signs.append((round(lon * 1e5), round(lat * 1e5), speed, b, name_ix[n]))
    st["signs"] = len(signs)
    if not signs:
        raise ValueError(f"{path}: no usable speed signs")
    blob = zlib.compress(json.dumps({"names": names, "signs": signs}, separators=(",", ":")).encode(), 9)
    with open(out + ".part", "wb") as fh:
        fh.write(MAGIC_SIGNS + blob)
    os.replace(out + ".part", out)
    return dict(st)


def _mvec(ax, ay, bx, by):
    """(dx, dy) in metres from a to b (units of 1e-5 degree)."""
    kx = math.cos(math.radians((ay + by) / 2e5)) * M_PER_UNIT
    return (bx - ax) * kx, (by - ay) * M_PER_UNIT


class SignSet:
    """Prepared speed signs; speeds_for(graph) works out the limits they set."""

    def __init__(self, path):
        with open(path, "rb") as fh:
            if fh.read(8) != MAGIC_SIGNS:
                raise ValueError(f"{path}: not a prepared speed sign file")
            d = json.loads(zlib.decompress(fh.read()))
        self.names = d["names"]
        self.signs = [tuple(s) for s in d["signs"]]
        self.speeds = [s[2] for s in self.signs]
        xs = [s[0] for s in self.signs]
        ys = [s[1] for s in self.signs]
        pad = 2000      # ~2 km: walks run on past the outermost signs
        self.bbox = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)

    def covers(self, xy):
        return False    # not a per-piece lookup: see speeds_for

    def speeds_for(self, g, highway, stats=None):
        """{segment: km/h} for the graph's pieces the signs give a limit, OSM's or not.
        highway: OSM highway value per segment (None for connectors)."""
        if stats is None:
            stats = collections.Counter()
        b = self.bbox
        poly, cum, names = {}, {}, {}
        grid = collections.defaultdict(list)
        for s in range(g.n_segs):
            hw = highway[s]
            if not hw or hw in NO_FILL or hw.endswith("_link"):
                continue
            nm = norm_name((g.seg_label[s] or (None,))[0])
            if nm is None:
                continue
            pts = [g.node_xy[g.seg_a[s]], *(g.seg_shape[s] or ()), g.node_xy[g.seg_b[s]]]
            if not any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for x, y in (pts[0], pts[-1])):
                continue
            d = [0.0]
            for i in range(1, len(pts)):
                dx, dy = _mvec(*pts[i - 1], *pts[i])
                d.append(d[-1] + math.hypot(dx, dy))
            poly[s], cum[s], names[s] = pts, d, nm
            for i in range(len(pts) - 1):
                (ax, ay), (bx, by) = pts[i], pts[i + 1]
                for cx in range((min(ax, bx) >> CELL) - 1, (max(ax, bx) >> CELL) + 2):
                    for cy in range((min(ay, by) >> CELL) - 1, (max(ay, by) >> CELL) + 2):
                        grid[(cx, cy)].append((s, i))

        # snap: each sign onto a same-named piece running its way
        on_seg = collections.defaultdict(list)      # (seg, dir) -> [(offset m, speed)]
        snapped = []
        for x, y, speed, bearing, ni in self.signs:
            want = self.names[ni]
            best = None
            for s, i in grid.get((x >> CELL, y >> CELL), ()):
                (ax, ay), (bx, by) = poly[s][i], poly[s][i + 1]
                vx, vy = _mvec(ax, ay, bx, by)
                L2 = vx * vx + vy * vy
                if not L2:
                    continue
                wx, wy = _mvec(ax, ay, x, y)
                t = max(0.0, min(1.0, (wx * vx + wy * vy) / L2))
                dist = math.hypot(wx - t * vx, wy - t * vy)
                if dist > SNAP_M:
                    continue
                diff = abs((math.degrees(math.atan2(vx, vy)) - bearing + 180) % 360 - 180)
                if diff <= 60:
                    dr = 1
                elif diff >= 120:
                    dr = -1
                else:
                    continue
                key = (names[s] != want, dist)
                if best is None or key < best[0]:
                    best = (key, s, cum[s][i] + t * math.sqrt(L2), dr)
            if best is None:
                stats["signs not on a road going their way"] += 1
                continue
            if best[0][0]:
                stats["signs whose road has another name in OSM"] += 1
                continue
            _, s, off, dr = best
            snapped.append((s, off, dr, speed))
            on_seg[(s, dr)].append((off, speed))
            stats["signs placed"] += 1

        def unit(p, q):
            dx, dy = _mvec(*p, *q)
            L = math.hypot(dx, dy) or 1.0
            return dx / L, dy / L

        def arrive(s, dr):
            pts = poly[s]
            return (g.seg_b[s], unit(pts[-2], pts[-1])) if dr == 1 else (g.seg_a[s], unit(pts[1], pts[0]))

        def depart(s, node):
            pts = poly[s]
            return (1, unit(pts[0], pts[1])) if g.seg_a[s] == node else (-1, unit(pts[-1], pts[-2]))

        cover = collections.defaultdict(list)       # (seg, dir) -> [(lo, hi, speed)]
        for s, off, dr, speed in snapped:
            cur, pos, walked, seen = (s, dr), off, 0.0, set()
            while True:
                s1, d1 = cur
                fwd = (lambda o: o > pos + 1) if d1 == 1 else (lambda o: o < pos - 1)
                ahead = [o for o, _ in on_seg.get(cur, ()) if fwd(o)]
                ahead += [o for o, q in on_seg.get((s1, -d1), ()) if q != speed and fwd(o)]
                if ahead:
                    stop = min(ahead) if d1 == 1 else max(ahead)
                    cover[cur].append((min(pos, stop), max(pos, stop), speed))
                    break
                end = cum[s1][-1] if d1 == 1 else 0.0
                if walked + abs(end - pos) > CAP_M:
                    lim = pos + (CAP_M - walked) * d1
                    cover[cur].append((min(pos, lim), max(pos, lim), speed))
                    break
                cover[cur].append((min(pos, end), max(pos, end), speed))
                walked += abs(end - pos)
                seen.add(s1)
                node, (ux, uy) = arrive(s1, d1)
                nxt = []
                for s2 in g.node_arms[node]:
                    if s2 in seen or names.get(s2) != names[s1]:
                        continue
                    d2, (vx, vy) = depart(s2, node)
                    turn = math.degrees(math.acos(max(-1.0, min(1.0, ux * vx + uy * vy))))
                    if turn <= MAX_TURN:
                        nxt.append((turn, s2, d2))
                if not nxt:
                    break
                nxt.sort()
                if len(nxt) > 1 and nxt[1][0] - nxt[0][0] < FORK_GAP:
                    break
                _, s2, d2 = nxt[0]
                cur, pos = (s2, d2), (0.0 if d2 == 1 else cum[s2][-1])

        out = {}
        for s in {k[0] for k in cover}:
            L = cum[s][-1] or 1.0
            got = []
            for dr in (1, -1):
                acc = collections.Counter()
                for lo, hi, speed in cover.get((s, dr), ()):
                    acc[speed] += hi - lo
                if acc:
                    speed, run = acc.most_common(1)[0]
                    if run >= 0.6 * L and run >= 0.75 * sum(acc.values()):
                        got.append(speed)
            if got:
                out[s] = min(got)
        return out


def latest_qld():
    """(name, file name) of the Queensland survey, dated by its catalogue entry."""
    import datetime
    from openlion.gov_speed import _get_json
    meta = _get_json(QLD_PACKAGE, 60)["result"]
    day = (meta.get("metadata_modified") or "")[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise RuntimeError("the Queensland data catalogue gave no date for its speed limits")
    d = datetime.date.fromisoformat(day)
    return f"Speed limit signs {d.day} {d:%B %Y}", f"qld_speed_signs_{day}.csv", meta


def fetch_qld(folder, progress=None):
    """Download the survey (one CSV per council) into one CSV of the usable signs in folder.
    progress(n) gets the number of signs kept so far. Returns (name, path)."""
    import urllib.parse
    import urllib.request
    name, fname, meta = latest_qld()
    dest = os.path.join(folder, fname)
    if os.path.exists(dest):
        return name, dest
    os.makedirs(folder, exist_ok=True)
    urls = []
    for res in meta.get("resources") or []:
        u = res.get("url") or ""
        p = urllib.parse.urlparse(u)
        if p.scheme == "https" and p.hostname in QLD_HOSTS and (res.get("format") or "").upper() == "CSV":
            urls.append(u)
    if not urls:
        raise RuntimeError("the Queensland data catalogue lists no speed limit files")
    kept = 0
    with open(dest + ".part", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(("SIGN_NUM", *CSV_FIELDS))
        for u in urls:
            for attempt in range(3):
                try:
                    req = urllib.request.Request(u, headers={"User-Agent": "OpenLion"})
                    with urllib.request.urlopen(req, timeout=120) as r:
                        text = r.read().decode("latin-1")
                    break
                except OSError:
                    if attempt == 2:
                        raise
            for r in csv.DictReader(io.StringIO(text)):
                v, _ = sign_row(r)
                if v is None:
                    continue
                lon, lat, speed, b, street = v
                d = next(k for k, deg in BEARING.items() if deg == b)
                w.writerow((r["SIGN_NUM"].strip(), lon, lat, speed, d, street, ""))
                kept += 1
            if progress:
                progress(kept)
    if not kept:
        raise RuntimeError("the Queensland speed limit files had no usable signs")
    os.replace(dest + ".part", dest)
    return name, dest
