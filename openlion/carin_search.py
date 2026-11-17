"""CARiN destination search, rebuilt for new road tiles. See docs/29_vz_map_disc.md.

  0x0B country trie (fixed at 0xE00, kept) -> 0x0A country record (fixed at 0x1000,
  patched: place trie address, POI links cleared)
  0x0D place trie   entries u32 unit, u8 char, u8 leaf, u16 offset, u16 count, u16 0
  0x0C places       s0 8 B (name, flags, area name, s1 record), s1 20 B (u32 0x0F unit,
                    u16 offset, u16 count, u16 POI offset, u16 POI count, u32 tile,
                    u16 road offset, u16 0); units chained by next/prev at 0x20/0x24
  0x0F street tries same entry format as 0x0D; one slice per place
  0x0E streets      s0 8 B (name, flags, area name, s1 record), s1 4 B (u16 first s2
                    entry, u8 count, u8 has numbers), s2 24 B (i32 tile x0, i32 tile y0,
                    4 x u16 house numbers, u32 tile, u16 first road, u16 road count)

Place record 0 of the first 0x0C unit is ' *': every street in the country, named
"street, suburb". POIs are not rebuilt, so every POI link is empty.
"""
import collections
import struct

from . import carin
from . import carin_tile as ct

START = 0x1200
MAX_LEAF = 250
UNIT_BYTES = 24000          # estimate; assemble enforces the real 32 KB ceiling
LEAF_BYTES = 20000          # a leaf heavier than this is split by the next letter
TRIE_UNIT_ENTRIES = 2700
NONE = 0x7FFF


def _chars(s):
    return s       # names are already normalised by carin_net.ascii_name


# ---- tries --------------------------------------------------------------------

def make_trie(names, lo, hi, depth=0, weight=None, max_weight=None):
    """Sorted names[lo:hi] -> [(char code, leaf, (lo, hi) | children)]. A leaf holds at
    most MAX_LEAF names and, given weight (bytes per name) and max_weight, at most that
    many bytes, so it always fits in one record unit."""
    out, i = [], lo
    while i < hi:
        ch = ord(names[i][depth]) if depth < len(names[i]) else 0
        j = i
        while j < hi and (ord(names[j][depth]) if depth < len(names[j]) else 0) == ch:
            j += 1
        heavy = weight is not None and sum(weight[i:j]) > max_weight
        if ch == 0 or j - i == 1 or (j - i <= MAX_LEAF and not heavy) or (depth >= 8 and not heavy):
            out.append((ch, 1, (i, j)))
        else:
            out.append((ch, 0, make_trie(names, i, j, depth + 1, weight, max_weight)))
        i = j
    return out


def leaves(trie):
    for ch, leaf, v in trie:
        if leaf:
            yield v
        else:
            yield from leaves(v)


class TrieUnit:
    """0x0D / 0x0F: slices of trie entries."""

    def __init__(self, key, table):
        self.key, self.table = key, table
        self.entries = []        # (target key, char, leaf, offset, count)

    def size_bytes(self):
        return 0x0C + 12 * len(self.entries)

    def logical(self, addr, unit_header):
        out = bytearray(unit_header) + struct.pack(">HH", 0x0C, len(self.entries))
        for tk, ch, leaf, off, cnt in self.entries:
            out += struct.pack(">IBBHHH", addr(tk), ch, leaf, off, cnt, 0)
        return bytes(out)


class TrieWriter:
    """Packs trie slices (sibling lists) into trie units; each slice stays in one unit."""

    def __init__(self, table, prefix):
        self.table, self.prefix = table, prefix
        self.units = []

    def _unit_for(self, n):
        # factory unit sizes: 0x0F up to 32 KB (2,725 entries), 0x11 up to 24 KB
        cap = 2040 if self.table == 0x11 else TRIE_UNIT_ENTRIES
        if not self.units or len(self.units[-1].entries) + n > cap:
            self.units.append(TrieUnit((self.prefix, len(self.units)), self.table))
        return self.units[-1]

    def add(self, trie, leaf_target):
        """leaf_target((lo, hi)) -> (unit key, offset, count). Returns (unit key, offset, count)
        of the top slice."""
        u = self._unit_for(len(trie))
        start = len(u.entries)
        u.entries += [None] * len(trie)
        for k, (ch, leaf, v) in enumerate(trie):
            if leaf:
                tk, off, cnt = leaf_target(v)
                u.entries[start + k] = (tk, ch, 1, off, cnt)
            else:
                sk, soff, scnt = self.add(v, leaf_target)
                u.entries[start + k] = (sk, ch, 0, soff, scnt)
        return u.key, 0x0C + 12 * start, len(trie)


