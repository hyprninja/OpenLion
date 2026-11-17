"""OSM roads -> a CARiN road network: junction split, codes, levels, tiles, pieces.

See docs/29_vz_map_disc.md. Coordinates are absolute CARiN units (1.8e-7 degree, lon
offset +30), snapped to the 64-unit grid tiles use, and kept off tile edges.

Codes follow the factory disc (calibrated against OSM, report 29):
  class  high byte: bit 7 urban, low 7 bits a typical speed in m/s
         low byte:  bits 4-5 direction (0x10 one way A->B, 0x20 one way B->A),
                    low nibble road form (0 motorway, 4 dual carriageway, 1 slip road,
                    0xB rural road, 0xC urban road)
  group  0-1 major roads, 2 rural secondary, 3 urban secondary, 4 tertiary and local
         (flags bits 8-10); level 1 holds groups 0-1, level 2 0-2, level 3 0-3, detail
         everything
"""
import collections
import math
import unicodedata

from . import osm_import

OX, OY = 0x2A518800, 0xEE924000 - (1 << 32)    # world origin (root bbox)
WORLD = 0x18000000
Q = 64                                          # tile coordinate step
EDGE_GRID = 0x6000                              # every tile edge is a multiple of this (the
                                                # smallest tile is 0xC000 x 0x6000)

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


# The factory's strings use a-z, 0-9, space, ' . and / only (4.9 M bytes, doc 33)
NAME_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789 './")
MAX_LABEL = 43          # the factory's longest map label


