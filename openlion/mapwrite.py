"""Writers for map files (mp0, mg1-mg4) and their parcels.

Design: every list codec has decode() -> python objects and encode(objects) -> bytes,
and decode keeps any bytes whose meaning is unknown (e.g. unused node slots) so that
factory parcels re-encode byte-for-byte. New (OSM) data simply leaves those at 0.
"""
import struct
import zlib

from .common import build_header
from .mapfile import HNDL, KD, MAPHDR, PCL_OFFS_FIELDS

OFFS_LEN = 318                     # 159 x u16
N_FIELDS = len(PCL_OFFS_FIELDS)    # 83 named fields, then the trailer
LIST_FIELDS = [i for i, n in enumerate(PCL_OFFS_FIELDS) if n.endswith("_list")]
ADAS2_LIST = 83                    # trailer u16[0] is adas2_list: offset of parcel end


# ---- parcel container ------------------------------------------------------

class ParcelContainer:
    """pcl_offs (83 named u16 + 76-u16 trailer) + list blobs in field order.

    Lists are contiguous in field order starting at 318. A list field whose value
    is out of order (mg layers: junction_view_* = 0) is kept as a fixed value."""

    def __init__(self, fields, trailer, blobs, fixed):
        self.fields = list(fields)      # 83 u16
        self.trailer = list(trailer)    # 76 u16 (trailer[0] recomputed)
        self.blobs = dict(blobs)        # field index -> bytes
        self.fixed = set(fixed)         # list field indices not laid out

    @classmethod
    def decode(cls, raw: bytes):
        u = struct.unpack_from("<159H", raw, 0)
        fields, trailer = u[:N_FIELDS], u[N_FIELDS:]
        order, fixed, prev = [], set(), OFFS_LEN
        for k in LIST_FIELDS:
            if prev <= u[k] <= len(raw):
                order.append(k)
                prev = u[k]
            else:
                fixed.add(k)
        if u[ADAS2_LIST] != len(raw):
            raise ValueError("adas2_list is not the parcel end")
        ends = [u[k] for k in order[1:]] + [len(raw)]
        blobs = {k: raw[u[k]:e] for k, e in zip(order, ends)}
        return cls(fields, trailer, blobs, fixed)

    def encode(self) -> bytes:
        f = list(self.fields)
        pos, body = OFFS_LEN, []
        for k in LIST_FIELDS:
            if k in self.fixed:
                continue
            f[k] = pos
            b = self.blobs.get(k, b"")
            body.append(b)
            pos += len(b)
        t = list(self.trailer)
        t[0] = pos
        return struct.pack("<159H", *(f + t)) + b"".join(body)

    # convenience: named access
    def idx(self, name):
        return PCL_OFFS_FIELDS.index(name)

    def get(self, name):
        return self.blobs.get(self.idx(name), b"")

    def set(self, name, blob, count=None, count_field=None):
        self.blobs[self.idx(name)] = blob
        if count is not None:
            cf = count_field or name.replace("_list", "_cnt")
            self.fields[self.idx(cf)] = count


# ---- list codecs -----------------------------------------------------------

def decode_positions(blob: bytes):
    """-> list of blocks [(anchor_lon, anchor_lat, [(dlon, dlat), ...])]."""
    blocks, off = [], 0
    while off < len(blob):
        n, x, y = struct.unpack_from("<Hii", blob, off)
        off += 10
        d = [struct.unpack_from("<hh", blob, off + 4 * i) for i in range(n)]
        off += 4 * n
        blocks.append((x, y, d))
    return blocks


def encode_positions(blocks) -> bytes:
    out = bytearray()
    for x, y, d in blocks:
        out += struct.pack("<Hii", len(d), x, y)
        for a, b in d:
            out += struct.pack("<hh", a, b)
    return bytes(out)


def blocks_from_points(points):
    """Absolute points -> blocks; a new block starts when a delta leaves i16."""
    blocks = []
    for p in points:
        if blocks:
            x0, y0, d = blocks[-1]
            px, py = last
            dx, dy = p[0] - px, p[1] - py
            if -32768 <= dx <= 32767 and -32768 <= dy <= 32767 and len(d) < 65535:
                d.append((dx, dy))
                last = p
                continue
        blocks.append((p[0], p[1], []))
        last = p
    return blocks


