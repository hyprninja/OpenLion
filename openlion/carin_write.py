"""Build CARiN road tiles (tables 0x00-0x03) and house-number units (0x04) from a
carin_net network. See docs/29_vz_map_disc.md and carin_tile.py for the layout.

Units refer to each other by address (offset >> 8 | pages), which is only known once
everything is laid out, so tiles are built as TileRec objects and turned into bytes by
TileRec.logical(addr), where addr(key) gives the final address of any unit.
"""
import collections
import math
import struct

from . import carin_net as cn
from . import carin_tile as ct

LEVELS = (
    # num, table, max group, max tile size, min tile size, detail, weight budget (bytes,
    # estimated as in carin_net.make_tiles; the factory's largest tiles: 22 KB overview,
    # 24 KB street level; nothing written may pass 32 KB)
    # each finer level adds one group, as on the factory: level 1 groups 0-1, level 2
    # 0-2, level 3 0-3 (doc 32); a road keeps its group on every level it is on
    (1, 0x01, 1, 0x300000, 0x60000, False, 20000),
    (2, 0x02, 2, 0x300000, 0x30000, False, 20000),
    (3, 0x03, 3, 0x300000, 0x18000, False, 20000),
    (6, 0x00, 4, 0x180000, 0x0C000, True, 23000),
)
COARSER = {6: (3, 2, 1), 3: (2, 1), 2: (1,), 1: ()}
OVERVIEW_CONST = 0x0020C49B
COUNTRY = "australia"


def level_of_group(g):
    """Coarsest level a road of this group appears on."""
    return 1 if g <= 1 else 2 if g <= 2 else 3 if g <= 3 else 6


MAX_GROUP = {1: 1, 2: 2, 3: 3, 6: 4}
# node flags, bits 14-15: the coarsest level the node reaches (doc 32)
LEVEL_CODE = {1: 0x0000, 2: 0x4000, 3: 0x8000, 6: 0xC000}
HEAD_6C = 0x0167000A     # the factory's commonest value (meaning not decoded)


def heading(p, q):
    """Degrees clockwise from north, p to q."""
    return math.degrees(math.atan2(q[0] - p[0], q[1] - p[1])) % 360


def in_level(g, lv):
    return g <= MAX_GROUP[lv]


class Piece:
    __slots__ = ("seg", "pts", "ka", "kb", "flip")

    def __init__(self, seg, pts, ka, kb):
        self.seg, self.pts, self.ka, self.kb, self.flip = seg, pts, ka, kb, False

    def orient(self):
        """Every factory road runs west to east (A before B by x, then y): reverse the piece
        if it doesn't. A reversed one-way road swaps its direction bits when written."""
        if self.pts[-1] < self.pts[0]:
            self.pts = self.pts[::-1]
            self.ka, self.kb = self.kb, self.ka
            self.flip = True

    def cls(self, net):
        c = net.cls[self.seg]
        if self.flip and c & 0x30 in (0x10, 0x20):
            c ^= 0x30
        return c


