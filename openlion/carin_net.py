"""OSM roads -> a CARiN road network: junction split, codes, levels, tiles, pieces.

See docs/29_vz_map_disc.md. Coordinates are absolute CARiN units (1.8e-7 degree, lon
offset +30), snapped to the 64-unit grid tiles use, and kept off tile edges.

Codes follow the factory disc (calibrated against OSM, report 29):
  class  high byte: bit 7 urban, low 7 bits a typical speed in m/s
         low byte:  bits 4-5 direction (0x10 one way A->B, 0x20 one way B->A),
                    low nibble road form (0 motorway, 4 dual carriageway, 1 slip road,
                    0xB rural road, 0xC urban road)
  group  0 motorway/trunk ... 4 local street (flags bits 8-10); level 1 holds groups
         0-1, level 2 0-2, level 3 0-3, detail everything
"""
import collections
import math
import unicodedata

from . import osm_import

OX, OY = 0x2A518800, 0xEE924000 - (1 << 32)    # world origin (root bbox)
WORLD = 0x18000000
Q = 64                                          # tile coordinate step
EDGE_GRID = 0xC000                              # every tile edge is a multiple of this

SKIP = {"track", "busway", "service"}


def to_carin(x5, y5):
    """deg * 1e5 -> snapped CARiN units, nudged off tile edges."""
    x = (x5 / 1e5 + 30.0) / 1.8e-7
    y = (y5 / 1e5) / 1.8e-7
    x = OX + Q * round((x - OX) / Q)
    y = OY + Q * round((y - OY) / Q)
    if (x - OX) % EDGE_GRID == 0:
        x += Q
    if (y - OY) % EDGE_GRID == 0:
        y += Q
    return x, y


NAME_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789 -'&/.,")


def ascii_name(s):
    """Lower-case ASCII as on the factory disc; other characters become spaces."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = "".join(ch if ch in NAME_CHARS else " " for ch in s)
    return " ".join(s.split())


def road_group(tags):
    return 4 - osm_import.road_rank(tags)


URBAN_NODES = 40      # junctions per 0xC000 cell (~1.5 km square) that make it built-up


def is_urban(tags, dense):
    sp = osm_import.parse_speed(tags.get("maxspeed"))
    if sp is not None and (sp <= 60 or sp >= 90):
        return sp <= 60
    return dense


def class_code(tags, direction, urban):
    hw = tags["highway"]
    sp = osm_import.parse_speed(tags.get("maxspeed"))
    link = hw.endswith("_link")
    if urban:
        speed = {"motorway": 0x20, "trunk": 0x11, "primary": 0x11}.get(hw, 0x0B if hw in (
            "secondary", "tertiary") or link else 0x06)
        if hw == "motorway" and sp and sp < 90:
            speed = 0x16
    else:
        if sp is None:
            sp = {"motorway": 110, "trunk": 100, "primary": 100, "secondary": 90}.get(hw, 80 if link else 70)
        speed = 0x20 if sp >= 110 else 0x1B if sp >= 100 else 0x16 if sp >= 80 else 0x11 if sp >= 60 else 0x0B
    if hw == "motorway":
        form = 0x0
    elif link:
        form = 0x1 if direction >= 0 else 0x2
    elif direction and hw in ("trunk", "primary", "secondary"):
        form = 0x4
    else:
        form = 0xC if urban else 0xB
    dbits = 0x10 if direction > 0 else 0x20 if direction < 0 else 0
    return ((0x80 if urban else 0) | speed) << 8 | dbits | form


class Net:
    """nodes: x, y (lists); segs: a, b, shape (list of (x, y) between), group, cls,
    name, ref, oneway (relative to a->b), urban."""

    def __init__(self):
        self.x, self.y = [], []
        self.a, self.b, self.shape = [], [], []
        self.group, self.cls, self.name, self.refs, self.urban = [], [], [], [], []

    def add_node(self, x, y):
        self.x.append(x)
        self.y.append(y)
        return len(self.x) - 1

    def add_seg(self, a, b, shape, tags, direction):
        self.a.append(a)
        self.b.append(b)
        self.shape.append(shape)
        self.group.append(road_group(tags))
        self.cls.append((tags, direction))      # finished by finish()
        self.name.append(ascii_name(tags.get("name", "")))
        self.refs.append(tuple(r for r in (ascii_name(p) for p in tags.get("ref", "").split(";")) if r))

    def finish(self):
        """Urban or rural from junction density, then the class codes."""
        dens = collections.Counter()
        deg = collections.Counter(self.a)
        deg.update(self.b)
        for n, d in deg.items():
            if d >= 3:
                dens[((self.x[n] - OX) // EDGE_GRID, (self.y[n] - OY) // EDGE_GRID)] += 1
        for s, (tags, direction) in enumerate(self.cls):
            n = self.a[s]
            dense = dens[((self.x[n] - OX) // EDGE_GRID, (self.y[n] - OY) // EDGE_GRID)] >= URBAN_NODES
            urban = is_urban(tags, dense)
            self.urban.append(urban)
            self.cls[s] = class_code(tags, direction, urban)

    @property
    def n_segs(self):
        return len(self.a)


# the database's world box in deg * 1e5 (Cocos Islands fall outside it, to the west)
WORLD_5 = (round((OX * 1.8e-7 - 30) * 1e5) + 1, round(OY * 1.8e-7 * 1e5) + 1,
           round(((OX + WORLD) * 1.8e-7 - 30) * 1e5) - 1, round((OY + WORLD) * 1.8e-7 * 1e5) - 1)


def build_net(ways, bbox=None, progress=None):
    """ways: [(wid, tags, WayPoints)] from the roads cache. bbox in deg*1e5 (x0,y0,x1,y1).
    Ways reaching outside the database's world box are left out."""
    keep = []
    for w in ways:
        tags, pts = w[1], w[2]
        if tags.get("highway") in SKIP or not osm_import.way_ok(tags):
            continue
        xy = pts.xy
        xs, ys = xy[0::2], xy[1::2]
        if min(xs) < WORLD_5[0] or max(xs) > WORLD_5[2] or min(ys) < WORLD_5[1] or max(ys) > WORLD_5[3]:
            continue
        if bbox:
            xy = pts.xy
            xs, ys = xy[0::2], xy[1::2]
            if max(xs) < bbox[0] or min(xs) > bbox[2] or max(ys) < bbox[1] or min(ys) > bbox[3]:
                continue
        keep.append(w)
    junctions = osm_import.junction_nodes(keep)
    net = Net()
    nid = {}

    def node(osm_id, x5, y5):
        k = nid.get(osm_id)
        if k is None:
            k = nid[osm_id] = net.add_node(*to_carin(x5, y5))
        return k

    for i, (_wid, tags, pts) in enumerate(keep):
        direction = osm_import.oneway_dir(tags)
        ids, xy = pts.ids, pts.xy
        n = len(ids)
        start = 0
        for k in range(1, n):
            if ids[k] in junctions or k == n - 1:
                a = node(ids[start], xy[2 * start], xy[2 * start + 1])
                b = node(ids[k], xy[2 * k], xy[2 * k + 1])
                shape = [to_carin(xy[2 * j], xy[2 * j + 1]) for j in range(start + 1, k)]
                if a == b:          # closed loop between one junction: split in two
                    if not shape:
                        start = k
                        continue
                    m = len(shape) // 2
                    mid = net.add_node(*shape[m])
                    net.add_seg(a, mid, shape[:m], tags, direction)
                    net.add_seg(mid, b, shape[m + 1:], tags, direction)
                else:
                    net.add_seg(a, b, shape, tags, direction)
                start = k
        if progress and i % 200000 == 0:
            progress(f"net: {i:,}/{len(keep):,} ways")
    net.finish()
    return net