# ---- record units ---------------------------------------------------------------

class RecordUnit:
    """0x0C (places) or 0x0E (streets): s0 name records, s1, (s2), strings."""

    def __init__(self, key, table):
        self.key, self.table = key, table
        self.recs = []          # (name, flags, area, payload)
        self.next = self.prev = None
        self._strings = None

    def add(self, rec):
        self.recs.append(rec)
        return 0x28 + 8 * (len(self.recs) - 1)

    def _layout(self):
        n = len(self.recs)
        s1 = 0x28 + 8 * n
        if self.table == 0x0E:
            s2 = s1 + 4 * n
            nruns = sum(len(r[3]) for r in self.recs)
            sstart = s2 + 24 * nruns
        else:
            # places: s2 is the POI-link section (s3 on the disc), 12 bytes a link
            s2 = s1 + 20 * n
            nruns = sum(len(r[3][5]) for r in self.recs)
            sstart = s2 + 12 * nruns
        return n, s1, s2, nruns, sstart

    def strings(self):
        if self._strings is None:
            pool, out = {}, bytearray()
            for name, _f, area, _p in self.recs:
                for s in (name, area):
                    if s and s not in pool:
                        pool[s] = len(out)
                        out += s.encode("ascii", "replace") + b"\0"
            self._strings = (pool, bytes(out))
        return self._strings

    def size_bytes(self):
        return self._layout()[4] + len(self.strings()[1])

    def logical(self, addr, unit_header):
        n, s1, s2, nruns, sstart = self._layout()
        pool, sbytes = self.strings()
        out = bytearray(sstart)
        out[:8] = unit_header
        if self.table == 0x0E:
            struct.pack_into(">HHHHHH", out, 8, 0x28, n, s1, n, s2, nruns)
        else:
            # s0, s1, s2 empty, s3 = POI links per category (factory layout)
            struct.pack_into(">HHHHHHHH", out, 8, 0x28, n, s1, n, 0, 0, s2 if nruns else 0, nruns)
        struct.pack_into(">II", out, 0x20, addr(self.next) if self.next else 0,
                         addr(self.prev) if self.prev else 0)
        sp = lambda s: sstart + pool[s] if s else 0
        run = 0
        for i, (name, flags, area, payload) in enumerate(self.recs):
            if self.table == 0x0E:
                struct.pack_into(">HHHH", out, 0x28 + 8 * i, sp(name), flags, sp(area), s1 + 4 * i)
                struct.pack_into(">HBB", out, s1 + 4 * i, s2 + 24 * run, len(payload), 0)
                for (tile_xy, tkey, roff, cnt) in payload:
                    struct.pack_into(">iiHHHHIHH", out, s2 + 24 * run, tile_xy[0], tile_xy[1],
                                     NONE, NONE, NONE, NONE, addr(tkey), roff, cnt)
                    run += 1
            else:
                struct.pack_into(">HHHH", out, 0x28 + 8 * i, sp(name), flags, sp(area), s1 + 20 * i)
                trie_key, toff, tcnt, tkey, roff, pois = payload
                first = s2 + 12 * run if pois else 0
                struct.pack_into(">IHHHHIHH", out, s1 + 20 * i, addr(trie_key), toff, tcnt, first, len(pois),
                                 addr(tkey) if tkey else 0, roff, 0)
                for cat, (pk, po, pc) in pois:
                    struct.pack_into(">IHHHH", out, s2 + 12 * run, addr(pk), po, pc, cat, 0)
                    run += 1
        return bytes(out) + sbytes


def pack_records(recs, trie, table, prefix, est, pool=None):
    """Sorted records -> record units, every trie leaf inside one unit. Returns
    (new units, leaf_target). With a pool (a dict kept by the caller), consecutive calls
    keep filling the same unit instead of each starting a mostly empty one."""
    units, where = [], {}
    pool = pool if pool is not None else {}
    cur, size = pool.get("cur"), pool.get("size", 0)
    for lo, hi in leaves(trie):
        add = sum(est(recs[i]) for i in range(lo, hi))
        if cur is None or (size + add > UNIT_BYTES and cur.recs):
            cur = RecordUnit((prefix, len(units)), table)
            units.append(cur)
            size = 0x28
        first = None
        for i in range(lo, hi):
            off = cur.add(recs[i])
            first = off if first is None else first
        where[(lo, hi)] = (cur.key, first, hi - lo)
        size += add
    pool["cur"], pool["size"] = cur, size
    return units, where.__getitem__


# ---- build -----------------------------------------------------------------------

