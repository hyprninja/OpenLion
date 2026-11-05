"""OSM -> background feature file (auz.fea): water, parks, landuse, rail, labels,
ocean/land, overview roads. Map data (c) OpenStreetMap contributors, ODbL.

Keeps the factory file's grid layout (levels, parcel sizes, scales) and the factory
per-type style bytes (flags, scaleFlag, priority/dimension), report 07/14.

Ocean is drawn the factory way: an ocean square (2:125) over every parcel that is
not entirely land, then the land mass (2:110, flags 3) clipped to the parcel on top.
"""
import array
import collections
import math
import struct
import sys

from . import feawrite
from .features import FeatureFile, wrap16
from .graph import Shape
from .osm_import import WayPoints
from .osm_names import normalise

# (category, type) -> per level: (flags, scaleFlag, attr prefix bytes)
# taken from the most common factory values (report 14)
STYLE = {
    (2, 125): {0: (1, 15), 1: (1, 7), 2: (1, 3), 3: (0, 3), 4: (0, 3)},
    (2, 110): {0: (3, 15), 1: (3, 1), 2: (3, 1), 3: (3, 3), 4: (3, 2)},
    (2, 80): {2: (65, 3), 3: (65, 3)},
    (2, 75): {0: (63, 15), 1: (63, 1), 2: (63, 2), 3: (63, 3), 4: (63, 1)},
    (2, 90): {0: (64, 15), 1: (64, 7), 2: (64, 1), 3: (64, 1)},
    (2, 10): {0: (43, 15), 1: (43, 7), 2: (43, 3), 3: (43, 3)},
    (2, 12): {0: (44, 15), 1: (44, 7), 2: (44, 3), 3: (44, 1)},
    (2, 14): {0: (45, 15), 1: (45, 7), 2: (45, 3), 3: (45, 1)},
    (2, 55): {0: (48, 15), 1: (48, 7)},
    (2, 204): {0: (48, 15)}, (2, 210): {0: (48, 15)}, (2, 211): {0: (48, 15)},
    (2, 201): {0: (48, 15)}, (2, 206): {0: (48, 15)}, (2, 208): {0: (48, 15)},
    (2, 150): {0: (50, 15), 1: (50, 3), 2: (50, 1)},
    (2, 214): {0: (255, 15)},
    (2, 35): {0: (46, 15), 1: (46, 2), 2: (46, 3), 3: (46, 3), 4: (46, 3)},
    (1, 90): {0: (255, 15), 1: (255, 7), 2: (255, 1), 3: (255, 1)},
    (1, 95): {1: (255, 7), 2: (255, 3)},
    (1, 200): {0: (255, 15), 1: (255, 7), 2: (255, 1)},
    (8, 190): {1: (128, 3), 2: (128, 1), 3: (129, 3), 4: (192, 3)},
    (16, 195): {1: (133, 7), 2: (133, 3), 3: (133, 1)},
}
ATTR = {  # priority/dimension prefix per type (factory)
    (2, 125): b"\x00\x01", (2, 110): b"\x30\x01", (2, 80): b"\x00\x01", (2, 75): b"\x40\x01",
    (2, 90): b"\x00\x04", (2, 10): b"\x20\x01", (2, 12): b"\x20\x01", (2, 14): b"\x20\x01",
    (2, 55): b"\x20\x01", (2, 204): b"\x50\x01", (2, 210): b"\x00\x01", (2, 211): b"\x00\x01",
    (2, 201): b"\x00\x01", (2, 206): b"\x00\x01", (2, 208): b"\x00\x01", (2, 150): b"\x50\x01",
    (2, 214): b"\x00\x01", (2, 35): b"\x90\x00", (1, 90): b"\x00\x04", (1, 95): b"\x10\x08",
    (1, 200): b"\x00\x01",
}
NONE = b"\x80\x00\x80\x00"
# draw order inside a parcel (factory: background first)
ORDER = [(2, 125), (2, 110), (2, 80), (2, 10), (2, 12), (2, 14), (2, 55), (2, 204), (2, 210),
         (2, 211), (2, 201), (2, 206), (2, 208), (2, 150), (2, 214), (2, 75), (2, 90),
         (1, 95), (1, 90), (1, 200), (8, 190), (2, 35), (16, 195)]
