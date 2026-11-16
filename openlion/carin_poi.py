"""OSM POIs for a CARiN disc: selection, road links, records (0x10), POI tiles (0x06),
the POI index (0x11) and the per-place category lists. See docs/29_vz_map_disc.md.

Input is the POI read cache of an OpenLion build (pois2_<country>.pickle): a list of
(iQ category, lon * 1e5, lat * 1e5, tags), categories as in osm_poi.RULES.

Every factory POI links to at least one street-level road (none has zero), so a POI is
only written if the road it actually sits on is on the disc:
  - the nearest drivable road in the whole OSM network, within MAX_LINK, is its road;
  - in the disc's state(s) every road is on the disc;
  - interstate, the POI is kept only if its own road is one of the major roads the
    state disc carries nationwide (a servo on the highway, not a cafe one street back).
"""
import collections
import math
import struct

from . import carin_net as cn

# iQ category (osm_poi.RULES) -> CARiN category (docs/29). Missing = left out.
CATEGORY = {
    1: 0x2F, 2: 0x3C, 3: 0x24, 4: 0x16, 5: 0x0C, 6: 0x15, 7: 0x2F, 8: 0x2F, 9: 0x17,
    10: 0x17, 11: 0x19, 12: 0x38, 13: 0x0E, 14: 0x37, 16: 0x23, 17: 0x32, 18: 0x30,
    19: 0x29, 20: 0x3C, 21: 0x2B, 22: 0x16, 23: 0x0D, 24: 0x2A, 25: 0x14, 26: 0x34,
    27: 0x38, 28: 0x33, 29: 0x39, 30: 0x20, 31: 0x14, 32: 0x31, 33: 0x25, 35: 0x26,
    36: 0x31, 38: 0x1F, 39: 0x28, 40: 0x1A, 41: 0x35, 42: 0x2C, 43: 0x26, 45: 0x26,
    46: 0x23, 47: 0x2E, 48: 0x2D, 49: 0x1D, 53: 0x2F, 61: 0x2F, 62: 0x2B, 65: 0x2F,
    66: 0x2F, 67: 0x15, 68: 0x2F, 69: 0x2F, 70: 0x2F,
}
# name for a POI OSM left unnamed, where the factory has the same habit ("petrol station 7")
DEFAULT_NAME = {0x0C: "petrol station", 0x0E: "car park", 0x17: "atm"}

MAX_LINK = 150 / 0.02          # 150 m in CARiN units (1 unit is about 2 cm)
CELL = 8192                    # road index cell (~165 m)
POI_RECORDS_PER_TILE = 850     # the factory's largest POI tile (24 KB at 28 B a record)
UNIT_BYTES = 24000
# when a smallest-size POI tile still holds too many, the most useful are kept
PRIORITY = (0x0C, 0x29, 0x2A, 0x2E, 0x34, 0x33, 0x35, 0x15, 0x1F, 0x0E, 0x0D, 0x2C, 0x30,
            0x17, 0x2D, 0x16, 0x39, 0x14, 0x20, 0x31, 0x24, 0x2B, 0x19, 0x38, 0x1A, 0x1D,
            0x28, 0x23, 0x26, 0x25, 0x27, 0x32, 0x37, 0x22, 0x21, 0x3C, 0x2F, 0x3D)


class Poi:
    __slots__ = ("cat", "x", "y", "name", "brand", "street", "house", "phone", "postcode",
                 "city", "place", "state", "seg", "link", "s0_unit", "s0_off")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def _clean(s):
    return cn.ascii_name(s or "")[:60]


def _phone(tags):
    p = tags.get("phone") or tags.get("contact:phone") or ""
    p = p.split(";")[0]
    digits = "".join(ch for ch in p if ch.isdigit())
    if not digits:
        return ""
    if digits.startswith("61"):
        return "+" + digits
    if digits.startswith("0"):
        return "+61" + digits[1:]
    return "+61" + digits


