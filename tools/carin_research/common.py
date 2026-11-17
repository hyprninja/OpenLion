"""Shared helpers for researching the VY/VZ map disc (docs 29-33).

  from common import Disc
  f = Disc("1-control-factory.iso")          # the original disc
  for m in f.tiles(levels=(0,)):             # decodable road tiles as models
      ...
  bits = f.codec_stream(0x26d7800)           # bit stream of a codec 0x0E street tile

Read-only. Nothing here writes a disc.
"""
import collections
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from openlion import carin, carin_tile as ct  # noqa: E402

FIXED = (0x5168200, 0x5AF9400)          # background + licensing block, never rebuilt


class Tile:
    """A decoded road tile (tables 0x00-0x03) with nodes and roads resolved."""

    def __init__(self, u, b):
        d = u.table == 0
        t = ct.parse(b, d)
        rs = ct.road_size(d)
        self.u, self.b, self.t, self.L, self.bbox, self.offs = u, b, t, u.table, t.bbox, t.offsets
        self.nodes = {}
        for k in range(t.n5):
            x, y, fr, f = struct.unpack_from(">HHHH", t.s5, 8 * k)
            self.nodes[t.offsets[5] + 8 * k] = dict(x=x, y=y, fr=fr, f=f, edge=False, i=k)
        for k in range(t.n6):
            x, y, fr, f, a, po, _z = struct.unpack_from(">HHHHIHH", t.s6, 16 * k)
            self.nodes[t.offsets[6] + 16 * k] = dict(x=x, y=y, fr=fr, f=f, edge=True, nb=a, po=po, i=k)
        self.roads, self.road_at = [], {}
        for k in range(t.nroads):
            off = t.offsets[4] + rs * k
            A, B, sh, nA, nB, cls, rid, fl, n1, n2, n3, x24 = struct.unpack_from(">HHHHHHIHHHHH", b, off)
            sh2 = struct.unpack_from(">H", b, off + rs + 4)[0]
            r = dict(k=k, off=off, A=A, B=B, sh=sh, nA=nA, nB=nB, cls=cls, rid=rid, fl=fl,
                     g=(fl >> 8) & 7, ptrs=(n1, n2, n3), x24=x24,
                     shp=[struct.unpack_from(">HHH", b, p) for p in range(sh, sh2, 6)])
            if d:
                r["srec"], r["attr"], r["np"] = struct.unpack_from(">HHH", b, off + 26)
            self.roads.append(r)
            self.road_at[off] = r
        self.groups = [struct.unpack_from(">HH", b, t.offsets[3] + 4 * i) for i in range(8)]

    def abs(self, x, y):
        return self.bbox[0] + 64 * x, self.bbox[1] + 64 * y


class Disc:
    def __init__(self, iso_path):
        self.img = open(iso_path, "rb").read()
        _f, self.db = carin.read_db(self.img)
        self.units = carin.units(self.db)
        self.by = {u.off: u for u in self.units}

    def unit(self, addr):
        return self.by[(addr >> 8) << 8]

    def decode(self, u):
        return carin.decode(self.db, u)

    def header(self, off):
        """First 0x74 bytes of a road tile; plain even for codec 0x0E units."""
        u = self.by[off]
        return self.db[u.off:u.off + 0x74] if u.codec == carin.CODEC_E else carin.decode(self.db, u)[:0x74]

    def sections(self, off):
        h = self.header(off)
        return [struct.unpack_from(">HH", h, 8 + 4 * i) for i in range(15)]

    def tiles(self, levels=(0, 1, 2, 3), skip_fixed=True):
        for u in self.units:
            if u.table in levels and u.codec != carin.CODEC_E and not (skip_fixed and FIXED[0] <= u.off < FIXED[1]):
                yield Tile(u, carin.decode(self.db, u))

    # -- codec 0x0E street tiles (doc 33) --------------------------------------------------
    def codec_parts(self, off):
        """(raw unit bytes, sections, p0, p1, s3 raw, s9 list, s10 list, bit string)."""
        u = self.by[off]
        b = self.db[u.off:u.off + u.size].rstrip(b"\0")
        secs = [struct.unpack_from(">HH", b, 8 + 4 * i) for i in range(15)]
        p = 0x74
        p0, p1 = b[p], b[p + 1]
        p += 2
        s3 = b[p:p + 32]
        p += 32
        s9 = [struct.unpack_from(">IHH", b, p + 8 * k) for k in range(secs[9][1])]
        p += 8 * secs[9][1]
        s10 = [struct.unpack_from(">IHH", b, p + 8 * k) for k in range(secs[10][1])]
        p += 8 * secs[10][1]
        return b, secs, p0, p1, s3, s9, s10, "".join(f"{x:08b}" for x in b[p:])

    def codec_stream(self, off):
        return self.codec_parts(off)[-1]

    def edge_records(self, off):
        """Bit positions of the s6 edge-node records in a codec street tile:
        [(bit position of the 32-bit neighbour address, address, partner offset)], in s6
        order. Exactly n6 per tile on all 5,950 factory codec tiles (doc 33)."""
        _b, secs, _p0, _p1, _s3, s9, _s10, bits = self.codec_parts(off)
        hits = []
        for a, _x, _y in s9:
            nsec = self.sections((a >> 8) << 8)
            o6, n6 = nsec[6]
            s = format(a, "032b")
            i = bits.find(s)
            while i >= 0:
                po = int(bits[i + 32:i + 48], 2)
                if o6 <= po < o6 + 16 * n6 and (po - o6) % 16 == 0:
                    hits.append((i, a, po))
                i = bits.find(s, i + 1)
        return sorted(hits)


def street_search_refs(disc):
    """tile offset -> [(street name, flags, road offset, road count)] from plain 0x0E units."""
    out = collections.defaultdict(list)
    for u in disc.units:
        if u.table != 0x0E or u.codec != carin.ZLIB:
            continue
        b = carin.decode(disc.db, u)
        (o0, n0), (_o1, _n1), (_o2, _n2) = [struct.unpack_from(">HH", b, 8 + 4 * i) for i in range(3)]
        for k in range(n0):
            p, fl, _p2, r = struct.unpack_from(">HHHH", b, o0 + 8 * k)
            first, cf = struct.unpack_from(">HH", b, r)
            name = b[p:b.index(b"\0", p)].decode("latin1")
            for q in range(cf >> 8):
                _x, _y, _h1, _h2, _h3, _h4, tile, ro, rc = struct.unpack_from(">iiHHHHIHH", b, first + 24 * q)
                out[(tile >> 8) << 8].append((name, fl, ro, rc))
    return out


def lonlat(x, y):
    return carin.to_lonlat(x, y)


def heading(dx, dy):
    """Degrees clockwise from north (y grows north)."""
    return math.degrees(math.atan2(dx, dy)) % 360