# simplification tolerance and minimum size per level, in degrees * 1e5
TOL = {0: 1, 1: 3, 2: 15, 3: 60, 4: 150}
MIN_SIZE = {0: 0, 1: 30, 2: 300, 3: 1500, 4: 4000}


def classify_area(t):
    if t.get("natural") == "water" or t.get("landuse") == "reservoir" or t.get("waterway") == "riverbank":
        return (2, 90) if t.get("water") in ("river", "canal") or t.get("waterway") == "riverbank" else (2, 75)
    if t.get("boundary") == "national_park" or (t.get("boundary") == "protected_area" and t.get("protect_class") == "2"):
        return (2, 12) if "STATE PARK" in normalise(t.get("name", "")) else (2, 10)
    if t.get("leisure") == "nature_reserve" or (t.get("boundary") == "protected_area"):
        return (2, 14)
    if t.get("leisure") in ("park", "garden") or t.get("landuse") == "recreation_ground":
        return (2, 55)
    if t.get("leisure") == "golf_course":
        return (2, 204)
    if t.get("leisure") in ("stadium", "sports_centre", "track") or t.get("landuse") == "racecourse":
        return (2, 210)
    if t.get("landuse") == "cemetery" or t.get("amenity") == "grave_yard":
        return (2, 211)
    if t.get("amenity") == "hospital":
        return (2, 201)
    if t.get("amenity") in ("university", "college"):
        return (2, 208)
    if t.get("shop") == "mall":
        return (2, 206)
    if t.get("aeroway") == "aerodrome":
        return (2, 150)
    if t.get("natural") == "beach":
        return (2, 214)
    if t.get("place") in ("island", "islet"):
        return (2, 110)
    if t.get("natural") == "bay":
        return (2, 80)
    return None


def classify_line(t):
    if t.get("waterway") in ("river", "stream"):
        return (1, 90)
    if t.get("waterway") in ("canal", "drain"):
        return (1, 95)
    if t.get("railway") == "rail" and t.get("service") not in ("yard", "siding", "spur", "crossover"):
        return (1, 200)
    return None


KEYS = ("natural", "water", "waterway", "landuse", "leisure", "boundary", "amenity", "shop",
        "aeroway", "place", "railway")


class Collected:
    """Background features, stored compactly (flat coordinate arrays, bboxes precomputed)."""

    def __init__(self):
        self.areas = []      # (type, name, [outer ring Shape], [ring bbox])
        self.lines = []      # (type, name, Shape, bbox)
        self.coast = []      # WayPoints per natural=coastline way
        self.places = []     # (name, x, y, place)


def _name(v):
    return sys.intern(v) if v else None