class TileRec:
    def __init__(self, level, rect):
        self.level, self.rect = level, rect          # rect = (x0, y0, w, h)
        self.pieces = []
        self.members = {}         # chain head seg -> the network roads it is made of
        self.bnodes = {}          # key -> (x, y, partner tile rect, partner key, group)
        self.key = (level.num, rect)
        self.children = []        # finer-level tile keys (overview)
        self.parent = None
        self.hn_key = None        # house-number unit key (detail)
        self.head_words = (0, 0, 0)   # detail: header 0x60, 0x64, 0x68 (set by the builder)
        self.finest_child = None  # overview: smallest finer-level tile under this one
        self._layout = None

    # -- build the static part once (everything but addresses) ---------------------
    def prepare(self, net, labels, node_levels):
        lv = self.level
        detail = lv.detail
        x0, y0, w, h = self.rect
        rel = lambda p: ((p[0] - x0) // cn.Q, (p[1] - y0) // cn.Q)
        # names (detail only)
        self.str_pool = {}
        entries = []           # (string, flags)

        def sref(s):
            if s not in self.str_pool:
                self.str_pool[s] = None
            return s

        street_of = {}
        self.street_major = {}
        if detail:
            places, streets = {}, {}
            for p in self.pieces:
                s = p.seg
                place, state = labels(s)
                pk = (place, state)
                if place and pk not in places:
                    places[pk] = len(places)
                nm, refs = net.name[s], net.refs[s]
                if nm or refs:
                    sk = (nm, refs, pk if place else None)
                    if sk not in streets:
                        streets[sk] = len(streets)
                    street_of[s] = streets[sk]
                    if net.group[s] <= 2:
                        self.street_major[sk] = True
            self.places = sorted(places, key=places.get)
            self.streets = sorted(streets, key=streets.get)
            if len(self.places) > cn.PLACE_CAP:
                # the factory never has more than 10 places in a tile: keep the busiest;
                # streets of the others show no suburb here (search has its own lists)
                busy = collections.Counter(labels(p.seg) for p in self.pieces)
                keep = set(sorted(self.places, key=lambda pk: -busy[pk])[:cn.PLACE_CAP])
                self.places = [pk for pk in self.places if pk in keep]
        # order roads: by group, then street record, then segment
        grp = lambda p: net.group[p.seg]
        for p in self.pieces:
            p.orient()
        self.pieces.sort(key=lambda p: (grp(p), street_of.get(p.seg, -1), p.seg))
        self.street_of = street_of
        # nodes
        inc = collections.defaultdict(list)            # node key -> [(road idx, end)]
        for i, p in enumerate(self.pieces):
            inc[p.ka].append((i, 0))
            inc[p.kb].append((i, 1))
        # each node's roads clockwise from north, by the direction to the road's next point
        # (factory: clockwise at 99.99% of junctions, starting nearest north at 98-99%)
        def out_heading(k, ie):
            i, e = ie
            pts = self.pieces[i].pts if e == 0 else self.pieces[i].pts[::-1]
            q = next((q for q in pts[1:] if q != pts[0]), pts[-1])
            return heading(pts[0], q)
        for k in inc:
            inc[k].sort(key=lambda ie: (out_heading(k, ie), ie))
        real = [k for k in inc if k[0] == "n"]
        bnd = [k for k in inc if k[0] == "b"]
        ming = {k: min(grp(self.pieces[i]) for i, _e in inc[k]) for k in inc}
        npos = {k: (self.pieces[inc[k][0][0]].pts[0] if inc[k][0][1] == 0 else self.pieces[inc[k][0][0]].pts[-1])
                for k in inc}
        self.npos = npos
        # inner nodes: by group, then x, then y (factory: every tile)
        real.sort(key=lambda k: (ming[k], npos[k]))
        bnd.sort(key=lambda k: min(i for i, _e in inc[k]))
        self.real, self.bnd, self.inc, self.ming = real, bnd, inc, ming
        self.node_levels = node_levels
        self.rel = rel
        # shapes, in road order
        self.n7 = sum(len(p.pts) - 2 for p in self.pieces)
        # name strings / entries
        if detail:
            self.entries = [(COUNTRY, 0x200)]
            self.place_entry = {}
            for pk in self.places:
                self.place_entry[pk] = len(self.entries)
                self.entries.append((pk[0], 0x200))
            self.street_entry = {}
            for sk in self.streets:
                nm, refs, _pk = sk
                self.street_entry[sk] = len(self.entries)
                if nm and refs and self.street_major.get(sk):
                    # the factory lists a numbered highway's route first (472 of 561)
                    self.entries.append((refs[0], 0x10))
                    self.entries += [(r, 0x11) for r in refs[1:]]
                    self.entries.append((nm, 0x20A))
                elif nm:
                    self.entries.append((nm, 0x208))
                    self.entries += [(r, 0x11) for r in refs]
                else:
                    self.entries.append((refs[0], 0x10))
                    self.entries += [(r, 0x11) for r in refs[1:]]
            self.state_entry = {}
            for pk in self.places:
                self.state_entry[pk] = len(self.entries)
                self.entries.append((pk[1] or COUNTRY, 0x200))
            strings, soff = bytearray(), {}
            for s, _f in self.entries:
                if s not in soff:
                    soff[s] = len(strings)
                    strings += s.encode("ascii", "replace")[:250] + b"\0"
            self.strings, self.soff = bytes(strings), soff
        # neighbours (by partner tile), children are set by the caller
        self.neigh = sorted({self.bnodes[k][2] for k in bnd})
        t = ct.Tile(detail, (x0, y0, x0 + w, y0 + h), bytes(32))
        t.s0 = [bytes(10)] * (3 if detail else 2)
        t.s1 = [bytes(10)] * ((len(self.places) + 2) if detail else 2)
        t.s2 = [bytes(10)] * ((len(self.streets) + 2) if detail else 2)
        t.s3 = bytes(32)
        t.nroads = len(self.pieces)
        t.roads = bytes(ct.road_size(detail) * (t.nroads + 1))
        t.n5, t.s5 = len(real), bytes(8 * len(real))
        t.n6, t.s6 = len(bnd), bytes(16 * len(bnd))
        t.n7, t.s7 = self.n7, bytes(6 * self.n7)
        # child list = slot grid (x-major), slot = shortest finer side (header 0x5C)
        if self.children:
            v = self.finest_child
            self.slots = (w // v, h // v)
        else:
            self.slots = (0, 0)
        t.n8 = self.slots[0] * self.slots[1]
        t.s8 = bytes(4 * t.n8)
        t.n9, t.s9 = len(self.neigh), bytes(8 * len(self.neigh))
        t.names = [(0, 0)] * (len(self.entries) if detail else 0)
        t.strings = self.strings if detail else b""
        self.tile = t
        self.offs, self.end = ct.layout(t)
        self.size_bytes = self.end + len(t.strings)

    # -- serialise with final addresses ---------------------------------------------
    def logical(self, net, addr, unit_header):
        t, O = self.tile, self.offs
        detail = self.level.detail
        rs = ct.road_size(detail)
        x0, y0, w, h = self.rect
        rel = self.rel
        node_off = {}
        for i, k in enumerate(self.real):
            node_off[k] = O[5] + 8 * i
        for i, k in enumerate(self.bnd):
            node_off[k] = O[6] + 16 * i
        road_off = lambda i: O[4] + rs * i
        # next-road links round each node (circular, in road order)
        nxt = {}
        for k, lst in self.inc.items():
            n = len(lst)
            for j, (i, e) in enumerate(lst):
                step = 1
                # a road whose two ends meet at this node occupies two entries;
                # pointing one entry at the other would leave the list unclosed
                while step < n and lst[(j + step) % n][0] == i:
                    step += 1
                ni, ne = lst[(j + step) % n]
                nxt[(i, e)] = road_off(ni)
        # names
        if detail:
            eoff = lambda j: O[14] + 4 * j
            names_end = O[14] + 4 * len(self.entries)
            name_ptr = O[14]
            t.names = [(names_end + self.soff[s], f) for s, f in self.entries]
            s0r = lambda j: O[0] + 10 * j
            s1r = lambda j: O[1] + 10 * j
            s2r = lambda j: O[2] + 10 * j
            sval = lambda s: names_end + self.soff[s]
            first_place = eoff(1)
            first_street = eoff(1 + len(self.places))
            first_state = eoff(1 + len(self.places) + sum(
                (1 + len(sk[1])) if sk[0] else len(sk[1]) for sk in self.streets))
            t.s0 = [bytes(10),
                    struct.pack(">HHHHH", sval(COUNTRY), 0x000D, eoff(0), 0, 0),
                    struct.pack(">HHHHH", 0, 0, first_place, 0, 0)]
            pidx = {pk: j + 1 for j, pk in enumerate(self.places)}
            t.s1 = [struct.pack(">HHHHH", 0, 0, s0r(0), 0, 0)]
            for pk in self.places:
                t.s1.append(struct.pack(">HHHHH", sval(pk[0]), sval(pk[1] or COUNTRY), s0r(1),
                                        eoff(self.place_entry[pk]), eoff(self.state_entry[pk])))
            t.s1.append(struct.pack(">HHHHH", 0, 0, s0r(0), first_street, names_end))
            t.s2 = [struct.pack(">HHHHH", 0, 0, s1r(0), 0, 0)]
            for sk in self.streets:
                first = self.entries[self.street_entry[sk]][0]
                parent = s1r(pidx[sk[2]]) if sk[2] in pidx else s1r(0)
                t.s2.append(struct.pack(">HHHHH", sval(first), 0, parent, eoff(self.street_entry[sk]), 4))
            t.s2.append(struct.pack(">HHHHH", 0, 0, s1r(0), first_state, 4))
        else:
            name_ptr = self.end
        # roads + shapes
        roads = bytearray(rs * (len(self.pieces) + 1))
        s7 = bytearray(6 * self.n7)
        sp = 0
        for i, p in enumerate(self.pieces):
            s = p.seg
            g = net.group[s]
            flags = 0x20 | (g << 8)
            struct.pack_into(">HHHHHHIHHHHH", roads, rs * i,
                             node_off[p.ka], node_off[p.kb], O[7] + 6 * sp,
                             nxt[(i, 0)], nxt[(i, 1)], p.cls(net), s + 1, flags,
                             name_ptr, name_ptr, name_ptr, net.x24[s])
            if detail:
                st = self.street_of.get(s)
                srec = (O[2] + 10 * (st + 1)) if st is not None else O[2]
                struct.pack_into(">HHH", roads, rs * i + 26, srec,
                                 0x1080 if net.urban[s] else 0x1000, name_ptr)
            for q in p.pts[1:-1]:
                struct.pack_into(">HHH", s7, 6 * sp, *rel(q), 0)
                sp += 1
        fence = rs * len(self.pieces)
        struct.pack_into(">HHH", roads, fence, 0 if detail else O[5] + 8 * len(self.real), 0, O[7] + 6 * sp)
        struct.pack_into(">HHH", roads, fence + 18, name_ptr, name_ptr, name_ptr)
        if detail:
            struct.pack_into(">H", roads, fence + 30, name_ptr)
        t.roads, t.s7 = bytes(roads), bytes(s7)
        # groups
        s3 = bytearray(32)
        for g in range(7):
            ri = next((i for i, p in enumerate(self.pieces) if net.group[p.seg] >= g), len(self.pieces))
            ni = next((j for j, k in enumerate(self.real) if self.ming[k] >= g), len(self.real))
            struct.pack_into(">HH", s3, 4 * g, road_off(ri), O[5] + 8 * ni)
        struct.pack_into(">HH", s3, 28, road_off(len(self.pieces)), O[5] + 8 * len(self.real))
        t.s3 = bytes(s3)
        # nodes
        lvnum = self.level.num

        def flags(k, edge):
            # bits 14-15: coarsest level with a node here; low byte: the road count at
            # street level here (0x100 one, 0x500 two, 0 three or more), as on the factory
            P = self.npos[k]
            deg = self.street_degree(P) or len(self.inc[k])
            return (0x1000 | (0x2000 if edge else 0) | LEVEL_CODE[self.coarsest_level(P, lvnum)]
                    | (0x0100 if deg == 1 else 0x0500 if deg == 2 else 0))
        s5 = bytearray(8 * len(self.real))
        for j, k in enumerate(self.real):
            struct.pack_into(">HHHH", s5, 8 * j, *rel(self.npos[k]), road_off(self.inc[k][0][0]), flags(k, False))
        s6 = bytearray(16 * len(self.bnd))
        for j, k in enumerate(self.bnd):
            x, y, prect, pkey, g = self.bnodes[k]
            struct.pack_into(">HHHHIHH", s6, 16 * j, *rel((x, y)), road_off(self.inc[k][0][0]), flags(k, True),
                             addr((lvnum, prect)), self.partner_off(pkey, prect), 0)
        t.s5, t.s6 = bytes(s5), bytes(s6)
        # children, neighbours
        s8 = bytearray(4 * t.n8)
        if t.n8:
            v = self.finest_child
            nx, ny = self.slots
            for ix in range(nx):
                for iy in range(ny):
                    px, py = x0 + ix * v + v // 2, y0 + iy * v + v // 2
                    for c in self.children:
                        cx, cy, cw_, ch_ = c[1]
                        if cx <= px < cx + cw_ and cy <= py < cy + ch_:
                            struct.pack_into(">I", s8, 4 * (ix * ny + iy), addr(c))
                            break
        t.s8 = bytes(s8)
        s9 = bytearray()
        for prect in self.neigh:
            s9 += struct.pack(">IHH", addr((lvnum, prect)), *self.edge_point(prect))
        t.s9 = bytes(s9)
        # header 0x54..0x74
        parent = addr(self.parent) if self.parent else 0
        if detail:
            # 0x60-0x68: distances to the level 1/2/3 network (builder); 0x70 = 0x01010000
            # only with a house-number unit (all 4,149 on the factory), else 0x00010000
            extra = struct.pack(">IIIII", *self.head_words, HEAD_6C, 0x01010000 if self.hn_key else 0x00010000)
            head = struct.pack(">II", addr(self.hn_key) if self.hn_key else 0, parent) + struct.pack(">I", 0) + extra
        else:
            # 0x5C: the smallest tile of the next finer level under this one (the factory's
            # value on 83-96% of coarse tiles; when it differs it is smaller, never larger)
            head = struct.pack(">IIIIIIII", 0, parent, self.finest_child or w, OVERVIEW_CONST,
                               OVERVIEW_CONST, OVERVIEW_CONST, 0, 0x00010000)
        t.head = head
        return ct.write(t, unit_header)

    def edge_point(self, prect):
        """A point on the edge shared with tile prect, in this tile's units."""
        x0, y0, w, h = self.rect
        px, py, pw, ph = prect
        lo_x, hi_x = max(x0, px), min(x0 + w, px + pw)
        lo_y, hi_y = max(y0, py), min(y0 + h, py + ph)
        mx = (lo_x + hi_x) // 2
        my = (lo_y + hi_y) // 2
        return (mx - x0) // cn.Q, (my - y0) // cn.Q

    partner_off = None   # set by the builder: (partner key, partner rect) -> node offset
    street_degree = staticmethod(lambda P: 0)          # set by the builder: roads at P on the street level
    coarsest_level = staticmethod(lambda P, lv: lv)    # set by the builder


def cn_levels_above(lv):
    return COARSER[lv]


class HouseNumbers:
    """0x04 unit for a detail tile: one 'no numbers' record per road."""

    def __init__(self, tile_key, nroads):
        self.tile_key, self.nroads = tile_key, nroads
        self.key = ("hn", tile_key)

    def size_bytes(self):
        return 0x14 + 10 * self.nroads

    def logical(self, addr, unit_header):
        out = bytearray(unit_header) + struct.pack(">HHI", 0x10, self.nroads, addr(self.tile_key))
        for _ in range(self.nroads):
            out += struct.pack(">5H", 0x7FFF, 0x7FFF, 0x7FFF, 0x7FFF, 1)
        return bytes(out)
