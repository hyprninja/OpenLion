"""CARiN road tiles (carindb tables 0x00-0x03): parse to a model and write back.

See docs/29_vz_map_disc.md. Big-endian. Offsets inside a tile are from the first byte
of its 8-byte unit header. A tile is laid out as:

  0x00  unit header (carin.py)
  0x08  15 x (u16 offset, u16 count)  sections s0..s14
  0x44  bbox: i32 x0, y0, x1, y1
  0x54  u32 0
  0x58  u32 address of the parent tile one level up (0 at the top)
  0x5C  6 x u32 (kept as found; see FIELDS_5C)
  0x74  sections, in the order 0 1 2 3 4 5 6 7 9 8 10 11 12 13 14, each 4-aligned

  s0, s1, s2  10-byte name-hierarchy records (country / place+state / street);
              record 0 is a blank, the last one a fence carrying end pointers
  s3          (7 + 1) x (u16 first road of group g, u16 first s5 node of group g)
  s4          roads, 32 bytes (detail) or 26 bytes (overview), plus a fence record
  s5          8-byte nodes inside the tile
  s6          16-byte nodes on the tile edge, linked to the neighbour's copy
  s7          6-byte shape points
  s8          u32 child tile addresses (overview levels)
  s9, s10     8-byte link records
  s14         (u16 string offset, u16 flags) name entries, then NUL-terminated strings
"""
import struct
from dataclasses import dataclass, field

HDR = 0x74
NSEC = 15
ORDER = (0, 1, 2, 3, 4, 5, 6, 7, 9, 8, 10, 11, 12, 13, 14)
REC = {0: 10, 1: 10, 2: 10, 5: 8, 6: 16, 7: 6, 8: 4, 9: 8, 10: 8}
GROUPS = 7


def road_size(detail):
    return 32 if detail else 26


def _align4(n):
    return (n + 3) & ~3


@dataclass
class Tile:
    detail: bool
    bbox: tuple                      # (x0, y0, x1, y1)
    head: bytes                      # 0x54..0x74 (parent address patched by the writer)
    s0: list = field(default_factory=list)   # raw 10-byte records
    s1: list = field(default_factory=list)
    s2: list = field(default_factory=list)
    s3: bytes = b""                  # (GROUPS + 1) * 4 bytes
    roads: bytes = b""               # n roads + fence
    nroads: int = 0
    s5: bytes = b""
    n5: int = 0
    s6: bytes = b""
    n6: int = 0
    s7: bytes = b""
    n7: int = 0
    s8: bytes = b""
    n8: int = 0
    s9: bytes = b""
    n9: int = 0
    s10: bytes = b""
    n10: int = 0
    names: list = field(default_factory=list)  # [(string offset, flags)] as stored
    strings: bytes = b""             # everything after the name entries
    offsets: tuple = ()              # section offsets as parsed (for pointer translation)


def parse(b: bytes, detail: bool) -> Tile:
    secs = [struct.unpack_from(">HH", b, 8 + 4 * i) for i in range(NSEC)]
    t = Tile(detail, struct.unpack_from(">iiii", b, 0x44), bytes(b[0x54:HDR]))
    rs = road_size(detail)

    def raw(i, size):
        o, c = secs[i]
        return bytes(b[o:o + c * size])

    for i in (0, 1, 2):
        o, c = secs[i]
        getattr(t, f"s{i}").extend(bytes(b[o + 10 * k:o + 10 * k + 10]) for k in range(c))
    t.s3 = raw(3, 4)[:0] + bytes(b[secs[3][0]:secs[3][0] + 4 * (secs[3][1] + 1)])
    o, c = secs[4]
    t.nroads = c
    t.roads = bytes(b[o:o + (c + 1) * rs])
    for i in (5, 6, 7, 8, 9, 10):
        o, c = secs[i]
        setattr(t, f"n{i}", c)
        setattr(t, f"s{i}", raw(i, REC[i]))
    if any(secs[i][1] for i in (11, 12, 13)):
        raise ValueError("sections 11-13 are not empty")
    o, c = secs[14]
    t.names = [struct.unpack_from(">HH", b, o + 4 * k) for k in range(c)]
    if c:
        end = len(b.rstrip(b"\0"))
        t.strings = bytes(b[o + 4 * c:end]) + b"\0"
    t.offsets = tuple(o for o, _c in secs)
    return t


def layout(t: Tile):
    """Section offsets the writer will use, in the factory order."""
    rs = road_size(t.detail)
    sizes = {
        0: 10 * len(t.s0), 1: 10 * len(t.s1), 2: 10 * len(t.s2), 3: len(t.s3),
        4: (t.nroads + 1) * rs, 5: 8 * t.n5, 6: 16 * t.n6, 7: 6 * t.n7,
        8: 4 * t.n8, 9: 8 * t.n9, 10: 8 * t.n10, 11: 0, 12: 0, 13: 0,
        14: 4 * len(t.names),
    }
    pos, offs = HDR, [0] * NSEC
    for i in ORDER:
        offs[i] = pos
        pos = _align4(pos + sizes[i]) if i != 14 else pos + sizes[i]
    return offs, pos


def counts(t: Tile):
    return [len(t.s0), len(t.s1), len(t.s2), GROUPS, t.nroads, t.n5, t.n6, t.n7,
            t.n8, t.n9, t.n10, 0, 0, 0, len(t.names)]


def write(t: Tile, unit_header: bytes) -> bytes:
    """Logical tile bytes (unit header + body), unpadded to pages. Pointers inside the
    records must already be correct for layout(t)."""
    offs, end = layout(t)
    out = bytearray(end)
    out[0:8] = unit_header
    for i, (o, c) in enumerate(zip(offs, counts(t))):
        if i == 14 and not t.detail:
            o = 0  # overview tiles carry no names; the factory writes offset 0
        struct.pack_into(">HH", out, 8 + 4 * i, o, c)
    struct.pack_into(">iiii", out, 0x44, *t.bbox)
    out[0x54:HDR] = t.head
    for i in (0, 1, 2):
        recs = getattr(t, f"s{i}")
        out[offs[i]:offs[i] + 10 * len(recs)] = b"".join(recs)
    out[offs[3]:offs[3] + len(t.s3)] = t.s3
    out[offs[4]:offs[4] + len(t.roads)] = t.roads
    for i in (5, 6, 7, 8, 9, 10):
        data = getattr(t, f"s{i}")
        out[offs[i]:offs[i] + len(data)] = data
    for k, (p, f) in enumerate(t.names):
        struct.pack_into(">HH", out, offs[14] + 4 * k, p, f)
    return bytes(out) + t.strings