def read(pbf_path, bbox, margin=20000, node_index="flex_mem"):
    import osmium
    import osmium.filter
    b = (bbox[0] - margin, bbox[1] - margin, bbox[2] + margin, bbox[3] + margin)
    inb = lambda x, y: b[0] <= x <= b[2] and b[1] <= y <= b[3]
    c = Collected()
    fp = (osmium.FileProcessor(pbf_path, osmium.osm.NODE | osmium.osm.WAY | osmium.osm.RELATION | osmium.osm.AREA)
          .with_locations(node_index)
          .with_areas(osmium.filter.KeyFilter(*KEYS))
          .with_filter(osmium.filter.KeyFilter(*KEYS)))
    for o in fp:
        if o.is_node():
            t = {x.k: x.v for x in o.tags}
            if t.get("place") in ("city", "town", "suburb", "village") and "name" in t:
                x, y = round(o.location.lon * 1e5), round(o.location.lat * 1e5)
                if inb(x, y):
                    c.places.append((t["name"], x, y, t["place"]))
        elif o.is_way():
            t = {x.k: x.v for x in o.tags}
            if t.get("natural") == "coastline":
                ids, xy = array.array("q"), array.array("i")
                for n in o.nodes:
                    if n.location.valid():
                        ids.append(n.ref)
                        xy.append(round(n.lon * 1e5))
                        xy.append(round(n.lat * 1e5))
                if ids:
                    c.coast.append(WayPoints(ids, xy))
                continue
            k = classify_line(t)
            if k:
                pts = [(round(n.lon * 1e5), round(n.lat * 1e5)) for n in o.nodes if n.location.valid()]
                if any(inb(*p) for p in pts):
                    c.lines.append((k, _name(t.get("name")), Shape(pts), _bb(pts)))
        elif o.is_area():
            t = {x.k: x.v for x in o.tags}
            k = classify_area(t)
            if not k:
                continue
            rings = [[(round(n.lon * 1e5), round(n.lat * 1e5)) for n in r] for r in o.outer_rings()]
            if any(inb(*p) for r in rings for p in r[:50]):
                c.areas.append((k, _name(t.get("name")), [Shape(r) for r in rings], [_bb(r) for r in rings]))
    return c


# ---- geometry ------------------------------------------------------------------

def coast_rings(chunks):
    """Join coastline ways into closed rings (land on the left)."""
    by_start = {}
    for w in chunks:
        by_start.setdefault(w[0][0], []).append(w)
    used, rings = set(), []
    for w in chunks:
        if id(w) in used:
            continue
        ring = list(w)
        used.add(id(w))
        while ring[-1][0] != ring[0][0]:
            nxt = [v for v in by_start.get(ring[-1][0], []) if id(v) not in used]
            if not nxt:
                break
            used.add(id(nxt[0]))
            ring += nxt[0][1:]
        if ring[-1][0] == ring[0][0] and len(ring) >= 4:
            rings.append(Shape([(x, y) for _, x, y in ring]))
    return rings