def decode_nodes(blob: bytes, cnt: int):
    """Node records (10 B; long node = 2 records). Keeps all slots + info bytes."""
    out, off = [], 0
    while len(out) < cnt:
        b = blob[off]
        c = b & 15
        if c < 5:
            out.append({"long": False, "b0": b, "info": blob[off + 1:off + 2],
                        "slots": list(struct.unpack_from("<4H", blob, off + 2))})
            off += 10
        else:
            out.append({"long": True, "b0": b, "info": blob[off + 1:off + 4],
                        "slots": list(struct.unpack_from("<8H", blob, off + 4))})
            out.append({"continuation": True})
            off += 20
    if off != len(blob):
        raise ValueError("node framing")
    return out


def encode_nodes(nodes) -> bytes:
    out = bytearray()
    for n in nodes:
        if n.get("continuation"):
            continue
        out.append(n["b0"])
        out += n["info"]
        slots = list(n["slots"]) + [0] * ((8 if n["long"] else 4) - len(n["slots"]))
        out += struct.pack("<8H" if n["long"] else "<4H", *slots)
    return bytes(out)


def make_node(arms, hands_mask, rank=0, depth=0):
    """New node from arms [(seg_idx, flags4)]; hands bit k = node is arm k's right end."""
    c = len(arms)
    slots = [(s & 0xFFF) | ((fl & 15) << 12) for s, fl in arms]
    info = (rank & 7) | ((depth & 31) << 3)
    if c < 5:
        return {"long": False, "b0": c | ((hands_mask & 15) << 4), "info": bytes([info]), "slots": slots}
    if c > 8:
        raise ValueError("node with more than 8 arms")
    return {"long": True, "b0": c, "info": bytes([hands_mask & 0xFF, info, 0]), "slots": slots}


SEG_FMT = None


def decode_segments(blob: bytes, cnt: int):
    out, off = [], 0
    for _ in range(cnt):
        b = blob[off]
        off += 1
        ec = (b >> 3) & 3
        ext = list(blob[off:off + ec])
        off += ec
        restr = ln = rn = None
        if b & 4:
            restr = (blob[off], blob[off + 1])
            off += 2
        if b & 1:
            ln = struct.unpack_from("<H", blob, off)[0]
            off += 2
        if b & 2:
            rn = struct.unpack_from("<H", blob, off)[0]
            off += 2
        length = struct.unpack_from("<i", blob, off)[0]
        off += 4
        out.append({"seginfo": (b >> 5) | sum(e << (3 + 8 * j) for j, e in enumerate(ext)),
                    "ext_count": ec, "restr": restr, "left_node": ln, "right_node": rn,
                    "length_ft": length})
    if off != len(blob):
        raise ValueError("segment framing")
    return out


def encode_segments(segs) -> bytes:
    out = bytearray()
    for s in segs:
        si = s["seginfo"]
        ec = s.get("ext_count")
        if ec is None:
            ec = 0 if si < 8 else (1 if si < 2048 else 2)
        b = ((si & 7) << 5) | (ec << 3)
        b |= 4 if s["restr"] is not None else 0
        b |= 1 if s["left_node"] is not None else 0
        b |= 2 if s["right_node"] is not None else 0
        out.append(b)
        for j in range(ec):
            out.append((si >> (3 + 8 * j)) & 0xFF)
        if s["restr"] is not None:
            out += bytes(s["restr"])
        if s["left_node"] is not None:
            out += struct.pack("<H", s["left_node"])
        if s["right_node"] is not None:
            out += struct.pack("<H", s["right_node"])
        out += struct.pack("<i", s["length_ft"])
    return bytes(out)