# ---- quadtree tiles ------------------------------------------------------------

class Level:
    def __init__(self, num, table, max_group, cell, max_size, min_size, detail, budget):
        self.num, self.table, self.max_group = num, table, max_group
        self.cell, self.max_size, self.min_size = cell, max_size, min_size
        self.detail, self.budget = detail, budget
        self.leaves = {}          # (x0, y0, size) -> Tile build record
        self.cells = {}           # (cx, cy) -> list of leaf keys


def seg_weight(net, s, detail):
    return (32 if detail else 26) + 6 * len(net.shape[s]) + 20 + (24 if detail else 0)


def cells_on_line(x1, y1, x2, y2, m):
    """Grid cells (size m) a straight edge touches (Amanatides-Woo walk)."""
    cx, cy = x1 // m, y1 // m
    ex, ey = x2 // m, y2 // m
    out = [(cx, cy)]
    dx, dy = x2 - x1, y2 - y1
    sx = 1 if dx > 0 else -1
    sy = 1 if dy > 0 else -1
    tx = ((cx + (sx > 0)) * m - x1) / dx if dx else math.inf
    ty = ((cy + (sy > 0)) * m - y1) / dy if dy else math.inf
    ddx = m / abs(dx) if dx else math.inf
    ddy = m / abs(dy) if dy else math.inf
    for _ in range(abs(ex - cx) + abs(ey - cy) + 2):
        if (cx, cy) == (ex, ey):
            break
        if tx <= ty:
            if tx == ty:     # through a corner: count both side cells too
                out.append((cx + sx, cy))
                out.append((cx, cy + sy))
                cy += sy
                ty += ddy
            cx += sx
            tx += ddx
        else:
            cy += sy
            ty += ddy
        out.append((cx, cy))
    return out