def clip_poly(pts, box):
    """Sutherland-Hodgman against an axis-aligned box (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = box
    for edge in range(4):
        if not pts:
            return []
        out = []
        inside = [lambda p: p[0] >= x0, lambda p: p[0] <= x1, lambda p: p[1] >= y0, lambda p: p[1] <= y1][edge]

        def cross(p, q):
            if edge < 2:
                xv = x0 if edge == 0 else x1
                t = (xv - p[0]) / (q[0] - p[0])
                return (xv, p[1] + t * (q[1] - p[1]))
            yv = y0 if edge == 2 else y1
            t = (yv - p[1]) / (q[1] - p[1])
            return (p[0] + t * (q[0] - p[0]), yv)

        prev = pts[-1]
        for cur in pts:
            if inside(cur):
                if not inside(prev):
                    out.append(cross(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(cross(prev, cur))
            prev = cur
        pts = out
    return pts


def clip_line(pts, box):
    """Liang-Barsky per segment; returns list of pieces."""
    x0, y0, x1, y1 = box
    pieces, cur = [], []
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        dx, dy = bx - ax, by - ay
        t0, t1, ok = 0.0, 1.0, True
        for p, q in ((-dx, ax - x0), (dx, x1 - ax), (-dy, ay - y0), (dy, y1 - ay)):
            if p == 0:
                if q < 0:
                    ok = False
                    break
            else:
                r = q / p
                if p < 0:
                    t0 = max(t0, r)
                else:
                    t1 = min(t1, r)
        if not ok or t0 > t1:
            if cur:
                pieces.append(cur)
                cur = []
            continue
        s = (ax + t0 * dx, ay + t0 * dy)
        e = (ax + t1 * dx, ay + t1 * dy)
        if not cur:
            cur = [s]
        elif cur[-1] != s:
            pieces.append(cur)
            cur = [s]
        cur.append(e)
        if t1 < 1.0:
            pieces.append(cur)
            cur = []
    if cur:
        pieces.append(cur)
    return [p for p in pieces if len(p) >= 2]


def simplify(pts, tol):
    """Douglas-Peucker. Always returns a new list of (x, y) tuples."""
    pts = list(pts)
    if tol <= 0 or len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = pts[a], pts[b]
        dx, dy = bx - ax, by - ay
        n = math.hypot(dx, dy)
        best, bi = -1.0, None
        for i in range(a + 1, b):
            if n == 0:   # closed ring: first == last, measure distance to that point
                d = math.hypot(pts[i][0] - ax, pts[i][1] - ay)
            else:
                d = abs(dy * (pts[i][0] - ax) - dx * (pts[i][1] - ay)) / n
            if d > best:
                best, bi = d, i
        if bi is not None and best > tol:
            keep[bi] = True
            stack += [(a, bi), (bi, b)]
    return [p for p, k in zip(pts, keep) if k]


def point_in(ring, x, y):
    c, j = False, len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < xi + (y - yi) * (xj - xi) / (yj - yi):
            c = not c
        j = i
    return c


# ---- encoding -----------------------------------------------------------------

def to_parcel(parts, centre, sx, sy, polygon):
    """Absolute parts -> parcel units; drops repeats (a zero step would read as a separator)."""
    out = []
    for part in parts:
        q = []
        for x, y in part:
            p = (round((x - centre[0]) / sx), round((y - centre[1]) / sy))
            if not q or p != q[-1]:
                q.append(p)
        if polygon and len(q) > 1 and q[0] == q[-1]:
            q.pop()
        if len(q) >= (3 if polygon else 2):
            out.append(q)
    return out


def feature(key, level, parts, name=None, attr=None):
    flags, sf = STYLE[key][level]
    cat = key[0]
    if attr is None:
        pre = ATTR.get(key, b"\x00\x01")
        attr = pre + NONE if cat == 1 else pre + NONE + NONE if cat == 2 else pre
    nm = [normalise(name)] if name and normalise(name) else []
    return {"category": cat, "type": key[1], "flags": flags, "scaleFlag": sf, "attr": attr,
            "names": nm, "b5": 1 if nm else 0, "parts": parts}


# Factory maxima over the whole file (header Zip_Statistic): 57,420 bytes stored,
# 116,000 raw. The unit presumably sizes buffers from them; stay below both.
MAX_PARCEL = 110000
MAX_ZIPPED = 57000
ESSENTIAL = {(2, 125), (2, 110), (2, 80)}


def fit_budget(feats, stats, level):
    """Drop the smallest non-essential features until the parcel fits both limits. If the
    essential ones alone are still too big (New Zealand's sounds and gulfs: dozens of
    detailed bays), bays may go too, smallest first; sea and land always stay."""
    first = collections.Counter()
    kept = _fit(feats, first, level, ESSENTIAL)
    if feats and _too_big(kept):
        stats[f"L{level} parcels over budget with bays"] += 1
        return _fit(feats, stats, level, ESSENTIAL - {(2, 80)})
    stats.update(first)
    return kept


def _too_big(feats):
    import zlib
    raw = feawrite.encode_parcel(feats) if feats else b""
    return len(raw) > MAX_PARCEL or (len(zlib.compress(raw, 9)) if raw else 0) > MAX_ZIPPED


def _fit(feats, stats, level, essential):
    import zlib
    sizes = [len(feawrite.encode_feature(f)) for f in feats]

    def extent(f):
        xs = [p[0] for part in f["parts"] for p in part]
        ys = [p[1] for part in f["parts"] for p in part]
        return (max(xs) - min(xs)) + (max(ys) - min(ys))

    cand = sorted((i for i, f in enumerate(feats) if (f["category"], f["type"]) not in essential),
                  key=lambda i: extent(feats[i]))
    budget = MAX_PARCEL
    drop = set()
    while True:
        total = 4 + sum(s for i, s in enumerate(sizes) if i not in drop)
        for i in cand:
            if total <= budget:
                break
            if i not in drop:
                drop.add(i)
                total -= sizes[i]
        kept = [f for i, f in enumerate(feats) if i not in drop]
        z = len(zlib.compress(feawrite.encode_parcel(kept), 9)) if kept else 0
        if z <= MAX_ZIPPED or not cand or len(drop) >= len(cand):
            break
        budget = int(total * MAX_ZIPPED / z * 0.97)
    if drop:
        stats[f"L{level} dropped-for-size"] += len(drop)
    return kept


def _poly_area(p):
    return abs(sum(p[i][0] * p[i - 1][1] - p[i - 1][0] * p[i][1] for i in range(len(p)))) / 2


def land_cells(polys, grid, rng, out):
    """Recursively clip land polygons to a range of cells. out[(ix, iy)] = parts
    (absolute) for coastal cells, "land" for cells entirely on land; cells not in
    out are open water."""
    x0g, y0g, W, H = grid
    ix0, ix1, iy0, iy1 = rng
    box = (x0g + ix0 * W, y0g + iy0 * H, x0g + (ix1 + 1) * W, y0g + (iy1 + 1) * H)
    parts = [q for q in (clip_poly(p, box) for p in polys) if len(q) >= 3]
    if not parts:
        return
    full = (box[2] - box[0]) * (box[3] - box[1])
    if sum(_poly_area(q) for q in parts) >= full * 0.9999:
        for ix in range(ix0, ix1 + 1):
            for iy in range(iy0, iy1 + 1):
                out[(ix, iy)] = "land"
        return
    if ix0 == ix1 and iy0 == iy1:
        out[(ix0, iy0)] = parts
        return
    if ix1 - ix0 >= iy1 - iy0:
        m = (ix0 + ix1) // 2
        land_cells(parts, grid, (ix0, m, iy0, iy1), out)
        land_cells(parts, grid, (m + 1, ix1, iy0, iy1), out)
    else:
        m = (iy0 + iy1) // 2
        land_cells(parts, grid, (ix0, ix1, iy0, m), out)
        land_cells(parts, grid, (ix0, ix1, m + 1, iy1), out)


def _bucket(items, bbox_of, grid, rng):
    """{(ix, iy): [item index]} over the cell range each item's bbox covers."""
    x0g, y0g, W, H = grid
    ix0, ix1, iy0, iy1 = rng
    out = collections.defaultdict(list)
    for n, it in enumerate(items):
        b = bbox_of(it)
        a0, a1 = max(ix0, int((b[0] - x0g) // W)), min(ix1, int((b[2] - x0g) // W))
        b0, b1 = max(iy0, int((b[1] - y0g) // H)), min(iy1, int((b[3] - y0g) // H))
        for ix in range(a0, a1 + 1):
            for iy in range(b0, b1 + 1):
                out[(ix, iy)].append(n)
    return out


def _bb(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _points(pts):
    """[(x, y)] from a point list or a flat x/y array (the pipeline's compact form)."""
    if pts and not isinstance(pts[0], tuple):
        return list(zip(pts[0::2], pts[1::2]))
    return pts


def _overlaps(b, grid, rng):
    """Does bbox b reach any cell of rng (same cell arithmetic as _bucket)?"""
    x0g, y0g, W, H = grid
    ix0, ix1, iy0, iy1 = rng
    return (max(ix0, int((b[0] - x0g) // W)) <= min(ix1, int((b[2] - x0g) // W)) and
            max(iy0, int((b[1] - y0g) // H)) <= min(iy1, int((b[3] - y0g) // H)))


def _level_params(lv, k, bbox):
    W, H = lv["parcel_w"], lv["parcel_h"]
    x0g, y0g = lv["bbox"][0], lv["bbox"][1]
    rng = (max(0, (bbox[0] - x0g) // W), min(lv["cnt_x"] - 1, (bbox[2] - x0g) // W),
           max(0, (bbox[1] - y0g) // H), min(lv["cnt_y"] - 1, (bbox[3] - y0g) // H))
    return {"k": k, "grid": (x0g, y0g, W, H), "rng": rng, "cnt_x": lv["cnt_x"],
            "sx": lv["parcel_w"] / lv["norm_x"], "sy": lv["parcel_h"] / lv["norm_y"]}


def build_strip(c, road_lines, P, rng, coast, has_sea):
    """Encode the cells of one strip (rng = ix0, ix1, iy0, iy1) of level P["k"].
    Only features reaching the strip are simplified. Each feature is simplified whole
    and kept in its original order, so a cell comes out exactly as in a one-pass build.
    coast: land_cells() result for (at least) these cells. -> ({cell index: bytes}, stats)"""
    k, grid, sx, sy = P["k"], P["grid"], P["sx"], P["sy"]
    x0g, y0g, W, H = grid
    tol, mins = TOL[k], MIN_SIZE[k]
    stats = collections.Counter()
    areas = []
    for key, name, rings, bbs in c.areas:
        if k not in STYLE.get(key, {}):
            continue
        for r, b in zip(rings, bbs):
            if b[2] - b[0] < mins and b[3] - b[1] < mins:
                continue
            if _overlaps(b, grid, rng):
                areas.append((key, name, simplify(r, tol), b))
    lines = [(key, name, simplify(pts, tol)) for key, name, pts, b in c.lines
             if k in STYLE.get(key, {}) and _overlaps(b, grid, rng)]
    roads_k = []
    if k >= 1:
        min_rank = {1: 2, 2: 3, 3: 4, 4: 4}[k]
        for r in road_lines:
            rank, cls, ref, pts = r[:4]
            if rank < min_rank:
                continue
            b = r[4] if len(r) > 4 else _bb(_points(pts))
            if _overlaps(b, grid, rng):
                roads_k.append((rank, cls, ref, simplify(_points(pts), tol)))
    a_cells = _bucket(areas, lambda a: a[3], grid, rng)
    l_cells = _bucket(lines, lambda ln: _bb(ln[2]), grid, rng)
    r_cells = _bucket(roads_k, lambda r: _bb(r[3]), grid, rng)
    p_cells = collections.defaultdict(list)
    for n, (name, x, y, pl) in enumerate(c.places):
        if k >= 2 and pl not in ("city", "town"):
            continue
        if k >= 3 and pl != "city":
            continue
        p_cells[(int((x - x0g) // W), int((y - y0g) // H))].append(n)
    todo = set(a_cells) | set(l_cells) | set(r_cells) | set(p_cells)
    if has_sea:
        for ix in range(rng[0], rng[1] + 1):
            for iy in range(rng[2], rng[3] + 1):
                if coast.get((ix, iy)) != "land":
                    todo.add((ix, iy))
    cells = {}
    for (ix, iy) in sorted(todo):
        if not (rng[0] <= ix <= rng[1] and rng[2] <= iy <= rng[3]):
            continue
        box = (x0g + ix * W, y0g + iy * H, x0g + (ix + 1) * W, y0g + (iy + 1) * H)
        centre = (box[0] + W // 2, box[1] + H // 2)
        cx, cy = centre
        feats = collections.defaultdict(list)
        cs = coast.get((ix, iy))
        if has_sea and cs != "land":
            sq = [(box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])]
            feats[(2, 125)].append(feature((2, 125), k, to_parcel([sq], centre, sx, sy, True)))
            if cs:
                pp = to_parcel(cs, centre, sx, sy, True)
                if pp:
                    feats[(2, 110)].append(feature((2, 110), k, pp))
        for n in a_cells.get((ix, iy), []):
            key, name, r, ab = areas[n]
            pp = to_parcel([clip_poly(r, box)], centre, sx, sy, True)
            if pp:
                feats[key].append(feature(key, k, pp, name))
        for n in l_cells.get((ix, iy), []):
            key, name, pts = lines[n]
            pp = to_parcel(clip_line(pts, box), centre, sx, sy, False)
            if pp:
                feats[key].append(feature(key, k, pp, name))
        for n in r_cells.get((ix, iy), []):
            rank, cls, ref, pts = roads_k[n]
            pp = to_parcel(clip_line(pts, box), centre, sx, sy, False)
            if pp and k in STYLE[(8, 190)]:
                feats[(8, 190)].append(feature((8, 190), k, pp, None, bytes([rank, cls, 0])))
                if ref and k in STYLE[(16, 195)]:
                    p0 = pp[0][len(pp[0]) // 2]
                    feats[(16, 195)].append(feature((16, 195), k, [[p0, (p0[0] - 2, p0[1] + 1)]], ref,
                                                    b"\x96\x01\x00\x00"))
        if k in STYLE[(2, 35)]:
            for n in p_cells.get((ix, iy), []):
                name, x, y, pl = c.places[n]
                p = (round((x - cx) / sx), round((y - cy) / sy))
                feats[(2, 35)].append(feature((2, 35), k, [[p, (p[0] - 2, p[1] + 1)]], name,
                                              b"\x90\x00" + NONE + struct.pack(">hh", *p)))
        out = []
        for key in ORDER:
            out += feats.get(key, [])
        out = fit_budget(out, stats, k)
        for ft in out:
            stats[f"L{k} {ft['category']}:{ft['type']}"] += 1
        if out:
            cells[iy * P["cnt_x"] + ix] = feawrite.encode_parcel(out)
    return cells, stats


def _strips(rng, n):
    """Split a cell range into up to n column strips."""
    ix0, ix1, iy0, iy1 = rng
    cols = ix1 - ix0 + 1
    n = max(1, min(n, cols))
    edges = [ix0 + cols * i // n for i in range(n + 1)]
    return [(a, b - 1, iy0, iy1) for a, b in zip(edges, edges[1:]) if b > a]


def _strip_inputs(c, road_lines, P, strips):
    """Split the features among column strips in one pass: each strip gets the features
    whose bbox reaches it (the test build_strip makes), in their original order."""
    import bisect
    x0g, y0g, W, H = P["grid"]
    k = P["k"]
    ix0, ix1, iy0, iy1 = P["rng"]
    starts = [r[0] for r in strips]

    def span(b):
        a0, a1 = max(ix0, int((b[0] - x0g) // W)), min(ix1, int((b[2] - x0g) // W))
        if a0 > a1 or max(iy0, int((b[1] - y0g) // H)) > min(iy1, int((b[3] - y0g) // H)):
            return None
        return bisect.bisect_right(starts, a0) - 1, bisect.bisect_right(starts, a1) - 1

    subs = [Collected() for _ in strips]
    roads = [[] for _ in strips]
    for sub in subs:
        sub.places = c.places
    for a in c.areas:
        if k not in STYLE.get(a[0], {}):
            continue
        hit = set()
        for b in a[3]:
            sp = span(b)
            if sp:
                hit.update(range(sp[0], sp[1] + 1))
        for n in sorted(hit):
            subs[n].areas.append(a)
    for ln in c.lines:
        if k in STYLE.get(ln[0], {}):
            sp = span(ln[3])
            if sp:
                for n in range(sp[0], sp[1] + 1):
                    subs[n].lines.append(ln)
    if k >= 1:
        min_rank = {1: 2, 2: 3, 3: 4, 4: 4}[k]
        for r in road_lines:
            if r[0] >= min_rank:
                sp = span(r[4] if len(r) > 4 else _bb(_points(r[3])))
                if sp:
                    for n in range(sp[0], sp[1] + 1):
                        roads[n].append(r)
    return subs, roads


def _worker_strip(args):
    sub, roads, P, rng, coast, has_sea = args
    return P["k"], build_strip(sub, roads, P, rng, coast, has_sea)


def build(c, factory_fea: bytes, bbox, road_lines=(), progress=None, workers=1, strips=8):
    """-> new auz.fea bytes (factory grid, OSM content inside bbox) and stats.
    road_lines: [(rank, class, ref, points[, bbox])] for the overview roads (levels 1-4);
    points is [(x, y)] or a flat x/y array.

    Each level is built in column strips. With workers == 1 they run here one after
    another, so only one strip's simplified geometry is in memory at a time. With
    workers > 1 they run in that many processes, each strip with just the features that
    reach it. The output is identical either way."""
    f = FeatureFile(factory_fea)
    stats = collections.Counter()
    land = coast_rings(c.coast)
    stats["coast rings"] = len(land)
    has_sea = bool(land)
    params = [_level_params(lv, k, bbox) for k, lv in enumerate(f.levels)]

    def coast_of(P):
        """land / sea per cell: one recursive pass over the whole level"""
        coast = {}
        if land:
            land_cells([simplify(r, TOL[P["k"]]) for r in land], P["grid"], P["rng"], coast)
        return coast

    results = [dict() for _ in f.levels]
    if workers <= 1:
        for k, P in enumerate(params):
            coast = coast_of(P)       # one level's coastline at a time
            for rng in _strips(P["rng"], strips):
                cells, st = build_strip(c, road_lines, P, rng, coast, has_sea)
                results[k].update(cells)
                stats.update(st)
            coast = None
            if progress:
                progress(k)
    else:
        # each strip goes to a worker with only the features that reach it; a few strips
        # per worker are in flight at a time, so memory stays flat however many there are
        import concurrent.futures as cf

        def tasks():
            for k, P in enumerate(params):
                coast = coast_of(P)
                strips_k = _strips(P["rng"], max(strips, workers * 3))
                subs, roads = _strip_inputs(c, road_lines, P, strips_k)
                for n, rng in enumerate(strips_k):
                    cs = {xy: v for xy, v in coast.items() if rng[0] <= xy[0] <= rng[1]}
                    yield k, n == len(strips_k) - 1, (subs[n], roads[n], P, rng, cs, has_sea)
                    subs[n] = roads[n] = None
                coast = subs = roads = None

        left, all_sent, done_upto = collections.Counter(), set(), 0
        with cf.ProcessPoolExecutor(max_workers=workers) as ex:
            inflight = set()
            gen = tasks()
            more = True
            while more or inflight:
                while more and len(inflight) < workers * 2:
                    try:
                        k, last, args = next(gen)
                    except StopIteration:
                        more = False
                        break
                    inflight.add(ex.submit(_worker_strip, args))
                    left[k] += 1
                    if last:
                        all_sent.add(k)
                done, inflight = cf.wait(inflight, return_when=cf.FIRST_COMPLETED)
                for fut in done:
                    k, (cells, st) = fut.result()
                    results[k].update(cells)
                    stats.update(st)
                    left[k] -= 1
                while done_upto < len(params) and done_upto in all_sent and left[done_upto] == 0:
                    if progress:
                        progress(done_upto)
                    done_upto += 1
    land = None
    levels = []
    for k, lv in enumerate(f.levels):
        cells = [b""] * (lv["cnt_x"] * lv["cnt_y"])
        for idx, blob in results[k].items():
            cells[idx] = blob
        levels.append({"bbox": lv["bbox"], "parcel_w": lv["parcel_w"], "parcel_h": lv["parcel_h"],
                       "cnt_x": lv["cnt_x"], "cnt_y": lv["cnt_y"], "norm_x": lv["norm_x"],
                       "norm_y": lv["norm_y"], "feaType": lv["feaType"], "scales": lv["scales"],
                       "raw_parcels": cells})
    return feawrite.encode_fea(factory_fea[:0x64], levels), stats