class RoadIndex:
    """Nearest drivable road to a point, over the whole network."""

    def __init__(self, net):
        self.net = net
        self.grid = collections.defaultdict(list)
        for s in range(net.n_segs):
            pts = self.points(s)
            seen = set()
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                n = int(max(abs(x2 - x1), abs(y2 - y1)) // (CELL // 2)) + 1
                for k in range(n + 1):
                    c = ((x1 + (x2 - x1) * k // n) // CELL, (y1 + (y2 - y1) * k // n) // CELL)
                    if c not in seen:
                        seen.add(c)
                        self.grid[c].append(s)

    def points(self, s):
        n = self.net
        return [(n.x[n.a[s]], n.y[n.a[s]])] + n.shape[s] + [(n.x[n.b[s]], n.y[n.b[s]])]

    def nearest(self, x, y):
        cx, cy = x // CELL, y // CELL
        best = (MAX_LINK, None)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for s in self.grid.get((cx + dx, cy + dy), ()):
                    d = dist_to_line(x, y, self.points(s))
                    if d < best[0]:
                        best = (d, s)
        return best[1]


def dist_to_line(x, y, pts):
    best = math.inf
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        vx, vy = x2 - x1, y2 - y1
        L = vx * vx + vy * vy
        t = 0.0 if L == 0 else max(0.0, min(1.0, ((x - x1) * vx + (y - y1) * vy) / L))
        d = math.hypot(x - (x1 + t * vx), y - (y1 + t * vy))
        if d < best:
            best = d
    return best


def select(pois, net, on_disc, point_labels, states, progress=None):
    """pois: the read cache. on_disc(seg) -> True if the road is in the street level.
    point_labels(x5, y5) -> (suburb, state). Returns [Poi]."""
    idx = RoadIndex(net)
    if progress:
        progress(f"POIs: road index over {net.n_segs:,} roads")
    out, dropped = [], collections.Counter()
    for k, (iq, x5, y5, tags) in enumerate(pois):
        cat = CATEGORY.get(iq)
        if cat is None:
            dropped["category not carried"] += 1
            continue
        if not (cn.WORLD_5[0] < x5 < cn.WORLD_5[2] and cn.WORLD_5[1] < y5 < cn.WORLD_5[3]):
            dropped["outside the world box"] += 1
            continue
        name = _clean(tags.get("name") or tags.get("brand") or tags.get("operator")) or DEFAULT_NAME.get(cat, "")
        if not name:
            dropped["no name"] += 1
            continue
        place, state = point_labels(x5, y5)
        x, y = cn.to_carin(x5, y5)
        seg = idx.nearest(x, y)
        if seg is None:
            dropped["no road within 150 m"] += 1
            continue
        if not on_disc(seg):
            dropped["its road isn't on this disc"] += 1
            continue
        home = states is None or state in states
        out.append(Poi(cat=cat, x=x, y=y, name=name, brand=_clean(tags.get("brand")),
                       street=_clean(tags.get("addr:street")), house=_clean(tags.get("addr:housenumber"))[:10],
                       phone=_phone(tags), postcode="", city=_clean(tags.get("addr:city")) or place,
                       place=place, state=state, seg=seg))
        if not home:
            dropped["kept: interstate, on a road the disc carries"] += 1
        if progress and k % 50000 == 0:
            progress(f"POIs: {k:,}/{len(pois):,}")
    return out, dropped


# ---- road links -----------------------------------------------------------------

def link(pois, tiles, chain_of):
    """For each POI, the street-level piece of its road nearest to it: (tile key, road
    offset). chain_of(seg) -> the first segment of the merged street-level road holding it."""
    by_chain = collections.defaultdict(list)
    for key, rec in tiles.items():
        if not rec.level.detail:
            continue
        for i, p in enumerate(rec.pieces):
            by_chain[p.seg].append((key, rec.offs[4] + 32 * i, p.pts))
    missing = 0
    for poi in pois:
        cands = by_chain.get(chain_of(poi.seg), ())
        if not cands:
            missing += 1
            continue
        key, off, _pts = min(cands, key=lambda c: dist_to_line(poi.x, poi.y, c[2]))
        poi.link = (key, off)
    return missing


# ---- 0x10 POI units ---------------------------------------------------------------

class PoiUnit:
    """0x10: name records (s0), 40-byte details (s1), road links (s4), strings."""

    def __init__(self, key):
        self.key, self.table = key, 0x10
        self.pois = []
        self.next = self.prev = None
        self._lay = None

    def strings(self):
        pool, out = {}, bytearray()
        for p in self.pois:
            for s in (p.name, p.place, p.brand, p.street, p.city, p.state, "australia", p.house,
                      p.phone, p.postcode):
                if s and s not in pool:
                    pool[s] = len(out)
                    out += s.encode("ascii", "replace") + b"\0"
        return pool, bytes(out)

    def layout(self):
        if self._lay is None:
            n = len(self.pois)
            s1 = 0x28 + 8 * n
            s4 = s1 + 40 * n
            sstart = s4 + 8 * n
            self._lay = (n, s1, s4, sstart, self.strings())
        return self._lay

    def size_bytes(self):
        n, s1, s4, sstart, (pool, sb) = self.layout()
        return sstart + len(sb)

    def logical(self, addr, unit_header):
        n, s1, s4, sstart, (pool, sb) = self.layout()
        out = bytearray(sstart)
        out[:8] = unit_header
        struct.pack_into(">HHHHHHHHHH", out, 8, 0x28, n, s1, n, 0, 0, 0, 0, s4, n)
        struct.pack_into(">II", out, 0x20, addr(self.next) if self.next else 0, addr(self.prev) if self.prev else 0)
        sp = lambda s: sstart + pool[s] if s else 0
        for i, p in enumerate(self.pois):
            struct.pack_into(">HHHH", out, 0x28 + 8 * i, sp(p.name), 0x0002, sp(p.place), s1 + 40 * i)
            struct.pack_into(">iiHHHHHHHIHHHHHHH", out, s1 + 40 * i, p.x, p.y, s4 + 8 * i, 1,
                             sp(p.brand), sp(p.street), sp(p.city), sp(p.state), sp("australia"),
                             0, sp(p.house), 0, 0, sp(p.phone), sp(p.postcode), 0, 0)
            tile, roff = p.link
            struct.pack_into(">IHH", out, s4 + 8 * i, addr(tile), roff, 0)
        return bytes(out) + sb


def pack_units(leaves):
    """leaves: [[Poi]] in index order; a leaf (a run the index points at) never splits
    across units. Sets each POI's name-record unit and offset; returns the units."""
    units = []
    cur, size = None, 0
    for leaf in leaves:
        est = sum(8 + 40 + 8 + 24 + sum(len(s) + 1 for s in (p.name, p.place, p.brand, p.street,
                                                              p.house, p.phone) if s) for p in leaf)
        if cur is None or (size + est > UNIT_BYTES and cur.pois):
            cur = PoiUnit(("poi", len(units)))
            units.append(cur)
            size = 0x28 + 64
        for p in leaf:
            p.s0_unit, p.s0_off = cur.key, 0x28 + 8 * len(cur.pois)
            cur.pois.append(p)
        size += est
    for a, b in zip(units, units[1:]):
        a.next, b.prev = b.key, a.key
    return units


def build(net, tiles, on_disc, chain_of, point_labels, states, cache, cell, progress=None):
    """-> (units, POI tile keys, {(place, state): [(category, (trie unit, offset, count))]},
    report). The POI grid is made by the caller from the tile keys."""
    from . import carin_search as cs
    pois, report = select(cache, net, on_disc, point_labels, states, progress)
    missing = link(pois, tiles, chain_of)
    pois = [p for p in pois if p.link]
    report["no street-level piece found"] += missing
    # tiles first: a tile that can't be split below the grid's smallest cell keeps its
    # 850 most useful POIs, and the rest leave the disc entirely (map and search)
    ptiles, thinned = make_tiles(pois, cell, cell // 32)
    gone = {id(p) for p in thinned}
    pois = [p for p in pois if id(p) not in gone]
    report["thinned from over-full tiles"] = len(thinned)
    # index order: category, place, name; one trie per (place, category)
    groups = collections.defaultdict(list)
    for p in pois:
        groups[(p.cat, p.place, p.state)].append(p)
    trie = cs.TrieWriter(0x11, "t11")
    ordered = []                 # (group key, names, trie, leaves as poi lists)
    for gk in sorted(groups):
        grp = sorted(groups[gk], key=lambda p: (p.name, p.x, p.y))
        names = [p.name for p in grp]
        tr = cs.make_trie(names, 0, len(names))
        ordered.append((gk, grp, tr, [grp[lo:hi] for lo, hi in cs.leaves(tr)]))
    units = pack_units([leaf for _gk, _g, _tr, lv in ordered for leaf in lv])
    links = collections.defaultdict(list)
    for (cat, place, state), grp, tr, _lv in ordered:
        if not place:
            continue
        spans = {}
        for lo, hi in cs.leaves(tr):
            spans[(lo, hi)] = (grp[lo].s0_unit, grp[lo].s0_off, hi - lo)
        links[(place, state)].append((cat, trie.add(tr, spans.__getitem__)))
    report["written"] = len(pois)
    report["home state"] = sum(1 for p in pois if states is None or p.state in states)
    return units + trie.units + ptiles, [t.key for t in ptiles], links, report


# ---- 0x06 POI tiles ----------------------------------------------------------------

class PoiTile:
    """0x06: (0x20, n), u32 0, bbox, 28-byte records."""

    def __init__(self, rect):
        self.rect = rect
        self.key = ("poitile", rect)
        self.table = 0x06
        self.pois = []

    def size_bytes(self):
        return 0x20 + 28 * len(self.pois)

    def logical(self, addr, unit_header):
        x0, y0, s = self.rect
        out = bytearray(unit_header) + struct.pack(">HHI", 0x20, len(self.pois), 0)
        out += struct.pack(">iiii", x0, y0, x0 + s, y0 + s)
        for p in self.pois:
            out += struct.pack(">IHHHH", addr(p.s0_unit), p.s0_off, (p.x - x0) // cn.Q, (p.y - y0) // cn.Q, p.cat)
            out += bytes(16)            # 28-byte record
        return bytes(out)


def make_tiles(pois, cell, min_size):
    """Quadtree POI tiles inside each grid cell, at most POI_RECORDS_PER_TILE each."""
    by_cell = collections.defaultdict(list)
    for p in pois:
        by_cell[((p.x - cn.OX) // cell, (p.y - cn.OY) // cell)].append(p)
    tiles = []

    dropped = []

    def split(x0, y0, s, ps):
        if len(ps) <= POI_RECORDS_PER_TILE or s <= min_size:
            if len(ps) > POI_RECORDS_PER_TILE:       # can't split further: thin it
                rank = {c: i for i, c in enumerate(PRIORITY)}
                ps = sorted(ps, key=lambda p: (rank.get(p.cat, len(PRIORITY)), p.name))
                dropped.extend(ps[POI_RECORDS_PER_TILE:])
                ps = ps[:POI_RECORDS_PER_TILE]
            t = PoiTile((x0, y0, s))
            t.pois = sorted(ps, key=lambda p: (p.y, p.x))
            tiles.append(t)
            return
        h = s // 2
        for dx in (0, h):
            for dy in (0, h):
                sub = [p for p in ps if x0 + dx <= p.x < x0 + dx + h and y0 + dy <= p.y < y0 + dy + h]
                if sub:
                    split(x0 + dx, y0 + dy, h, sub)

    for (cx, cy), ps in by_cell.items():
        split(cn.OX + cx * cell, cn.OY + cy * cell, cell, ps)
    return tiles, dropped