def collect(net, tiles):
    """(street, place, state) -> [(tile origin, tile key, first road offset, count)]."""
    streets = collections.defaultdict(list)
    for key, rec in tiles.items():
        if not rec.level.detail:
            continue
        rs = ct.road_size(True)
        x0, y0, _w, _h = key[1]
        prev, start = None, 0
        order = rec.pieces + [None]
        for i, p in enumerate(order):
            sk = None
            if p is not None:
                st = rec.street_of.get(p.seg)
                if st is not None:
                    nm, _refs, pk = rec.streets[st]
                    if nm:
                        sk = (_chars(nm), pk[0] if pk else "", pk[1] if pk else "")
            if sk != prev:
                if prev is not None:
                    streets[prev].append(((x0, y0), key, rec.offs[4] + rs * start, i - start))
                prev, start = sk, i
    return streets


def build(factory, net, tiles, labels, poi_links=None):
    """poi_links: {(place, state): [(category, (0x11 unit, offset, count))]} from carin_poi."""
    poi_links = poi_links or {}
    streets = collect(net, tiles)
    by_place = collections.defaultdict(list)
    for (nm, place, state), runs in streets.items():
        by_place[(place, state)].append((nm, runs))
    units = []
    trie_f = TrieWriter(0x0F, "t0f")
    # national list: "street, suburb"
    nat = sorted(((f"{nm}, {place}" if place else nm), runs) for (nm, place, state), runs in streets.items())
    nat_names = [n for n, _r in nat]
    nat_recs = [(n, 0x0002, "", r) for n, r in nat]
    est = lambda r: 8 + 4 + 24 * len(r[3]) + len(r[0]) + 1
    nat_trie = make_trie(nat_names, 0, len(nat_names), 0, [est(r) for r in nat_recs], LEAF_BYTES)
    nat_units, nat_leaf = pack_records(nat_recs, nat_trie, 0x0E, "t0e-all", est)
    nat_slice = trie_f.add(nat_trie, nat_leaf)
    units += nat_units
    # places
    place_rows = []
    place_pool = {}          # suburbs share street units instead of one each
    all_street_units = list(nat_units)
    for (place, state), lst in sorted(by_place.items()):
        if not place:
            continue
        lst.sort()
        names = [nm for nm, _r in lst]
        recs = [(nm, 0x0002, "", r) for nm, r in lst]
        tr = make_trie(names, 0, len(names), 0, [est(r) for r in recs], LEAF_BYTES)
        pu, pleaf = pack_records(recs, tr, 0x0E, ("t0e", place, state), est, place_pool)
        sl = trie_f.add(tr, pleaf)
        units += pu
        all_street_units += pu
        first_run = lst[0][1][0]
        place_rows.append((_chars(place), state, sl, first_run[1], first_run[2]))
    # merge the many small per-place street units into shared ones is left to zlib;
    # chain all street units for scrolling
    for a, b in zip(all_street_units, all_street_units[1:]):
        a.next, b.prev = b.key, a.key
    place_rows.sort()
    p_names = [" *"] + [p for p, *_r in place_rows]
    p_recs = [(" *", 0x0000, " ", (nat_slice[0], nat_slice[1], nat_slice[2], None, 0, []))]
    p_recs += [(p, 0x0002, st, (sl[0], sl[1], sl[2], tk, ro, poi_links.get((p, st), [])))
               for p, st, sl, tk, ro in place_rows]
    p_trie = make_trie(p_names, 0, len(p_names))
    p_units, p_leaf = pack_records(p_recs, p_trie, 0x0C, "t0c", lambda r: 8 + 20 + 12 * len(r[3][5]) + len(r[0]) + len(r[2]) + 2)
    for a, b in zip(p_units, p_units[1:]):
        a.next, b.prev = b.key, a.key
    trie_d = TrieWriter(0x0D, "t0d")
    top = trie_d.add(p_trie, p_leaf)
    build.top = top
    # first unit holds the top slice; the country record points at it
    units = trie_d.units + p_units + trie_f.units + units
    return units


def patch_fixed(factory, db, place):
    """0x0A country record: new place trie, no POI links."""
    top_key, top_off, top_cnt = build.top
    pos, pages = place[top_key]
    u = factory.by[0x1000]
    b = bytearray(carin.decode(db, u))
    struct.pack_into(">IHH", b, 0x30, (pos >> 8) << 8 | pages, top_off, top_cnt)
    struct.pack_into(">HH", b, 0x14, 0, 0)          # s3: POI links
    struct.pack_into(">HH", b, 0x48, 0, 0)          # their offset / count in the record
    b[0x5C:0x5C + 48] = bytes(48)                   # and the four national lists themselves
    db = bytearray(db)
    db[0x1000:0x1200] = carin.encode_in_place(u, bytes(b))
    return bytes(db)
