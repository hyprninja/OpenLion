"""Rebuild a VY/VZ map disc's road data from OpenStreetMap (CARiN carindb).

  python -m openlion.carin_build NAV_DB.iso OUT.iso [--roads roads2_AU.pickle]
         [--boundaries boundaries3_AU.pickle] [--bbox lon0 lat0 lon1 lat1]

Kept byte for byte from the user's disc: every file except carindb; in carindb the
volume header, metadata, the C-IQ licensing tables and the whole background block
(sea, land, water at six scales, with its grids). Rebuilt: road tiles at four levels,
house-number units, the road and POI grids, the root's layer pointers and the search
tables. POIs are left out (empty layer): the factory ones point at the old road tiles.
See docs/29_vz_map_disc.md.
"""
import argparse
import collections
import pickle
import struct
import sys
import time
import zlib

from . import carin
from . import carin_net as cn
from . import carin_tile as ct
from . import carin_write as cw

PAGE = carin.PAGE
# No house numbers yet: detail tiles carry no 0x04 unit (address 0), as 2,297 factory
# tiles do. Set True to write one "no numbers" record per road instead.
WITH_HOUSE_NUMBERS = False
# no unit on the factory disc unpacks to more than 32 KB (search units reach it exactly)
MAX_LOGICAL = 32768
# store street-level tiles uncompressed, as the factory does with every one it doesn't
# 0x0E-encode (502 stored, 2 zlib); off by default because it roughly triples their size
STORED_TILES = False
# detail-level shape tolerance in tile steps (1 step = 64 units, about 1.3 m)
DETAIL_SHAPE_TOL = 1.5
EMPTY = "empty"


def log(msg, t0=[time.time()]):
    print(f"[{time.time() - t0[0]:7.1f}s] {msg}", flush=True)


# ---- the factory database ------------------------------------------------------