def ascii_name(s):
    """Lower-case ASCII as on the factory disc; & becomes 'and', other characters spaces;
    names longer than the factory's longest label are cut at a word boundary."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ")
    s = "".join(ch if ch in NAME_CHARS else " " for ch in s)
    s = " ".join(s.split())
    if len(s) > MAX_LABEL:
        s = s[:MAX_LABEL + 1].rsplit(" ", 1)[0] if " " in s[:MAX_LABEL + 1] else s[:MAX_LABEL]
    return s


ROUTE = __import__("re").compile(r"[a-z]{1,2}[0-9]{1,3}")


def route_refs(ref):
    """OSM ref -> route numbers as the factory writes them: a shield code and the number,
    at most 4 characters ('m1', 'a10', 'b100', 'ni87'). Bare numbers and anything else
    (lane counts, URLs, 'acw' ...) have no shield on the factory disc and are left out."""
    out = []
    for part in ref.split(";"):
        r = ascii_name(part).replace(" ", "")
        if len(r) <= 4 and ROUTE.fullmatch(r) and r not in out:
            out.append(r)
    return tuple(out)


def road_group(tags):
    return 4 - osm_import.road_rank(tags)


URBAN_NODES = 40      # junctions per DENSITY_CELL (~1.5 km square) that make it built-up
DENSITY_CELL = 0xC000


def is_urban(tags, dense):
    sp = osm_import.parse_speed(tags.get("maxspeed"))
    if sp is not None and (sp <= 60 or sp >= 90):
        return sp <= 60
    return dense


SLIP_TYPE = {0x1: 1, 0x8: 1, 0x2: 2, 0x9: 2, 0x3: 3, 0xA: 3}


def class_code(tags, direction, urban, slip_form=None):
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
        form = slip_form if slip_form is not None else (0xC if urban else 0xB)
    elif direction and hw in ("trunk", "primary", "secondary"):
        form = 0x4
    else:
        form = 0xC if urban else 0xB
    dbits = 0x10 if direction > 0 else 0x20 if direction < 0 else 0
    return factory_class(((0x80 if urban else 0) | speed) << 8 | dbits | form)


# Every road class code on the factory disc (all four levels): high byte -> low bytes.
# A generated code outside this set is moved to the nearest one the unit is known to use.
FACTORY_CLASSES = {
    0x03: (0x3C, 0x3E, 0x0E), 0x0B: (0x1C, 0x0C),
    0x11: (0x11, 0x12, 0x13, 0x14, 0x18, 0x19, 0x1A, 0x1B, 0x1C, 0x1D, 0x21, 0x22, 0x23, 0x24,
           0x28, 0x29, 0x2A, 0x2B, 0x2C, 0x0B, 0x0D),
    0x16: (0x10, 0x11, 0x12, 0x13, 0x14, 0x18, 0x19, 0x1A, 0x1B, 0x1C, 0x20, 0x21, 0x22, 0x23,
           0x24, 0x28, 0x29, 0x2A, 0x2B, 0x2C, 0x0B, 0x0D),
    0x1B: (0x10, 0x11, 0x12, 0x13, 0x14, 0x18, 0x19, 0x1A, 0x1B, 0x20, 0x21, 0x22, 0x23, 0x24,
           0x28, 0x29, 0x2B, 0x03, 0x0A, 0x0B),
    0x20: (0x10, 0x14, 0x1B, 0x20, 0x24, 0x2B, 0x0B),
    0x83: (0x3C, 0x0E),
    0x86: (0x01, 0x11, 0x12, 0x13, 0x18, 0x19, 0x1A, 0x1B, 0x1C, 0x1D, 0x21, 0x22, 0x23, 0x28,
           0x29, 0x2A, 0x2B, 0x2C, 0x2D, 0x4C, 0x51, 0x52, 0x5C, 0x61, 0x62, 0x6C, 0x0C, 0x0D),
    0x8B: (0x11, 0x12, 0x13, 0x18, 0x19, 0x1A, 0x1B, 0x1C, 0x1D, 0x21, 0x22, 0x23, 0x24, 0x28,
           0x29, 0x2A, 0x2B, 0x2C, 0x2D, 0x4C, 0x51, 0x52, 0x53, 0x59, 0x5C, 0x61, 0x62, 0x63,
           0x68, 0x6C, 0x08, 0x0A, 0x0B, 0x0C, 0x0D),
    0x91: (0x14, 0x1C, 0x24, 0x2C, 0x04, 0x54, 0x64),
    0x96: (0x10, 0x20),
    0x9B: (0x11, 0x12, 0x13, 0x19, 0x1A, 0x1C, 0x21, 0x22, 0x23, 0x2A, 0x2C, 0x52, 0x53, 0x62, 0x63),
    0xA0: (0x10, 0x14, 0x1B, 0x20, 0x24, 0x2B, 0x04, 0x50, 0x5B, 0x60, 0x6B, 0x0B),
}


def factory_class(code):
    """The code itself if the factory uses it, else the closest one it does use. Direction
    never changes (a two-way road stays two-way), except that a slip road with no direction
    becomes one-way forwards; the road's form is kept before its speed; speeds only move to
    the nearest one of the same urban/rural kind."""
    hi, lo = code >> 8, code & 0xFF
    if lo in FACTORY_CLASSES.get(hi, ()):
        return code
    dbits, form = lo & 0x30, lo & 0x0F
    if form in SLIP_TYPE and not dbits:
        dbits = 0x10
    # a slip road keeps its slip type (entry 1/8, exit 2/9, connector 3/A) before anything else
    forms = {0x0: (0x0, 0x4, 0xB, 0xC), 0x4: (0x4, 0xC, 0xB),
             0x1: (0x1, 0x8, 0xC, 0xB), 0x8: (0x8, 0x1, 0xC, 0xB),
             0x2: (0x2, 0x9, 0xC, 0xB), 0x9: (0x9, 0x2, 0xC, 0xB),
             0x3: (0x3, 0xA, 0xC, 0xB), 0xA: (0xA, 0x3, 0xC, 0xB),
             0xB: (0xB, 0xC, 0x4), 0xC: (0xC, 0xB, 0x4)}.get(form, (form, 0xC, 0xB))
    urban = hi & 0x80
    speeds = sorted((h for h in FACTORY_CLASSES if (h & 0x80) == urban),
                    key=lambda h: (abs((h & 0x7F) - (hi & 0x7F)), h))
    for f in forms:
        for h in speeds:
            if (dbits | f) in FACTORY_CLASSES[h]:
                return h << 8 | dbits | f
    raise ValueError(f"no factory class near {code:#06x}")


class Net:
    """nodes: x, y (lists); segs: a, b, shape (list of (x, y) between), group, cls,
    name, ref, oneway (relative to a->b), urban."""

    def __init__(self):
        self.x, self.y = [], []
        self.a, self.b, self.shape = [], [], []
        self.group, self.cls, self.name, self.refs, self.urban = [], [], [], [], []
        self.hw, self.x24 = [], []

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
        self.hw.append(tags.get("highway", ""))
        self.name.append(ascii_name(tags.get("name", "")))
        self.refs.append(route_refs(tags.get("ref", "")))

    def finish(self):
        """Urban or rural from junction density, then the class codes."""
        dens = collections.Counter()
        deg = collections.Counter(self.a)
        deg.update(self.b)
        for n, d in deg.items():
            if d >= 3:
                dens[((self.x[n] - OX) // DENSITY_CELL, (self.y[n] - OY) // DENSITY_CELL)] += 1
        forms = self.slip_forms()
        for s, (tags, direction) in enumerate(self.cls):
            n = self.a[s]
            dense = dens[((self.x[n] - OX) // DENSITY_CELL, (self.y[n] - OY) // DENSITY_CELL)] >= URBAN_NODES
            urban = is_urban(tags, dense)
            self.urban.append(urban)
            # level groups, matched to the factory's density (level 1 ~35 k roads, level 2
            # ~67 k, level 3 ~124 k nationwide): 0-1 from level 1 on, rural secondary roads
            # (2) from level 2, urban secondary roads (3) from level 3, tertiary and local
            # streets (4) on the street level only
            g = self.group[s]
            self.group[s] = 3 if (g == 2 and urban) else 4 if g >= 3 else g
            self.cls[s] = class_code(tags, direction, urban, forms.get(s))
            # word +24, bits 8-9: the slip-road type, 100% of the factory's slip roads
            self.x24.append(SLIP_TYPE.get(self.cls[s] & 0xF, 0) << 8)

    def slip_forms(self):
        """OSM *_link roads -> the factory's slip-road form from where they lead (doc 32):
        1 onto a motorway, 2 off one, 3 motorway to motorway; 8, 9, A the same for a
        divided road; None (an ordinary road) when neither end meets one."""
        ends = collections.defaultdict(list)
        for s in range(len(self.a)):
            ends[self.a[s]].append(s)
            ends[self.b[s]].append(s)
        link = lambda s: self.hw[s].endswith("_link")
        divided = lambda s: (not link(s) and self.cls[s][1] != 0
                             and self.hw[s] in ("trunk", "primary", "secondary"))

        def reach(n, came):
            """Kinds of road met walking along links from node n (away from seg came)."""
            seen, kinds = {came}, set()
            stack = [n]
            for _ in range(200):
                if not stack:
                    break
                n = stack.pop()
                for o in ends[n]:
                    if o in seen:
                        continue
                    seen.add(o)
                    if link(o):
                        stack.append(self.b[o] if self.a[o] == n else self.a[o])
                    elif self.hw[o] == "motorway":
                        kinds.add("m")
                    elif divided(o):
                        kinds.add("d")
            return kinds
        out = {}
        for s in range(len(self.a)):
            if not link(s):
                continue
            direction = self.cls[s][1]
            up, down = (self.a[s], self.b[s]) if direction >= 0 else (self.b[s], self.a[s])
            ku, kd = reach(up, s), reach(down, s)
            if "m" in ku or "m" in kd:
                out[s] = 0x3 if "m" in ku and "m" in kd else 0x1 if "m" in kd else 0x2
            elif "d" in ku or "d" in kd:
                out[s] = 0xA if "d" in ku and "d" in kd else 0x8 if "d" in kd else 0x9
        return out

    @property
    def n_segs(self):
        return len(self.a)


# the database's world box in deg * 1e5 (Cocos Islands fall outside it, to the west)
WORLD_5 = (round((OX * 1.8e-7 - 30) * 1e5) + 1, round(OY * 1.8e-7 * 1e5) + 1,
           round(((OX + WORLD) * 1.8e-7 - 30) * 1e5) - 1, round((OY + WORLD) * 1.8e-7 * 1e5) - 1)


MAX_SEG_SHAPE = 400     # longer OSM roads get pass-through nodes (factory: 588 at most)


def build_net(ways, bbox=None, progress=None, coverage=None):
    """ways: [(wid, tags, WayPoints)] from the roads cache. bbox in deg*1e5 (x0,y0,x1,y1).
    Ways reaching outside the database's world box are left out, and so are ways entirely
    outside `coverage` (deg*1e5 box: where the factory has street-level data; it has none
    on Christmas, Norfolk or Lord Howe Island)."""
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
            if max(xs) < bbox[0] or min(xs) > bbox[2] or max(ys) < bbox[1] or min(ys) > bbox[3]:
                continue
        if coverage:
            if max(xs) < coverage[0] or min(xs) > coverage[2] or max(ys) < coverage[1] or min(ys) > coverage[3]:
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
                    while len(shape) > MAX_SEG_SHAPE:
                        m = MAX_SEG_SHAPE // 2
                        mid = net.add_node(*shape[m])
                        net.add_seg(a, mid, shape[:m], tags, direction)
                        a, shape = mid, shape[m + 1:]
                    net.add_seg(a, b, shape, tags, direction)
                start = k
        if progress and i % 200000 == 0:
            progress(f"net: {i:,}/{len(keep):,} ways")
    drop_zero_length(net)
    net.finish()
    return net


def drop_zero_length(net):
    """Two junctions that snap to the same point with nothing between them make a road of
    no length, which the factory never has: such pairs become one node."""
    parent = list(range(len(net.x)))

    def find(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n
    zero = set()
    for s in range(len(net.a)):
        a, b = net.a[s], net.b[s]
        if not net.shape[s] and (net.x[a], net.y[a]) == (net.x[b], net.y[b]):
            zero.add(s)
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra
    if not zero:
        return
    keep = [s for s in range(len(net.a)) if s not in zero]
    for name in ("a", "b"):
        col = getattr(net, name)
        setattr(net, name, [find(col[s]) for s in keep])
    for name in ("shape", "group", "cls", "name", "refs", "hw"):
        col = getattr(net, name)
        setattr(net, name, [col[s] for s in keep])
    # a road that now starts and ends at the merged node: gone if it has no shape, else
    # split in two at its middle point (the factory has no road whose ends meet)
    drop = set()
    for s in range(len(net.a)):
        if net.a[s] != net.b[s]:
            continue
        drop.add(s)
        sh = net.shape[s]
        if sh:
            m = len(sh) // 2
            mid = net.add_node(*sh[m])
            for a, b, part in ((net.a[s], mid, sh[:m]), (mid, net.b[s], sh[m + 1:])):
                net.a.append(a)
                net.b.append(b)
                net.shape.append(part)
                for name in ("group", "cls", "name", "refs", "hw"):
                    getattr(net, name).append(getattr(net, name)[s])
    if drop:
        for name in ("a", "b", "shape", "group", "cls", "name", "refs", "hw"):
            col = getattr(net, name)
            setattr(net, name, [v for s, v in enumerate(col) if s not in drop])


# ---- quadtree tiles ------------------------------------------------------------

class Level:
    def __init__(self, num, table, max_group, cell, max_size, min_size, detail, budget):
        self.num, self.table, self.max_group = num, table, max_group
        self.cell, self.max_size, self.min_size = cell, max_size, min_size
        self.detail, self.budget = detail, budget
        self.leaves = {}          # (x0, y0, w, h) -> Tile build record
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


# Tiles are split the way the factory splits them: a square into two halves stacked one
# above the other (each twice as wide as tall), and such a half into two squares side by
# side. Every factory tile is a square or a 2:1 rectangle aligned to its own short side
# (7,220 squares, 2,378 rectangles); four-way splits made twice as many, half-full tiles.
# A tile is (x0, y0, w, h).

def halves(rect):
    x0, y0, w, h = rect
    if w == h:
        return (x0, y0, w, h // 2), (x0, y0 + h // 2, w, h // 2)
    return (x0, y0, w // 2, h), (x0 + w // 2, y0, w // 2, h)


def can_split(rect, min_size):
    """The smallest tile is min_size wide and min_size / 2 tall."""
    _x0, _y0, w, h = rect
    return h >= min_size if w == h else w // 2 >= min_size


# The factory's largest counts per tile (tools/carin_envelope.py): shape points 1,532 on
# the street level, 494-702 on levels 1-3; places (suburb records) 12 including the blank
# and the fence, so 10 real ones.
SHAPE_CAP = {6: 1500, 3: 690, 2: 690, 1: 490}
PLACE_CAP = 10
# A coarse tile's child list (s8) is a slot grid: (w / slot) x (h / slot) slots, slot =
# the shortest side of any finer tile under it (header 0x5C). The factory never has more
# than 256 slots on level 3 or 128 on levels 1 and 2, so a coarse tile also splits when
# the finer tiles under it would need more.
SLOT_CAP = {3: 256, 2: 128, 1: 128}


def make_tiles(lines, lv, cover=(), places=None, finer_min=None, finer=None):
    """KD-tree leaves for a level from the weight of each polyline's points.
    cover: extra points that must fall inside some leaf even with no roads there (the
    centres of the finer level's tiles, so every finer tile has a parent, as on the
    factory disc)."""
    pts = collections.defaultdict(float)     # (x // m, y // m) -> weight
    shp = collections.Counter()              # shape points per cell
    plc = collections.defaultdict(set)       # place ids per cell (street level)
    m = lv.min_size // 2                     # the smallest tile's short side
    for x, y in cover:
        pts[((x - OX) // m, (y - OY) // m)] += 0.001
    # calibrated on the Victoria build: about 50 bytes a road on the street level
    # (record, node, name entries), 38 on overview levels, 6 a shape point
    base = 52 if lv.detail else 38
    lines = list(lines)
    for li, allp in enumerate(lines):
        w = (base + 6 * (len(allp) - 2)) / len(allp)
        for x, y in allp:
            pts[((x - OX) // m, (y - OY) // m)] += w
        for x, y in allp[1:-1]:
            shp[((x - OX) // m, (y - OY) // m)] += 1
        if places is not None and places[li] is not None:
            x, y = allp[len(allp) // 2]
            plc[((x - OX) // m, (y - OY) // m)].add(places[li])
        # every cell a road passes over must end up inside a tile
        for (x1, y1), (x2, y2) in zip(allp, allp[1:]):
            for cell in cells_on_line(x1 - OX, y1 - OY, x2 - OX, y2 - OY, m):
                pts[cell] += 0.001

    by_top = collections.defaultdict(dict)
    k = lv.max_size // m
    for (i, j), v in pts.items():
        by_top[(i // k, j // k)][(i, j)] = v

    leaves = []
    # nesting: a coarse tile is never split through a finer tile, so every finer tile lies
    # inside one coarse tile (factory: all 6,442 street tiles inside their parent)
    fin = collections.defaultdict(list)
    for r in finer or ():
        fin[((r[0] - OX) // lv.max_size, (r[1] - OY) // lv.max_size)].append(r)

    def cuts(rect):
        x0, y0, w, h = rect
        for fx, fy, fw, fh in fin.get(((x0 - OX) // lv.max_size, (y0 - OY) // lv.max_size), ()):
            if fx < x0 + w and x0 < fx + fw and fy < y0 + h and y0 < fy + fh:
                if (w == h and fh > h // 2) or (w != h and fw > w // 2):
                    return True
        return False

    shape_cap = SHAPE_CAP.get(lv.num, 1500)

    slot_cap = SLOT_CAP.get(lv.num)

    def fits(cellpts, rect):
        if sum(cellpts.values()) > lv.budget:
            return False
        if finer_min and slot_cap:
            v = min((finer_min[c] for c in cellpts if c in finer_min), default=None)
            if v and (rect[2] // v) * (rect[3] // v) > slot_cap:
                return False
        if sum(shp.get(c, 0) for c in cellpts) > shape_cap:
            return False
        if places is not None:
            seen = set()
            for c in cellpts:
                seen |= plc.get(c, set())
                if len(seen) > PLACE_CAP:
                    return False
        return True

    def split(rect, cellpts):
        if not cellpts:
            return
        if fits(cellpts, rect) or not can_split(rect, lv.min_size) or cuts(rect):
            leaves.append(rect)
            return
        for half in halves(rect):
            hx, hy, hw, hh = half
            gx, gy = (hx - OX) // m, (hy - OY) // m
            nx, ny = hw // m, hh // m
            sub = {c: v for c, v in cellpts.items() if gx <= c[0] < gx + nx and gy <= c[1] < gy + ny}
            split(half, sub)

    for (ti, tj), cellpts in by_top.items():
        split((OX + ti * lv.max_size, OY + tj * lv.max_size, lv.max_size, lv.max_size), cellpts)
    return leaves


class Locator:
    """Point -> leaf tile, for one level's leaves (a partition of the covered area)."""

    def __init__(self, leaves, max_size, min_size):
        self.max, self.min = max_size, min_size
        self.leaves = set(leaves)

    def find(self, x, y):
        ti, tj = (x - OX) // self.max, (y - OY) // self.max
        rect = (OX + ti * self.max, OY + tj * self.max, self.max, self.max)
        while True:
            if rect in self.leaves:
                return rect
            if not can_split(rect, self.min):
                return None
            a, b = halves(rect)
            rect = a if (a[0] <= x < a[0] + a[2] and a[1] <= y < a[1] + a[3]) else b