def decode_shapes(blob: bytes, cnt: int):
    """(cnt+1) x u16 offsets (always present; empty list = [2]), then i16 point pairs."""
    if not blob:
        return None                     # mg layers: no shape table at all
    offs = struct.unpack_from(f"<{cnt + 1}H", blob, 0)
    if offs[-1] != len(blob):
        raise ValueError("shape framing")
    return [[struct.unpack_from("<hh", blob, a + 4 * j) for j in range((b - a) // 4)]
            for a, b in zip(offs, offs[1:])]


def encode_shapes(shapes) -> bytes:
    """mp0 always writes the offset table (empty = b'\\x02\\x00'); pass None for mg layers."""
    if shapes is None:
        return b""
    pos = 2 * (len(shapes) + 1)
    offs, body = [], bytearray()
    for sh in shapes:
        offs.append(pos)
        for a, b in sh:
            body += struct.pack("<hh", a, b)
        pos += 4 * len(sh)
    offs.append(pos)
    return struct.pack(f"<{len(offs)}H", *offs) + bytes(body)


def decode_fixed(blob: bytes, size: int):
    if len(blob) % size:
        raise ValueError("fixed-size list framing")
    return [blob[i:i + size] for i in range(0, len(blob), size)]


def encode_addr(seg, road_id_rel, nameinfo=0, rng=b""):
    """abs_addr (rng empty, 7 B), short_addr (+4 B), long_addr (+6 B)."""
    return struct.pack("<HBI", seg, nameinfo, road_id_rel) + rng


def decode_adas(blob: bytes, cnt: int):
    out, off = [], 0
    for _ in range(cnt):
        seg, content = struct.unpack_from("<HB", blob, off)
        off += 3
        speed = lanes = None
        if content & 2:
            speed = blob[off]
            off += 1
        if (content >> 2) & 3:
            lanes = blob[off]
            off += 1
        n_lim = blob[off]
        off += 1
        out.append({"seg": seg, "content": content, "speed_kmh": speed, "lanes": lanes, "n_limits": n_lim})
    if off != len(blob):
        raise ValueError("adas framing")
    return out


def encode_adas(items) -> bytes:
    out = bytearray()
    for a in items:
        content = a.get("content")
        if content is None:
            content = (2 if a.get("speed_kmh") is not None else 0) | (4 if a.get("lanes") is not None else 0)
        out += struct.pack("<HB", a["seg"], content)
        if content & 2:
            out.append(a["speed_kmh"])
        if (content >> 2) & 3:
            out.append(a["lanes"])
        out.append(a.get("n_limits", 0))
    return bytes(out)


def encode_turn_res(items) -> bytes:
    out = bytearray()
    for t in items:
        v = (t["seg"] & 0xFFF) | ((t["direction"] & 1) << 12) | ((len(t["arms"]) & 7) << 13)
        # unused arm slots: factory files carry leftover values there; new data writes 0
        slots = list(t["arms"]) + list(t.get("unused_arms", []))[: 5 - len(t["arms"])]
        for q, a in enumerate(slots):
            v |= (a & 7) << (16 + 3 * q)
        v |= (t["hand"] & 1) << 31
        out += struct.pack("<I", v)
    return bytes(out)


def decode_turn_res(blob: bytes):
    out = []
    for j in range(len(blob) // 4):
        v = struct.unpack_from("<I", blob, 4 * j)[0]
        c = (v >> 13) & 7
        out.append({"seg": v & 0xFFF, "direction": (v >> 12) & 1,
                    "arms": [(v >> (16 + 3 * q)) & 7 for q in range(c)], "hand": v >> 31,
                    "unused_arms": [(v >> (16 + 3 * q)) & 7 for q in range(c, 5)], "_raw": v})
    return out


# ---- map file --------------------------------------------------------------

def compress_parcel(raw: bytes) -> bytes:
    return zlib.compress(raw, 9)


def encode_mapfile(template_header: bytes, maphdr: dict, kd_nodes, parcels_zipped, parcel_sizes) -> bytes:
    """Assemble a map file.
    maphdr: min_long, min_lat, max_long, max_lat, layer, kd_depth,
            max_tmcp_chain_len, max_tmcp_location_size.
    kd_nodes: [(edge, lo, hi, info)]; parcels_zipped: zlib blobs; parcel_sizes: raw sizes."""
    n = len(parcels_zipped)
    off = 0x5E + MAPHDR.size + KD.size * len(kd_nodes) + HNDL.size * n
    data_off = off
    handles = []
    for z, raw_n in zip(parcels_zipped, parcel_sizes):
        if raw_n > 0xFFFF or len(z) > 0xFFFF:
            raise ValueError("parcel larger than 64 KB")
        handles.append(HNDL.pack(off, raw_n, len(z)))
        off += len(z)
    mh = MAPHDR.pack(maphdr["min_long"], maphdr["min_lat"], maphdr["max_long"], maphdr["max_lat"],
                     maphdr["layer"], n, len(kd_nodes), maphdr["kd_depth"],
                     maphdr.get("max_tmcp_chain_len", 0), maphdr.get("max_tmcp_location_size", 0))
    head = build_header(template_header, n, off - data_off)
    return (head + mh + b"".join(KD.pack(*k) for k in kd_nodes) + b"".join(handles)
            + b"".join(parcels_zipped))