class Factory:
    def __init__(self, db):
        self.db = db
        self.units = carin.units(db)
        self.by = {u.off: u for u in self.units}
        self.root = carin.decode(db, self.by[0xC00])
        self._grids = {}

    def raw(self, addr):
        u = self.by[(addr >> 8) << 8]
        return carin.decode(self.db, u) if u.codec != carin.CODEC_E else self.db[u.off:u.off + u.size]

    def layer(self, i):
        """Root entry i: (t08 address, level, scale)."""
        a, = struct.unpack_from(">I", self.root, 0x10 + 24 * i)
        lvl, sc = struct.unpack_from(">HH", self.root, 0x10 + 24 * i + 20)
        return a, lvl, sc

    def grid(self, i):
        """Root entry i -> (cell size, [t09 address per cell], [t08 units])."""
        a = self.layer(i)[0]
        off = (a >> 8) << 8
        cells, units = [], []
        cell = None
        while off in self.by and self.by[off].table == 0x08:
            b = carin.decode(self.db, self.by[off])
            s, c = struct.unpack_from(">HH", b, 8)
            cell = struct.unpack_from(">I", b, 12)[0]
            cells += struct.unpack_from(f">{c}I", b, s)
            units.append(off)
            off += self.by[off].size
            n = int(round(len(cells) ** 0.5))
            if cell and (carin_world() // cell) ** 2 == len(cells):
                break
        return cell, cells, units

    def tile_at(self, layer, x, y):
        cell, cells, _u = self.grid_cached(layer)
        n = carin_world() // cell
        cx, cy = (x - cn.OX) // cell, (y - cn.OY) // cell
        if not (0 <= cx < n and 0 <= cy < n):
            return None
        b = self.raw(cells[cx * n + cy])
        (o0, m), (_o1, k) = struct.unpack_from(">HH", b, 8), struct.unpack_from(">HH", b, 12)
        sub = struct.unpack_from(">I", b, 16)[0]
        if not k:
            return None
        sn = cell // sub
        ix, iy = (x - cn.OX - cx * cell) // sub, (y - cn.OY - cy * cell) // sub
        e = struct.unpack_from(">H", b, o0 + 2 * (ix * sn + iy))[0]
        return struct.unpack_from(">I", b, e)[0] if e else None

    def grid_cached(self, i):
        if i not in self._grids:
            self._grids[i] = self.grid(i)
        return self._grids[i]

    def background_start(self):
        """Lowest offset of anything reachable from the background layers."""
        lo = None
        for i in (5, 6, 7, 9, 10, 11):
            _cell, cells, units = self.grid(i)
            for a in set(cells) | set(units):
                o = (a >> 8) << 8
                lo = o if lo is None else min(lo, o)
                if a in cells:
                    b = self.raw(a)
                    (_o0, _m), (o1, k) = struct.unpack_from(">HH", b, 8), struct.unpack_from(">HH", b, 12)
                    for t in struct.unpack_from(f">{k}I", b, o1):
                        lo = min(lo, (t >> 8) << 8)
        return lo

    def span(self, tables, roots=()):
        """(lo, hi) extent of every unit of the given tables, plus the 0x08/0x09
        grid-and-cell chains reachable from the root entries in `roots`."""
        lo = hi = None
        def add(o, s):
            nonlocal lo, hi
            lo = o if lo is None else min(lo, o)
            hi = s if hi is None else max(hi, s)
        for u in self.units:
            if u.table in tables:
                add(u.off, u.off + u.size)
        cells = []
        for i in roots:
            a = self.layer(i)[0]
            off = (a >> 8) << 8
            while off in self.by and self.by[off].table == 0x08:
                add(off, off + self.by[off].size)
                b = self.raw(off)
                s, c = struct.unpack_from(">HH", b, 8)
                cells += struct.unpack_from(f">{c}I", b, s)
                off += self.by[off].size
        for ca in set(cells):
            u = self.by.get((ca >> 8) << 8)
            if u and u.table == 0x09:
                add(u.off, u.off + u.size)
        return (lo, hi)


def carin_world():
    return cn.WORLD


# ---- grids ------------------------------------------------------------------

class CellUnit:
    """0x09: the tiles of one grid cell, on an x-major sub-grid."""

    def __init__(self, layer, cell_xy, cell, tiles):
        self.key = ("cell", layer, cell_xy)
        self.table = 0x09
        x0 = cn.OX + (cell_xy[0] * cell if tiles else 0)
        y0 = cn.OY + (cell_xy[1] * cell if tiles else 0)
        if tiles:
            sub = min(s for _x, _y, s in (t[1] for t in tiles))
            sub = max(sub, cell // 32)
        else:
            sub = cell
        n = cell // sub
        self.sub, self.n = sub, n
        self.entries = []       # tile key per sub-cell (or None)
        order = []
        for ix in range(n):
            for iy in range(n):
                px, py = x0 + ix * sub + sub // 2, y0 + iy * sub + sub // 2
                hit = None
                for key in tiles:
                    tx, ty, ts = key[1]
                    if tx <= px < tx + ts and ty <= py < ty + ts:
                        hit = key
                        break
                self.entries.append(hit)
                if hit is not None and hit not in order:
                    order.append(hit)
        self.order = order
        self.s1 = 0x14 + ((2 * n * n + 3) & ~3)

    def size_bytes(self):
        return self.s1 + 4 * len(self.order)

    def logical(self, addr, unit_header):
        out = bytearray(self.size_bytes())
        out[:8] = unit_header
        struct.pack_into(">HHHHI", out, 8, 0x14, self.n * self.n, self.s1, len(self.order), self.sub)
        pos = {k: self.s1 + 4 * i for i, k in enumerate(self.order)}
        for i, k in enumerate(self.entries):
            struct.pack_into(">H", out, 0x14 + 2 * i, pos[k] if k is not None else 0)
        for k, p in pos.items():
            struct.pack_into(">I", out, p, addr(k))
        return bytes(out)


class GridUnit:
    """0x08: a run of cell-unit addresses (x-major over the world)."""

    PER_UNIT = 6140
    # Always zlib. The factory stores its grids, but a stored grid at any address the
    # factory doesn't use was refused by the unit on all three discs that had one (B, D,
    # E), while compressed grids loaded on every disc that had them (5, 7, 10, 12, A).
    compress = True
    force_zlib = True

    def __init__(self, layer, part, cell, keys):
        self.key = ("grid", layer, part)
        self.table = 0x08
        self.cell, self.keys = cell, keys

    def size_bytes(self):
        return 0x10 + 4 * len(self.keys)

    def logical(self, addr, unit_header):
        out = bytearray(unit_header) + struct.pack(">HHI", 0x10, len(self.keys), self.cell)
        for k in self.keys:
            out += struct.pack(">I", addr(k))
        return bytes(out)


def make_grid(layer, cell, tiles):
    """tiles: [tile key (level, rect)] -> (grid units, cell units). Empty cells share one."""
    n = cn.WORLD // cell
    per = collections.defaultdict(list)
    for key in tiles:
        x0, y0, s = key[1]
        for cx in range((x0 - cn.OX) // cell, (x0 + s - 1 - cn.OX) // cell + 1):
            for cy in range((y0 - cn.OY) // cell, (y0 + s - 1 - cn.OY) // cell + 1):
                per[(cx, cy)].append(key)
    cells = {xy: CellUnit(layer, xy, cell, ts) for xy, ts in per.items()}
    empty = CellUnit(layer, EMPTY, cell, [])
    empty.key = ("cell", layer, EMPTY)
    keys = []
    for cx in range(n):
        for cy in range(n):
            keys.append(cells[(cx, cy)].key if (cx, cy) in cells else empty.key)
    grids = [GridUnit(layer, i, cell, keys[i:i + GridUnit.PER_UNIT])
             for i in range(0, len(keys), GridUnit.PER_UNIT)]
    return grids, list(cells.values()) + [empty]


# ---- road levels ------------------------------------------------------------

class LevelInfo:
    def __init__(self, num, table, max_group, max_size, min_size, detail, budget, cell, root_entry):
        self.num, self.table, self.max_group = num, table, max_group
        self.max_size, self.min_size, self.detail, self.budget = max_size, min_size, detail, budget
        self.cell, self.root_entry = cell, root_entry


STATES = {"VIC": "victoria", "NSW": "new south wales", "QLD": "queensland", "SA": "south australia",
          "WA": "western australia", "TAS": "tasmania", "NT": "northern territory",
          "ACT": "australian capital territory", "JBT": "jervis bay territory"}
# on a state disc, roads of this group or more important keep street detail nationwide;
# 2 = everything level 3 carries, so every coarse tile has street-level tiles under it
# (the factory has no coarse tile without them)
STATE_DISC_NATIONAL_GROUP = 2


def build_levels(net, labels, factory, states=None):
    """states: set of state names (as in STATES' values) whose every road gets street
    detail; None for all of Australia. The coarse levels are national either way."""
    root_entry = {1: 2, 2: 3, 3: 4, 6: 0}
    levels = {}
    for num, table, maxg, max_size, _min, detail, budget in cw.LEVELS:
        cell = factory.grid_cached(root_entry[num])[0]
        levels[num] = LevelInfo(num, table, maxg, min(max_size, cell), max(cell // 32, 0xC000), detail,
                                budget, cell, root_entry[num])
    min_group = [9] * len(net.x)
    for s in range(net.n_segs):
        g = net.group[s]
        for n in (net.a[s], net.b[s]):
            if g < min_group[n]:
                min_group[n] = g

    on_level = collections.defaultdict(set)     # node -> overview levels it is a road end on

    def node_levels(n):
        return on_level[n] | {6}

    tiles = {}            # key -> TileRec
    locs = {}
    # finest first: each coarser level must cover every tile of the level below it, so
    # zooming out always finds a parent (the factory leaves 0.2% of detail tiles without one)
    cover = ()
    for num in (6, 3, 2, 1):
        lv = levels[num]
        segs = [s for s in range(net.n_segs) if net.group[s] <= lv.max_group]
        if lv.detail and states is not None:
            segs = [s for s in segs if net.group[s] <= STATE_DISC_NATIONAL_GROUP or labels(s)[1] in states]
        if lv.detail:
            # street level: merge only where nothing a driver or the search would see
            # changes (name, route numbers, class, suburb, urban/rural)
            same = lambda o, s: (net.cls[o] == net.cls[s] and net.group[o] == net.group[s]
                                 and net.name[o] == net.name[s] and net.refs[o] == net.refs[s]
                                 and net.urban[o] == net.urban[s] and labels(o) == labels(s))
            chains = merge_chains(net, segs, same)
            lv.on_disc = set(segs)                     # every road the street level holds
            lv.chain_of = {m: first for first, ch in chains.items() for m in ch}
        else:
            chains = merge_chains(net, segs)
        segs = sorted(chains)
        if not lv.detail:
            for s in segs:
                for n in chain_ends(net, chains[s]):
                    on_level[n].add(num)
        lines = {}
        for s in segs:
            pts = chain_points(net, chains[s])
            # shapes within DETAIL_SHAPE_TOL of the OSM line on the detail level (OSM
            # draws curves far denser than the unit can show), coarser above it
            pts = simplify(pts, {1: 24, 2: 12, 3: 6, 6: DETAIL_SHAPE_TOL}[num] * cn.Q)
            lines[s] = pts
        leaves = cn.make_tiles(lines.values(), cn.Level(num, lv.table, lv.max_group, lv.cell,
                                                        lv.max_size, lv.min_size, lv.detail, lv.budget),
                               cover)
        cover = [(x0 + sz // 2, y0 + sz // 2) for x0, y0, sz in leaves]
        loc = cn.Locator(leaves, lv.max_size, lv.min_size)
        locs[num] = loc
        recs = {r: cw.TileRec(lv, r) for r in leaves}
        for s in segs:
            pcs = cn.clip(lines.pop(s), loc)
            na, nb = chain_ends(net, chains[s])
            for i, (rect, pp) in enumerate(pcs):
                ka = ("n", na) if i == 0 else ("b", s, i - 1, 1)
                kb = ("n", nb) if i == len(pcs) - 1 else ("b", s, i, 0)
                recs[rect].pieces.append(cw.Piece(s, pp, ka, kb))
                if i > 0:
                    recs[rect].bnodes[ka] = (pp[0][0], pp[0][1], pcs[i - 1][0], ("b", s, i - 1, 0), net.group[s])
                if i < len(pcs) - 1:
                    recs[rect].bnodes[kb] = (pp[-1][0], pp[-1][1], pcs[i + 1][0], ("b", s, i, 1), net.group[s])
        for r, rec in recs.items():
            tiles[(num, r)] = rec
        big = max(((sum(len(p.pts) for p in r.pieces) * 6 + len(r.pieces) * 40, k) for k, r in recs.items()),
                  default=(0, None))
        log(f"level {num}: {len(segs):,} roads, {len(leaves):,} tiles, heaviest ~{big[0]:,} B at {big[1]}")
    # a coarse tile with no roads never occurs on the factory disc: drop any the cover
    # pass made; the tiles under them keep parent 0, as 0.2-2.7% of factory tiles do
    for key in [k for k, r in tiles.items() if k[0] != 6 and not r.pieces]:
        del tiles[key]
    # parents and children
    finer = {1: 2, 2: 3, 3: 6}
    for num, fnum in finer.items():
        for key, rec in tiles.items():
            if key[0] != fnum:
                continue
            x0, y0, s = key[1]
            p = locs[num].find(x0 + s // 2, y0 + s // 2)
            if p is not None and (num, p) in tiles:
                rec.parent = (num, p)
            # every coarser tile this one overlaps lists it as a child
            for ckey in overlapping(locs[num], key[1]):
                c = tiles.get((num, ckey))
                if c is not None:
                    c.children.append(key)
                    c.finest_child = min(c.finest_child or s, s)
    for rec in tiles.values():
        rec.children.sort()
    return levels, tiles, node_levels


def merge_chains(net, segs, same=None):
    """Join roads that meet end to end at a node no other road of the level touches, when
    same(o, s) says they match (default: same class and group). Returns {first seg: [segs
    in order, each oriented as stored]} with the chain running from the first seg's a end."""
    if same is None:
        same = lambda o, s: net.cls[o] == net.cls[s] and net.group[o] == net.group[s]
    ends = collections.defaultdict(list)
    for s in segs:
        ends[net.a[s]].append(s)
        ends[net.b[s]].append(s)
    used = set()
    out = {}

    def through(n, s):
        lst = ends[n]
        if len(lst) != 2:
            return None
        o = lst[0] if lst[1] == s else lst[1]
        if o == s or o in used or not same(o, s):
            return None
        # keep one orientation so the class's direction bits stay right
        return o if net.a[o] == n else None

    def grow(first):
        chain = [first]
        used.add(first)
        cur = first
        while True:
            o = through(net.b[cur], cur)
            # the factory has no road whose ends meet: a ring stops one short of closing
            if o is None or len(chain) >= 64 or net.b[o] == net.a[first]:
                break
            chain.append(o)
            used.add(o)
            cur = o
        out[first] = chain

    for s in segs:
        if s in used:
            continue
        # walk back to the start of the chain
        first = s
        seen = {s}
        while True:
            n = net.a[first]
            lst = ends[n]
            if len(lst) != 2:
                break
            o = lst[0] if lst[1] == first else lst[1]
            if o in seen or o in used or net.b[o] != n or not same(o, first):
                break
            seen.add(o)
            first = o
        grow(first)
        # the forward walk can stop before it gets back to s (64-road cap, ring rule):
        # whatever it didn't reach starts chains of its own, so no road is ever dropped
        while s not in used:
            grow(next(m for m in seen if m not in used))
    return out


def chain_points(net, chain):
    pts = [(net.x[net.a[chain[0]]], net.y[net.a[chain[0]]])]
    for s in chain:
        pts += net.shape[s]
        pts.append((net.x[net.b[s]], net.y[net.b[s]]))
    return pts


def chain_ends(net, chain):
    return net.a[chain[0]], net.b[chain[-1]]


def overlapping(loc, rect):
    """Leaves of `loc` that overlap rect. Probes every loc.min cell inside rect, at a step
    no bigger than rect itself (a rect smaller than loc.min used to be probed outside
    itself and could be listed under the wrong coarse tile)."""
    x0, y0, s = rect
    out = set()
    step = min(loc.min, s)
    for x in range(x0, x0 + s, step):
        for y in range(y0, y0 + s, step):
            k = loc.find(x + step // 2, y + step // 2)
            if k is not None:
                out.add(k)
    return sorted(out)


def simplify(pts, tol):
    """Douglas-Peucker on absolute points (ends kept)."""
    if len(pts) <= 2:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        (x1, y1), (x2, y2) = pts[i], pts[j]
        dx, dy = x2 - x1, y2 - y1
        L = (dx * dx + dy * dy) ** 0.5 or 1.0
        best, bk = -1.0, None
        for k in range(i + 1, j):
            d = abs(dy * (pts[k][0] - x1) - dx * (pts[k][1] - y1)) / L
            if d > best:
                best, bk = d, k
        if bk is not None and best > tol:
            keep[bk] = True
            stack += [(i, bk), (bk, j)]
    return [p for p, k in zip(pts, keep) if k]


# ---- layout and assembly ---------------------------------------------------------

class Blob:
    """A unit whose logical bytes don't depend on addresses (or are patched by fn)."""

    def __init__(self, key, table, data_fn, size, compress=True):
        self.key, self.table, self.data_fn, self._size, self.compress = key, table, data_fn, size, compress

    def size_bytes(self):
        return self._size

    def logical(self, addr, unit_header):
        return self.data_fn(addr, unit_header)


def assemble(factory, units, fixed, start):
    """Lay out units from `start`, filling up to each reserved range in `fixed`
    (sorted (lo, hi) pairs, e.g. the POI slab, the routing block and the background
    block) and resuming after it. Returns the new carindb. Units: objects with
    key, table, size_bytes(), logical(addr, hdr)."""
    db = factory.db
    est = {}          # key -> pages on disc
    enc = {}

    def encode(u, addr):
        logical_len = u.size_bytes()
        pages_l = -(-logical_len // PAGE)
        hdr = struct.pack(">IHH", 0, u.table, 0)
        data = u.logical(addr, hdr)
        data = data.ljust(pages_l * PAGE, b"\0")
        if getattr(u, "compress", True):
            body = zlib.compress(data[8:], 9)
            if 8 + len(body) < len(data) - PAGE or getattr(u, "force_zlib", False):
                return data[:8], body, pages_l, carin.ZLIB
        return data[:8], data[8:], pages_l, carin.STORED

    place = {}

    def layout():
        place.clear()
        pos = start
        order = []
        ri = 0
        for u in units:
            p = est[u.key]
            # skip every reserved range this unit would touch (a unit starting exactly
            # on a range's first byte used to be written over the kept factory data)
            while ri < len(fixed) and pos + p * PAGE > fixed[ri][0]:
                if pos < fixed[ri][0]:
                    order.append(("fill", pos, (fixed[ri][0] - pos) // PAGE))
                pos = max(pos, fixed[ri][1])
                ri += 1
            place[u.key] = (pos, p)
            order.append(("unit", pos, u))
            pos += p * PAGE
        while ri < len(fixed) and pos < fixed[ri][0]:
            order.append(("fill", pos, (fixed[ri][0] - pos) // PAGE))
            pos = fixed[ri][1]
            ri += 1
        return order, pos

    def addr(key):
        pos, p = place[key]
        return (pos >> 8) << 8 | p

    # pass 0: sizes with dummy addresses
    for u in units:
        place[u.key] = (0, 1)
    for u in units:
        h, body, pl, codec = encode(u, lambda k: 0x01000101)
        est[u.key] = -(-(8 + len(body)) // PAGE) + (1 if codec == carin.ZLIB and (8 + len(body)) % PAGE > PAGE - 64 else 0)
    for attempt in range(6):
        order, end = layout()
        grew = False
        for kind, pos, u in order:
            if kind != "unit":
                continue
            h, body, pl, codec = encode(u, addr)
            need = -(-(8 + len(body)) // PAGE)
            if need > est[u.key]:
                est[u.key] = need
                grew = True
            enc[u.key] = (body, pl, codec)
        if not grew:
            break
        log(f"layout: some units grew, pass {attempt + 2}")
    else:
        raise RuntimeError("layout did not settle")
    out = bytearray(db[:start])
    out += bytes(max(end, len(db)) - start)
    for lo, hi in fixed:
        out[lo:hi] = db[lo:hi]
    prev = None
    for kind, pos, u in order:
        if kind == "fill":
            p = u
            while p > 0:
                k = min(p, 0xFF)
                owner = prev if prev is not None else pos
                struct.pack_into(">IHH", out, pos, ((owner >> 8) << 8) | k, 0x05, 0)
                prev, pos, p = pos, pos + k * PAGE, p - k
            continue
        body, pl, codec = enc[u.key]
        pages = est[u.key]
        c = (codec << 8) | (pl if codec == carin.ZLIB else 0)
        out[pos:pos + 8] = struct.pack(">IHH", addr(u.key), u.table, c)
        out[pos + 8:pos + 8 + len(body)] = body
        prev = pos
    if end > len(db):
        # overflow went after the fixed block; the file ends where the last unit does
        del out[end:]
    return bytes(out), place


class TileUnit:
    def __init__(self, rec, net):
        self.rec, self.net = rec, net
        self.key = rec.key
        self.table = rec.level.table
        self.compress = not (STORED_TILES and rec.level.detail)

    def size_bytes(self):
        return self.rec.size_bytes

    def logical(self, addr, unit_header):
        return self.rec.logical(self.net, addr, unit_header)


class HNUnit:
    def __init__(self, hn):
        self.hn, self.key, self.table = hn, hn.key, 0x04

    def size_bytes(self):
        return 0x10 + 10 * self.hn.nroads

    def logical(self, addr, unit_header):
        return self.hn.logical(addr, unit_header)


def make_labels(net, boundaries):
    """segment -> (suburb, state) from OSM admin areas (levels 9 and 4)."""
    from . import osm_names
    if boundaries is None:
        none = lambda s: ("", "")
        none.point = lambda x5, y5: ("", "")
        return none
    loc = osm_names.Localities(boundaries)
    states = [a for a in boundaries if a.level == 4]
    st_idx = osm_names.AreaIndex(states, cell=50000)
    cache = {}

    def to5(x, y):
        return int(round((x * 1.8e-7 - 30.0) * 1e5)), int(round(y * 1.8e-7 * 1e5))

    def labels(s):
        if s in cache:
            return cache[s]
        sh = net.shape[s]
        if sh:
            x, y = sh[len(sh) // 2]
        else:
            x = (net.x[net.a[s]] + net.x[net.b[s]]) // 2
            y = (net.y[net.a[s]] + net.y[net.b[s]]) // 2
        x5, y5 = to5(x, y)
        i = loc.find(x5, y5)
        place = cn.ascii_name(loc.loc[i].name) if i is not None else ""
        j = st_idx.find(x5, y5)
        state = cn.ascii_name(states[j].name) if j is not None else ""
        cache[s] = (place, state)
        return cache[s]

    def point(x5, y5):
        """(suburb, state) at a point in deg * 1e5 (POIs)."""
        i = loc.find(x5, y5)
        j = st_idx.find(x5, y5)
        return (cn.ascii_name(loc.loc[i].name) if i is not None else "",
                cn.ascii_name(states[j].name) if j is not None else "")
    labels.point = point
    return labels


def build_db(factory, net, labels, keep_poi=False, states=None, poi_cache=None):
    levels, tiles, node_levels = build_levels(net, labels, factory, states)
    for rec in tiles.values():
        rec.prepare(net, labels, node_levels)
    # boundary node offsets, for the partner pointers
    boff = {}
    for key, rec in tiles.items():
        for j, k in enumerate(rec.bnd):
            boff[(key[0], key[1], k)] = rec.offs[6] + 16 * j

    def partner(lvnum):
        return lambda pkey, prect: boff[(lvnum, prect, pkey)]
    for key, rec in tiles.items():
        rec.partner_off = partner(key[0])
        if rec.level.detail:
            x0, y0, s = key[1]
            fa = factory.tile_at(0, x0 + s // 2, y0 + s // 2)
            if fa:
                rec.head_extra = factory.raw(fa)[0x60:0x74]
    big = max(rec.size_bytes for rec in tiles.values())
    log(f"tiles: {len(tiles):,}, largest {big:,} bytes")
    if big > MAX_LOGICAL:
        raise RuntimeError(f"a tile is {big:,} bytes; no factory unit unpacks to more than "
                           f"{MAX_LOGICAL:,}. Lower the level budget or simplify harder")
    units = []
    hns = []
    for num in (1, 2, 3, 6):
        for key in sorted(k for k in tiles if k[0] == num):
            rec = tiles[key]
            units.append(TileUnit(rec, net))
            if rec.level.detail and WITH_HOUSE_NUMBERS:
                hn = cw.HouseNumbers(key, len(rec.pieces))
                rec.hn_key = hn.key
                hns.append(HNUnit(hn))
    units += hns
    grids = {}
    for num in (1, 2, 3, 6):
        lv = levels[num]
        g, cells = make_grid(num, lv.cell, [k for k in tiles if k[0] == num])
        grids[lv.root_entry] = g
        units += g + cells
    # POI layer: every cell empty (or, with keep_poi, the factory's own POI layer
    # is preserved byte for byte and root entry 1 keeps the factory address)
    poi_links = {}
    if poi_cache is not None:
        from . import carin_poi
        lv = levels[6]
        poi_cell = factory.grid_cached(1)[0]
        pu, ptiles, poi_links, report = carin_poi.build(
            net, tiles, lambda seg: seg in lv.on_disc, lambda seg: lv.chain_of[seg], labels.point,
            states, poi_cache, poi_cell, progress=log)
        log("POIs: " + ", ".join(f"{k} {v:,}" for k, v in report.items()))
        g, cells = make_grid("poi", poi_cell, ptiles)
        grids[1] = g
        units += pu + g + cells
    elif not keep_poi:
        poi_cell = factory.grid_cached(1)[0]
        g, cells = make_grid("poi", poi_cell, [])
        grids[1] = g
        units += g + cells
    build_db.poi_links = poi_links
    return units, grids, tiles


def patch_root(factory, db, grids):
    u = factory.by[0xC00]
    root = bytearray(carin.decode(db, u))
    for entry, g in grids.items():
        struct.pack_into(">I", root, 0x10 + 24 * entry, g[0])
    return carin.encode_in_place(u, bytes(root))


def main(argv=None):
    global STORED_TILES, DETAIL_SHAPE_TOL
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("iso")
    ap.add_argument("out")
    ap.add_argument("--roads", required=True, help="roads2_AU.pickle from an OpenLion build")
    ap.add_argument("--boundaries", help="boundaries3_AU.pickle (suburb and state names)")
    ap.add_argument("--bbox", nargs=4, type=float, help="lon0 lat0 lon1 lat1 (test builds)")
    ap.add_argument("--db-only", action="store_true", help="write carindb instead of an ISO")
    ap.add_argument("--keep-factory-poi", action="store_true",
                    help="keep the factory POI layer (0x06/0x10/0x11) byte for byte at its "
                         "factory offset, instead of an empty POI layer")
    ap.add_argument("--pois", help="pois2_AU.pickle from an OpenLion build: current OSM POIs "
                    "replace the factory ones (each linked to the road it sits on)")
    ap.add_argument("--states", help="state disc: full street detail only for these states, "
                    "e.g. VIC or VIC,TAS (needs --boundaries); major roads stay national")
    ap.add_argument("--stored-tiles", action="store_true",
                    help="store street-level tiles uncompressed (factory: 502 stored, 2 zlib)")
    ap.add_argument("--detail-tol", type=float, default=DETAIL_SHAPE_TOL,
                    help="detail shape tolerance in tile steps of about 1.3 m (default %(default)s)")
    ap.add_argument("--allow-overflow", action="store_true",
                    help="let units go past the licensing block when the map doesn't fit "
                         "before it (discs 3 and 10 did this and the unit rejected disc 3)")
    a = ap.parse_args(argv)
    STORED_TILES, DETAIL_SHAPE_TOL = a.stored_tiles, a.detail_tol
    img = open(a.iso, "rb").read()
    _f, db = carin.read_db(img)
    factory = Factory(db)
    log("reading OSM roads")
    c = pickle.load(open(a.roads, "rb"))
    bbox = tuple(int(v * 1e5) for v in a.bbox) if a.bbox else None
    net = cn.build_net(c.ways, bbox, progress=log)
    del c
    log(f"network: {net.n_segs:,} roads, {len(net.x):,} nodes")
    boundaries = pickle.load(open(a.boundaries, "rb")) if a.boundaries else None
    labels = make_labels(net, boundaries)
    states = None
    if a.states:
        states = {STATES[x.strip().upper()] for x in a.states.split(",")}
        log(f"state disc: street detail for {', '.join(sorted(states))}; elsewhere roads of "
            f"group {STATE_DISC_NATIONAL_GROUP} or above")
    if a.pois and a.keep_factory_poi:
        raise SystemExit("--pois replaces the factory POIs; don't combine it with --keep-factory-poi")
    poi_cache = pickle.load(open(a.pois, "rb")) if a.pois else None
    units, grids, tiles = build_db(factory, net, labels, keep_poi=a.keep_factory_poi, states=states,
                                   poi_cache=poi_cache)
    from . import carin_search
    units += carin_search.build(factory, net, tiles, labels, build_db.poi_links)
    # what the unit reads at start-up (grids, cells, search directory) goes first, so it
    # always lands before the licensing block; road tiles and street lists follow
    first = (0x08, 0x09, 0x0C, 0x0D, 0x0F)
    units = [u for u in units if u.table in first] + [u for u in units if u.table not in first]
    fixed = [(factory.background_start(), len(db))]
    if a.keep_factory_poi:
        poi = factory.span({0x06, 0x10, 0x11}, roots=(1,))
        fixed += [poi]
        # the factory 0x04 units are house numbers for the factory's own detail tiles;
        # rebuilt tiles carry house-number address 0, so nothing reads them: not kept
        log(f"keeping factory POI layer {poi[0]:#x}..{poi[1]:#x}")
    fixed.sort()
    log(f"laying out {len(units):,} units; fixed block from {fixed[0][0]:#x}")
    new, place = assemble(factory, units, fixed, carin_search.START)
    grid_addr = {e: [(place[g.key][0] >> 8) << 8 | place[g.key][1] for g in gs] for e, gs in grids.items()}
    new = bytearray(new)
    new[0xC00:0xE00] = patch_root(factory, new, grid_addr)
    new = carin_search.patch_fixed(factory, new, place)
    over = sum(1 for pos, _p in place.values() if pos >= len(db))
    log(f"carindb: {len(new):,} bytes ({len(db):,} before); {over:,} units past the licensing block")
    if over and not a.allow_overflow:
        raise SystemExit(f"{over:,} units don't fit before the licensing block (disc 3, which "
                         "did this, was rejected). Shrink the build, or pass --allow-overflow.")
    if a.db_only:
        open(a.out, "wb").write(new)
    else:
        from . import carin_iso
        open(a.out, "wb").write(carin_iso.replace_db(img, bytes(new)))
    log(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