def clip(points, loc):
    """Polyline (snapped abs points) -> [(tile, [points])] pieces, consecutive pieces
    meeting at a crossing point on the shared edge (snapped to the 64 grid).

    A crossing is always computed from the original segment it lies on (not from the last
    crossing), so the same segment crossing the same edge line gives the same point on
    every level: the coarse levels are clipped from the street level's own geometry, and
    their edge nodes then sit exactly on street-level nodes, as on the factory (doc 31)."""
    out = []
    cur = loc.find(*points[0])
    piece = [points[0]]
    for a, q in zip(points, points[1:]):
        while True:
            x0, y0, w, h = cur
            x1, y1 = x0 + w, y0 + h
            if x0 <= q[0] < x1 and y0 <= q[1] < y1:
                piece.append(q)
                break
            dx, dy = q[0] - a[0], q[1] - a[1]
            t = 1.0
            if dx > 0:
                t = min(t, (x1 - a[0]) / dx)
            elif dx < 0:
                t = min(t, (x0 - a[0]) / dx)
            if dy > 0:
                t = min(t, (y1 - a[1]) / dy)
            elif dy < 0:
                t = min(t, (y0 - a[1]) / dy)
            cx, cy = a[0] + dx * t, a[1] + dy * t
            # snap: the axis that hit the edge is exact, the other to the 64 grid
            ex = x1 if dx > 0 and abs(cx - x1) < 1e-6 * w + 1 else x0 if dx < 0 and abs(cx - x0) < 1e-6 * w + 1 else None
            ey = y1 if dy > 0 and abs(cy - y1) < 1e-6 * h + 1 else y0 if dy < 0 and abs(cy - y0) < 1e-6 * h + 1 else None
            cx = ex if ex is not None else OX + Q * round((cx - OX) / Q)
            cy = ey if ey is not None else OY + Q * round((cy - OY) / Q)
            cx = min(max(cx, x0), x1)
            cy = min(max(cy, y0), y1)
            c = (int(cx), int(cy))
            grazed = len(piece) == 1 and c == piece[0]   # only touched this tile at a corner
            if not grazed:
                piece.append(c)
                out.append((cur, piece))
            # next tile: step a little past the crossing along the segment
            n = math.hypot(dx, dy)
            nxt = loc.find(int(c[0] + (1 if dx > 0 else -1 if dx < 0 else 0) * max(1, abs(dx) / n * 4)),
                           int(c[1] + (1 if dy > 0 else -1 if dy < 0 else 0) * max(1, abs(dy) / n * 4)))
            if nxt is None:
                raise ValueError(f"no tile at {c}")
            if nxt == cur:
                # numeric corner case: the far point is in this tile after all
                if not grazed:
                    piece = out.pop()[1][:-1]
                piece.append(q)
                break
            cur = nxt
            piece = [c]
    out.append((cur, piece))
    return out
