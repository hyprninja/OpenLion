"""Writers for map files (mp0, mg1-mg4) and their parcels.

Design: every list codec has decode() -> python objects and encode(objects) -> bytes,
and decode keeps any bytes whose meaning is unknown (e.g. unused node slots) so that
factory parcels re-encode byte-for-byte. New (OSM) data simply leaves those at 0.
"""
import struct
import zlib

from . import schema
from .common import build_header
from .mapfile import HNDL, KD, MAPHDR, PCL_OFFS_FIELDS

# 5.2.1 layout constants, kept for callers that predate schema.py
OFFS_LEN = schema.V521.offs_len    # 318 = 159 x u16
N_FIELDS = schema.V521.n_fields    # 83 named fields, then the trailer
LIST_FIELDS = schema.V521.list_fields
ADAS2_LIST = N_FIELDS              # trailer u16[0] is adas2_list: offset of parcel end


# ---- parcel container ------------------------------------------------------

class ParcelContainer:
    """pcl_offs (named u16 fields + trailer) + list blobs in field order.

    Lists are contiguous in field order starting right after pcl_offs. A list field
    whose value is out of order (mg layers: junction_view_* = 0) is kept as a fixed
    value. The field set depends on the datamodel (schema.py): 83 fields + 76-u16
    trailer in 5.2.1, 87 + 76 in 5.3, 77 + 37 in the MY10 compact layout."""

    def __init__(self, fields, trailer, blobs, fixed, sch=None):
        self.schema = sch or schema.active()
        self.fields = list(fields)      # n_fields u16
        self.trailer = list(trailer)    # trailer_len u16 (trailer[0] recomputed)
        self.blobs = dict(blobs)        # field index -> bytes
        self.fixed = set(fixed)         # list field indices not laid out
        if len(self.fields) != self.schema.n_fields:
            raise ValueError(f"{len(self.fields)} pcl_offs fields for schema {self.schema.name}")

    @classmethod
    def decode(cls, raw: bytes):
        sch = schema.for_parcel(raw)
        u = struct.unpack_from(f"<{sch.n_offs}H", raw, 0)
        fields, trailer = u[:sch.n_fields], u[sch.n_fields:]
        order, fixed, prev = [], set(), sch.offs_len
        for k in sch.list_fields:
            if prev <= u[k] <= len(raw):
                order.append(k)
                prev = u[k]
            else:
                fixed.add(k)
        if trailer[0] != len(raw):
            raise ValueError("adas2_list is not the parcel end")
        ends = [u[k] for k in order[1:]] + [len(raw)]
        blobs = {k: raw[u[k]:e] for k, e in zip(order, ends)}
        return cls(fields, trailer, blobs, fixed, sch)

    def encode(self) -> bytes:
        sch = self.schema
        f = list(self.fields)
        pos, body = sch.offs_len, []
        for k in sch.list_fields:
            if k in self.fixed:
                continue
            f[k] = pos
            b = self.blobs.get(k, b"")
            body.append(b)
            pos += len(b)
        t = list(self.trailer)
        t[0] = pos
        return struct.pack(f"<{sch.n_offs}H", *(f + t)) + b"".join(body)

    # convenience: named access
    def idx(self, name):
        return self.schema.idx[name]

    def get(self, name):
        return self.blobs.get(self.idx(name), b"")

    def layer0(self):
        """True for an mp0 parcel: only mp0 fills shape_size (2 when it has no shapes)."""
        return self.fields[self.idx("shape_size")] != 0

    def set(self, name, blob, count=None, count_field=None):
        self.blobs[self.idx(name)] = blob
        if count is not None:
            cf = count_field or name.replace("_list", "_cnt")
            self.fields[self.idx(cf)] = count


# ---- MY10 compact encoding (schema.V521_COMPACT) ---------------------------

def _compact():
    return schema.active().compact


def read_uv(b, off):
    """Unsigned varint: 7 bits per byte, low bits first, high bit = more bytes."""
    v = s = 0
    while True:
        x = b[off]
        off += 1
        v |= (x & 127) << s
        s += 7
        if x < 128:
            return v, off


def write_uv(v, out):
    while v > 127:
        out.append((v & 127) | 128)
        v >>= 7
    out.append(v)


