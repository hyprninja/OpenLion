"""OSM -> background feature file (auz.fea): water, parks, landuse, rail, labels,
ocean/land, overview roads. Map data (c) OpenStreetMap contributors, ODbL.

Keeps the factory file's grid layout (levels, parcel sizes, scales) and the factory
per-type style bytes (flags, scaleFlag, priority/dimension), report 07/14.

Ocean is drawn the factory way: an ocean square (2:125) over every parcel that is
not entirely land, then the land mass (2:110, flags 3) clipped to the parcel on top.
"""
import collections
import math
import struct

from . import feawrite
from .features import FeatureFile, wrap16
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
    def __init__(self):
        self.areas = []      # (type, name, [outer rings])
        self.lines = []      # (type, name, [points])
        self.coast = []      # [(node_id, x, y)] per natural=coastline way
        self.places = []     # (name, x, y, place)


def read(pbf_path, bbox, margin=20000):
    import osmium
    import osmium.filter
    b = (bbox[0] - margin, bbox[1] - margin, bbox[2] + margin, bbox[3] + margin)
    inb = lambda x, y: b[0] <= x <= b[2] and b[1] <= y <= b[3]
    c = Collected()
    fp = (osmium.FileProcessor(pbf_path, osmium.osm.NODE | osmium.osm.WAY | osmium.osm.RELATION | osmium.osm.AREA)
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
                pts = [(n.ref, round(n.lon * 1e5), round(n.lat * 1e5)) for n in o.nodes if n.location.valid()]
                if pts:
                    c.coast.append(pts)
                continue
            k = classify_line(t)
            if k:
                pts = [(round(n.lon * 1e5), round(n.lat * 1e5)) for n in o.nodes if n.location.valid()]
                if any(inb(*p) for p in pts):
                    c.lines.append((k, t.get("name"), pts))
        elif o.is_area():
            t = {x.k: x.v for x in o.tags}
            k = classify_area(t)
            if not k:
                continue
            rings = [[(round(n.lon * 1e5), round(n.lat * 1e5)) for n in r] for r in o.outer_rings()]
            if any(inb(*p) for r in rings for p in r[:50]):
                c.areas.append((k, t.get("name"), rings))
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
            rings.append([(x, y) for _, x, y in ring])
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
    """Douglas-Peucker."""
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
            "names": nm, "b5": 1 if nm else 0, "b6": 0, "parts": parts}


# Factory maxima over the whole file (header Zip_Statistic): 57,420 bytes stored,
# 116,000 raw. The unit presumably sizes buffers from them; stay below both.
MAX_PARCEL = 110000
MAX_ZIPPED = 57000
ESSENTIAL = {(2, 125), (2, 110), (2, 80)}