def make_tiles(lines, lv, cover=()):
    """Quadtree leaves for a level from the weight of each polyline's points.
    cover: extra points that must fall inside some leaf even with no roads there (the
    centres of the finer level's tiles, so every finer tile has a parent, as on the
    factory disc)."""
    pts = collections.defaultdict(float)     # (x // min, y // min) -> weight
    m = lv.min_size
    for x, y in cover:
        pts[((x - OX) // m, (y - OY) // m)] += 0.001
    base = (32 + 20 + 24) if lv.detail else (26 + 20)
    for allp in lines:
        w = (base + 6 * (len(allp) - 2)) / len(allp)
        for x, y in allp:
            pts[((x - OX) // m, (y - OY) // m)] += w
        # every cell a road passes over must end up inside a tile
        for (x1, y1), (x2, y2) in zip(allp, allp[1:]):
            for cell in cells_on_line(x1 - OX, y1 - OY, x2 - OX, y2 - OY, m):
                pts[cell] += 0.001
    # aggregate upward
    def weight(x0, y0, size):
        n = size // m
        gx, gy = (x0 - OX) // m, (y0 - OY) // m
        if n == 1:
            return pts.get((gx, gy), 0.0)
        return sum(v for (i, j), v in pts.items() if gx <= i < gx + n and gy <= j < gy + n)

    by_top = collections.defaultdict(dict)
    for (i, j), v in pts.items():
        k = lv.max_size // m
        by_top[(i // k, j // k)][(i, j)] = v

    leaves = []

    def split(x0, y0, size, cellpts):
        tot = sum(cellpts.values())
        if tot == 0:
            return
        if tot <= lv.budget or size <= m:
            leaves.append((x0, y0, size))
            return
        h = size // 2
        for dx in (0, h):
            for dy in (0, h):
                gx, gy = (x0 + dx - OX) // m, (y0 + dy - OY) // m
                n = h // m
                sub = {k: v for k, v in cellpts.items() if gx <= k[0] < gx + n and gy <= k[1] < gy + n}
                split(x0 + dx, y0 + dy, h, sub)

    for (ti, tj), cellpts in by_top.items():
        split(OX + ti * lv.max_size, OY + tj * lv.max_size, lv.max_size, cellpts)
    del weight
    return leaves


class Locator:
    """Point -> leaf tile key, for one level's leaves (a partition of the covered area)."""

    def __init__(self, leaves, max_size, min_size):
        self.max, self.min = max_size, min_size
        self.top = {}
        for x0, y0, size in leaves:
            self.top.setdefault(((x0 - OX) // max_size, (y0 - OY) // max_size), {})[(x0, y0, size)] = 1
        self.leaves = set(leaves)

    def find(self, x, y):
        ti, tj = (x - OX) // self.max, (y - OY) // self.max
        size = self.max
        x0, y0 = OX + ti * size, OY + tj * size
        while size >= self.min:
            k = (x0, y0, size)
            if k in self.leaves:
                return k
            size //= 2
            x0 += size if x - x0 >= size else 0
            y0 += size if y - y0 >= size else 0
        return None


def clip(points, loc):
    """Polyline (snapped abs points) -> [(tile, [points])] pieces, consecutive pieces
    meeting at a crossing point on the shared edge (snapped to the 64 grid)."""
    out = []
    cur = loc.find(*points[0])
    piece = [points[0]]
    for q in points[1:]:
        p = piece[-1]
        while True:
            x0, y0, s = cur
            x1, y1 = x0 + s, y0 + s
            if x0 <= q[0] < x1 and y0 <= q[1] < y1:
                piece.append(q)
                break
            dx, dy = q[0] - p[0], q[1] - p[1]
            t = 1.0
            if dx > 0:
                t = min(t, (x1 - p[0]) / dx)
            elif dx < 0:
                t = min(t, (x0 - p[0]) / dx)
            if dy > 0:
                t = min(t, (y1 - p[1]) / dy)
            elif dy < 0:
                t = min(t, (y0 - p[1]) / dy)
            cx, cy = p[0] + dx * t, p[1] + dy * t
            # snap: the axis that hit the edge is exact, the other to the 64 grid
            ex = x1 if dx > 0 and abs(cx - x1) < 1e-6 * s + 1 else x0 if dx < 0 and abs(cx - x0) < 1e-6 * s + 1 else None
            ey = y1 if dy > 0 and abs(cy - y1) < 1e-6 * s + 1 else y0 if dy < 0 and abs(cy - y0) < 1e-6 * s + 1 else None
            cx = ex if ex is not None else OX + Q * round((cx - OX) / Q)
            cy = ey if ey is not None else OY + Q * round((cy - OY) / Q)
            cx = min(max(cx, x0), x1)
            cy = min(max(cy, y0), y1)
            c = (int(cx), int(cy))
            piece.append(c)
            out.append((cur, piece))
            # next tile: step a little past the crossing along the edge direction
            n = math.hypot(dx, dy)
            nxt = loc.find(int(c[0] + (1 if dx > 0 else -1 if dx < 0 else 0) * max(1, abs(dx) / n * 4)),
                           int(c[1] + (1 if dy > 0 else -1 if dy < 0 else 0) * max(1, abs(dy) / n * 4)))
            if nxt is None:
                raise ValueError(f"no tile at {c}")
            if nxt == cur:
                # numeric corner case: the far point is in this tile after all
                piece = out.pop()[1][:-1]
                piece.append(q)
                break
            cur = nxt
            piece = [c]
            p = c
    out.append((cur, piece))
    return out