def read_pair(b, off):
    """Signed (dlon, dlat) pair. First byte: bit7 more bytes, bit6 lon < 0, bit5 lat < 0,
    bits0-4 payload; then 7-bit continuation bytes. Payload bits alternate lat, lon
    (lat in the even bits), lowest first."""
    x = b[off]
    off += 1
    neg_x, neg_y = x & 64, x & 32
    v, s = x & 31, 5
    while x >= 128:
        x = b[off]
        off += 1
        v |= (x & 127) << s
        s += 7
    lon = lat = 0
    for k in range(0, s, 2):
        lat |= ((v >> k) & 1) << (k >> 1)
        lon |= ((v >> (k + 1)) & 1) << (k >> 1)
    return (-lon if neg_x else lon, -lat if neg_y else lat), off


def write_pair(dx, dy, out):
    ax, ay = abs(dx), abs(dy)
    v, k = 0, 0
    while ax or ay:
        v |= ((ay & 1) << (2 * k)) | ((ax & 1) << (2 * k + 1))
        ax >>= 1
        ay >>= 1
        k += 1
    first = (v & 31) | (64 if dx < 0 else 0) | (32 if dy < 0 else 0)
    v >>= 5
    out.append(first | (128 if v else 0))
    while v:
        out.append((v & 127) | (128 if v > 127 else 0))
        v >>= 7


ADDR_KINDS = (("abs_addr", 0), ("short_addr", 4), ("long_addr", 6))   # range bytes


# ---- list codecs -----------------------------------------------------------

def decode_positions(blob: bytes):
    """-> list of blocks [(anchor_lon, anchor_lat, [(dlon, dlat), ...])].
    A block is u16 n, i32 lon, i32 lat, then n deltas: i16 pairs, or (compact) read_pair."""
    compact = _compact()
    blocks, off = [], 0
    while off < len(blob):
        n, x, y = struct.unpack_from("<Hii", blob, off)
        off += 10
        if compact:
            d = []
            for _ in range(n):
                p, off = read_pair(blob, off)
                d.append(p)
        else:
            d = [struct.unpack_from("<hh", blob, off + 4 * i) for i in range(n)]
            off += 4 * n
        blocks.append((x, y, d))
    return blocks


def encode_positions(blocks) -> bytes:
    compact = _compact()
    out = bytearray()
    for x, y, d in blocks:
        out += struct.pack("<Hii", len(d), x, y)
        for a, b in d:
            if compact:
                write_pair(a, b, out)
            else:
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
    """Node records (10 B; long node = 2 records). Keeps all slots + info bytes.
    Compact: u8 b0 + 1 info byte (short) or 3 (long), then count varint slots."""
    if _compact():
        return _decode_nodes_compact(blob, cnt)
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


def _decode_nodes_compact(blob, cnt):
    out, off = [], 0
    while len(out) < cnt:
        b = blob[off]
        c = b & 15
        k = 1 if c < 5 else 3
        info = blob[off + 1:off + 1 + k]
        off += 1 + k
        slots = []
        for _ in range(c):
            v, off = read_uv(blob, off)
            slots.append(v)
        out.append({"long": c >= 5, "b0": b, "info": info, "slots": slots})
        if c >= 5:
            out.append({"continuation": True})
    if off != len(blob):
        raise ValueError("node framing")
    return out


def encode_nodes(nodes) -> bytes:
    out = bytearray()
    if _compact():
        for n in nodes:
            if n.get("continuation"):
                continue
            out.append(n["b0"])
            out += n["info"]
            for v in n["slots"][:n["b0"] & 15]:
                write_uv(v, out)
        return bytes(out)
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