def fit_budget(feats, stats, level):
    """Drop the smallest non-essential features until the parcel fits both limits."""
    import zlib
    sizes = [len(feawrite.encode_feature(f)) for f in feats]

    def extent(f):
        xs = [p[0] for part in f["parts"] for p in part]
        ys = [p[1] for part in f["parts"] for p in part]
        return (max(xs) - min(xs)) + (max(ys) - min(ys))

    cand = sorted((i for i, f in enumerate(feats) if (f["category"], f["type"]) not in ESSENTIAL),
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


def build(c, factory_fea: bytes, bbox, road_lines=(), progress=None):
    """-> new auz.fea bytes (factory grid, OSM content inside bbox) and stats.
    road_lines: [(rank, class, ref, [points])] for the overview roads (levels 1-4)."""
    f = FeatureFile(factory_fea)
    stats = collections.Counter()
    land = coast_rings(c.coast)
    region_land = [clip_poly(r, bbox) for r in land]
    region_land = [r for r in region_land if len(r) >= 3]
    stats["coast rings"] = len(land)
    levels = []
    for k, lv in enumerate(f.levels):
        sx, sy = lv["parcel_w"] / lv["norm_x"], lv["parcel_h"] / lv["norm_y"]
        x0g, y0g = lv["bbox"][0], lv["bbox"][1]
        W, H = lv["parcel_w"], lv["parcel_h"]
        cells = [b""] * (lv["cnt_x"] * lv["cnt_y"])
        ix0 = max(0, (bbox[0] - x0g) // W)
        ix1 = min(lv["cnt_x"] - 1, (bbox[2] - x0g) // W)
        iy0 = max(0, (bbox[1] - y0g) // H)
        iy1 = min(lv["cnt_y"] - 1, (bbox[3] - y0g) // H)
        tol, mins = TOL[k], MIN_SIZE[k]
        # per-level geometry, simplified once
        areas = []
        for key, name, rings in c.areas:
            if k not in STYLE.get(key, {}):
                continue
            for r in rings:
                xs = [p[0] for p in r]
                ys = [p[1] for p in r]
                if max(xs) - min(xs) < mins and max(ys) - min(ys) < mins:
                    continue
                areas.append((key, name, simplify(r, tol), (min(xs), min(ys), max(xs), max(ys))))
        lines = []
        for key, name, pts in c.lines:
            if k in STYLE.get(key, {}):
                lines.append((key, name, simplify(pts, tol)))
        land_k = [simplify(r, tol) for r in region_land]
        roads_k = []
        if k >= 1:
            min_rank = {1: 2, 2: 3, 3: 4, 4: 4}[k]
            for rank, cls, ref, pts in road_lines:
                if rank >= min_rank:
                    roads_k.append((rank, cls, ref, simplify(pts, tol)))
        for iy in range(iy0, iy1 + 1):
            for ix in range(ix0, ix1 + 1):
                box = (x0g + ix * W, y0g + iy * H, x0g + (ix + 1) * W, y0g + (iy + 1) * H)
                centre = (box[0] + W // 2, box[1] + H // 2)
                feats = collections.defaultdict(list)
                # ocean + land masses
                land_parts = [clip_poly(r, box) for r in land_k]
                land_parts = [p for p in land_parts if len(p) >= 3]
                cx, cy = centre
                all_land = not land_parts and any(point_in(r, cx, cy) for r in land_k)
                if c.coast and not all_land and (land_parts or k in (0, 1, 2, 3, 4)):
                    sq = [(box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])]
                    feats[(2, 125)].append(feature((2, 125), k, to_parcel([sq], centre, sx, sy, True)))
                    if land_parts:
                        pp = to_parcel(land_parts, centre, sx, sy, True)
                        if pp:
                            feats[(2, 110)].append(feature((2, 110), k, pp))
                for key, name, r, ab in areas:
                    if ab[2] < box[0] or ab[0] > box[2] or ab[3] < box[1] or ab[1] > box[3]:
                        continue
                    pp = to_parcel([clip_poly(r, box)], centre, sx, sy, True)
                    if pp:
                        feats[key].append(feature(key, k, pp, name))
                for key, name, pts in lines:
                    pp = to_parcel(clip_line(pts, box), centre, sx, sy, False)
                    if pp:
                        feats[key].append(feature(key, k, pp, name))
                for rank, cls, ref, pts in roads_k:
                    pp = to_parcel(clip_line(pts, box), centre, sx, sy, False)
                    if pp and (8, 190) in STYLE and k in STYLE[(8, 190)]:
                        feats[(8, 190)].append(feature((8, 190), k, pp, None, bytes([rank, cls, 0])))
                        if ref and (16, 195) in STYLE and k in STYLE[(16, 195)]:
                            p0 = pp[0][len(pp[0]) // 2]
                            lab = [[p0, (p0[0] - 2, p0[1] + 1)]]
                            feats[(16, 195)].append(feature((16, 195), k, lab, ref, b"\x96\x01\x00\x00"))
                if (2, 35) in STYLE and k in STYLE[(2, 35)]:
                    for name, x, y, pl in c.places:
                        if k >= 2 and pl not in ("city", "town"):
                            continue
                        if box[0] <= x < box[2] and box[1] <= y < box[3]:
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
                    cells[iy * lv["cnt_x"] + ix] = feawrite.encode_parcel(out)
        levels.append({"bbox": lv["bbox"], "parcel_w": lv["parcel_w"], "parcel_h": lv["parcel_h"],
                       "cnt_x": lv["cnt_x"], "cnt_y": lv["cnt_y"], "norm_x": lv["norm_x"],
                       "norm_y": lv["norm_y"], "feaType": lv["feaType"], "scales": lv["scales"],
                       "raw_parcels": cells})
        if progress:
            progress(k)
    return feawrite.encode_fea(factory_fea[:0x64], levels), stats