def decode_segments(blob: bytes, cnt: int, layer0: bool = False, compact=None):
    """Segment records. Compact: length is a varint, and with layer0 (mp0) each record
    also carries "shape" [(dlon, dlat)] and "names" [(kind, road_rel, range bytes)]
    (layer_0_data: varint shape count, read_pair points, big-endian u16 counter
    long:5 short:5 abs:6, then if any name a varint base roadID = the smallest, and per
    name (abs, short, long order) a varint roadID - base plus its range bytes).
    compact defaults to the active schema's."""
    compact = _compact() if compact is None else compact
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
        if compact:
            length, off = read_uv(blob, off)
        else:
            length = struct.unpack_from("<i", blob, off)[0]
            off += 4
        s = {"seginfo": (b >> 5) | sum(e << (3 + 8 * j) for j, e in enumerate(ext)),
             "ext_count": ec, "restr": restr, "left_node": ln, "right_node": rn,
             "length_ft": length}
        if compact and layer0:
            n, off = read_uv(blob, off)
            shape = []
            for _ in range(n):
                p, off = read_pair(blob, off)
                shape.append(p)
            counter = (blob[off] << 8) | blob[off + 1]
            off += 2
            counts = {"long_addr": counter & 31, "short_addr": (counter >> 5) & 31,
                      "abs_addr": counter >> 10}
            names = []
            if counter:
                base, off = read_uv(blob, off)
                for kind, size in ADDR_KINDS:
                    for _ in range(counts[kind]):
                        d, off = read_uv(blob, off)
                        names.append((kind, base + d, bytes(blob[off:off + size])))
                        off += size
            s["shape"], s["names"] = shape, names
        out.append(s)
    if off != len(blob):
        raise ValueError("segment framing")
    return out


def _encode_layer0(s, out):
    shape = s.get("shape") or []
    write_uv(len(shape), out)
    for a, b in shape:
        write_pair(a, b, out)
    names = s.get("names") or []
    by_kind = {k: [n for n in names if n[0] == k] for k, _ in ADDR_KINDS}
    na, ns, nl = (len(by_kind[k]) for k in ("abs_addr", "short_addr", "long_addr"))
    if na > 63 or ns > 31 or nl > 31:
        raise ValueError(f"segment has too many road links ({na} abs, {ns} short, {nl} long)")
    counter = nl | (ns << 5) | (na << 10)
    out += bytes([counter >> 8, counter & 255])
    if counter:
        base = min(n[1] for n in names)
        write_uv(base, out)
        for kind, size in ADDR_KINDS:
            for _, road, rng in by_kind[kind]:
                if len(rng) != size:
                    raise ValueError(f"{kind} range is {len(rng)} bytes, not {size}")
                write_uv(road - base, out)
                out += rng


def encode_segments(segs) -> bytes:
    """Compact layer0 data is written for segments that carry a "shape" key."""
    compact = _compact()
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
        if compact:
            write_uv(s["length_ft"], out)
            if "shape" in s:
                _encode_layer0(s, out)
        else:
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


# House-number range on one side of a segment (report 24). addrinfo low 4 bits: the
# side, seen from the segment's left end (seg_a) towards its right end, and the parity.
# The top 2 bits are the high bits of a signed delta: 10 bits in short_addr, 18 in
# long_addr. base is the number at the left end, base + delta the one at the right end.
HN_CODE = {("right", "even"): 3, ("right", "odd"): 7, ("right", "mixed"): 11,
           ("left", "even"): 12, ("left", "odd"): 13, ("left", "mixed"): 14}
HN_SIDE = {v: k for k, v in HN_CODE.items()}


def encode_range(code, base, delta):
    """-> (kind, range bytes): short_addr when it fits, else long_addr."""
    if 0 <= base <= 0xFFFF and -512 <= delta <= 511:
        v = delta & 0x3FF
        return "short_addr", struct.pack("<BBH", code | (v >> 8) << 6, v & 0xFF, base)
    if not (0 <= base <= 0xFFFFFF and -(1 << 17) <= delta < (1 << 17)):
        raise ValueError(f"house numbers out of range: {base} {delta:+}")
    v = delta & 0x3FFFF
    return "long_addr", struct.pack("<BBHH", code | (v >> 16) << 6, base >> 16, base & 0xFFFF, v & 0xFFFF)


def decode_range(kind, rng):
    """(code, base, delta) of a short_addr / long_addr range, None for abs_addr."""
    if kind == "short_addr":
        ai, d, base = struct.unpack("<BBH", rng)
        v, bits = (ai >> 6) << 8 | d, 10
    elif kind == "long_addr":
        ai, msb, lsw, d = struct.unpack("<BBHH", rng)
        base, v, bits = msb << 16 | lsw, (ai >> 6) << 16 | d, 18
    else:
        return None
    if v >> (bits - 1):
        v -= 1 << bits
    return ai & 15, base, v


def decode_adas(blob: bytes, cnt: int):
    """u16 seg, u8 content, then optional bytes:
    content bit1 speed_restricted -> u8 speed (km/h), bits2-3 no_of_lanes_present -> u8 lanes,
    always u8 no_of_speed_limits, then (datamodel 5.3 only; 0 in 5.2.1)
    bit6 speed_restricted_rl -> u8 speed against digitising direction,
    bit7 speed_restricted_lr -> u8 speed along digitising direction."""
    out, off = [], 0
    for _ in range(cnt):
        seg, content = struct.unpack_from("<HB", blob, off)
        off += 3
        speed = lanes = rl = lr = None
        if content & 2:
            speed = blob[off]
            off += 1
        if (content >> 2) & 3:
            lanes = blob[off]
            off += 1
        n_lim = blob[off]
        off += 1
        if content & 0x40:
            rl = blob[off]
            off += 1
        if content & 0x80:
            lr = blob[off]
            off += 1
        a = {"seg": seg, "content": content, "speed_kmh": speed, "lanes": lanes, "n_limits": n_lim}
        if rl is not None:
            a["speed_rl"] = rl
        if lr is not None:
            a["speed_lr"] = lr
        out.append(a)
    if off != len(blob):
        raise ValueError("adas framing")
    return out


def encode_adas(items) -> bytes:
    out = bytearray()
    for a in items:
        content = a.get("content")
        if content is None:
            content = ((2 if a.get("speed_kmh") is not None else 0) | (4 if a.get("lanes") is not None else 0)
                       | (0x40 if a.get("speed_rl") is not None else 0)
                       | (0x80 if a.get("speed_lr") is not None else 0))
        out += struct.pack("<HB", a["seg"], content)
        if content & 2:
            out.append(a["speed_kmh"])
        if (content >> 2) & 3:
            out.append(a["lanes"])
        out.append(a.get("n_limits", 0))
        if content & 0x40:
            out.append(a["speed_rl"])
        if content & 0x80:
            out.append(a["speed_lr"])
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


# Lists with a codec: (list field, count field, decode(blob, cnt, container), encode(obj))
LIST_CODECS = (
    ("pos_list", "pos_cnt", lambda b, c, pc: decode_positions(b), encode_positions),
    ("exc_pos_list", "exc_pos_cnt", lambda b, c, pc: decode_positions(b), encode_positions),
    ("node_list", "node_cnt", lambda b, c, pc: decode_nodes(b, c), encode_nodes),
    ("seg_list", "seg_cnt", lambda b, c, pc: decode_segments(b, c, pc.layer0()), encode_segments),
    ("shape_list", "shape_cnt", lambda b, c, pc: decode_shapes(b, c), encode_shapes),
    ("adas_list", "adas_cnt", lambda b, c, pc: decode_adas(b, c), encode_adas),
    ("turn_res_list", "turn_res_cnt", lambda b, c, pc: decode_turn_res(b), encode_turn_res),
)


# ---- map file --------------------------------------------------------------

def compress_parcel(raw: bytes) -> bytes:
    return zlib.compress(raw, 9)


def encode_mapfile(template_header: bytes, maphdr: dict, kd_nodes, parcels_zipped, parcel_sizes) -> bytes:
    """Assemble a map file.
    maphdr: min_long, min_lat, max_long, max_lat, layer, kd_depth,
            max_tmcp_chain_len, max_tmcp_location_size (5.2.1 only; 5.3 writes maxPclId).
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
    if schema.active().max_pcl_id:
        # 5.3: u32 maxPclId in place of the two TMC u16s (low half first)
        tail = ((n - 1) & 0xFFFF, (n - 1) >> 16)
    else:
        tail = (maphdr.get("max_tmcp_chain_len", 0), maphdr.get("max_tmcp_location_size", 0))
    mh = MAPHDR.pack(maphdr["min_long"], maphdr["min_lat"], maphdr["max_long"], maphdr["max_lat"],
                     maphdr["layer"], n, len(kd_nodes), maphdr["kd_depth"], *tail)
    byte_cnt = off - data_off
    if schema.active().max_pcl_id and maphdr["layer"] == 0:
        # MY17 factory mp0 stores the whole file size here (its mg layers store the
        # parcel data size, like every 5.2.1 map file). Copied as found.
        byte_cnt = off
    head = build_header(template_header, n, byte_cnt)
    return (head + mh + b"".join(KD.pack(*k) for k in kd_nodes) + b"".join(handles)
            + b"".join(parcels_zipped))
