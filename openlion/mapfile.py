"""Parser for Siemens/Continental "Ordinary Map File" (mp0, mg1-mg4).
Layout from au.mod (schema offsets 0x153E-0x5230).

  0x00  common header (0x5E)       rec_cnt = parcel count
  0x5E  mapHeader (0x20):
          i32 min_long, min_lat, max_long, max_lat   (deg * 1e5)
          u16 layer, u32 pcl_cnt, i32 kd_cnt, u16 kd_depth,
          u16 max_tmcp_chain_len, u16 max_tmcp_location_size   (5.2.1)
          u32 maxPclId = pcl_cnt - 1 (same 4 bytes)              (5.3)
  0x7E  pcl_dir: kd_cnt x kd_node (13 bytes):
          i32 edge (cut coordinate), u32 lo_index, u32 hi_index, u8 kd_info
          kd_info bits: 0 kd_cut, 2 lo_empty, 3 lo_parcel, 6 hi_empty, 7 hi_parcel
  ....  pcl_hndl_list: pcl_cnt x { i32 offset, u16 org_byte_cnt, u16 zip_byte_cnt }
  ....  parcel_data: zlib streams
"""
import struct
import sys
import zlib

from . import schema

MAPHDR = struct.Struct("<iiiiHIiHHH")  # 32 bytes
KD = struct.Struct("<iIIB")           # 13 bytes
HNDL = struct.Struct("<iHH")          # 8 bytes


class MapFile:
    def __init__(self, data: bytes):
        self.d = data
        self.dbID, self.fileID, self.rec_cnt, self.byte_cnt = struct.unpack_from("<IHII", data, 0x50)
        (self.min_long, self.min_lat, self.max_long, self.max_lat, self.layer,
         self.pcl_cnt, self.kd_cnt, self.kd_depth, self.max_tmcp_chain_len,
         self.max_tmcp_location_size) = MAPHDR.unpack_from(data, 0x5E)
        off = 0x5E + MAPHDR.size
        self.kd = [KD.unpack_from(data, off + i * KD.size) for i in range(self.kd_cnt)]
        off += self.kd_cnt * KD.size
        self.hndl_off = off
        self.handles = [HNDL.unpack_from(data, off + i * HNDL.size) for i in range(self.pcl_cnt)]
        self.data_off = off + self.pcl_cnt * HNDL.size

    def parcel_raw(self, i):
        o, org, z = self.handles[i]
        return self.d[o:o + z], org

    def parcel(self, i):
        z, org = self.parcel_raw(i)
        out = zlib.decompress(z)
        if len(out) != org:
            raise ValueError(f"parcel {i}: {len(out)} != org_byte_cnt {org}")
        return out


OFFS_NAMES = [
    "pos", "alt", "exc_pos", "exc_alt", "node", "seg", "attr", "abs_addr",
    "short_addr", "long_addr", "adas", "adas_speed", "adas_oot",
]


class Parcel:
    """Decompressed parcel. Starts with pcl_offs (u16 (list_offset, count) pairs;
    shape_list has an extra shape_size). 318 bytes in datamodel 5.2.1, 326 in 5.3."""

    def __init__(self, raw: bytes):
        self.r = raw
        self.schema = schema.for_parcel(raw)
        L = parcel_lists(raw)
        self.offs = {n: (L[n + "_list"], L[n + "_cnt"]) for n in OFFS_NAMES}
        self.offs["shape"] = (L["shape_list"], L["shape_cnt"])
        self.shape_size = L["shape_size"]
        self.offs["turn_res"] = (L["turn_res_list"], L["turn_res_cnt"])

    def positions(self, which="pos"):
        """One or more blocks, each: u16 n, i32 lon, i32 lat, n x (i16 dlon, i16 dlat).
        Deltas are cumulative from the block's anchor. Blocks repeat until
        `cnt` points have been read."""
        off, cnt = self.offs[which]
        pts = []
        while len(pts) < cnt:
            n, x, y = struct.unpack_from("<Hii", self.r, off)
            off += 10
            pts.append((x, y))
            for i in range(n):
                a, b = struct.unpack_from("<hh", self.r, off)
                off += 4
                x += a
                y += b
                pts.append((x, y))
        self._pos_end = off
        return pts

    def nodes(self):
        """Node records are 10 bytes; node index i <-> position i.
        u8 nodeinfo0: count:4 (number of arms), sHands:4
        count < 5 : snode, 1 record:  u8 nodeinfo1 (sRank:3, sDepth:5), 4 x u16 slot
        count >= 5: lnode, 2 records (20 bytes, takes indices i and i+1; position
                    i+1 duplicates position i): u8 lHands (8-bit), u8 nodeinfo2,
                    u8 nodeinfo3, 8 x u16 slot
        First `count` slots are the arms: seg index = low 12 bits, top 4 = flags.
        hands bit k = 1 when the node is arm k's higher-index ("right") end.
        The continuation index is returned as {"continuation": True}."""
        off, cnt = self.offs["node"]
        out = []
        while len(out) < cnt:
            b = self.r[off]
            c = b & 15
            if c < 5:
                slots = struct.unpack_from("<4H", self.r, off + 2)[:c]
                info, hands, long_ = self.r[off + 1:off + 2], b >> 4, False
                off += 10
            else:
                slots = struct.unpack_from("<8H", self.r, off + 4)[:c]
                info, hands, long_ = self.r[off + 2:off + 4], self.r[off + 1], True
                off += 20
            out.append({"count": c, "hands": hands, "info": info, "long": long_,
                        "segs": [v & 0xFFF for v in slots],
                        "seg_flags": [v >> 12 for v in slots]})
            if long_:
                out.append({"count": 0, "hands": 0, "info": b"", "long": False,
                            "continuation": True, "segs": [], "seg_flags": []})
        self._node_end = off
        return out

    def seg_ends(self):
        """{seg: [node indices]} built from the node arm lists (sorted)."""
        ends = {}
        for ni, n in enumerate(self.nodes()):
            for s in n["segs"]:
                ends.setdefault(s, []).append(ni)
        return {s: sorted(v) for s, v in ends.items()}

    def turn_restrictions(self):
        """turn_res_list: u32 each.
        seg:12, direction:1 (meaning TBD), count:3, arm_no_0..4 (3 bits each,
        index into the reached node's arm list), hand:1 (0 = start at the
        segment's lower-index end, 1 = higher-index end). count arms are
        taken at successive nodes; the result is a prohibited manoeuvre."""
        off, cnt = self.offs["turn_res"]
        out = []
        for j in range(cnt):
            v = struct.unpack_from("<I", self.r, off + 4 * j)[0]
            c = (v >> 13) & 7
            out.append({"seg": v & 0xFFF, "direction": (v >> 12) & 1, "count": c,
                        "arms": [(v >> (16 + 3 * q)) & 7 for q in range(c)], "hand": v >> 31})
        return out

    def segments(self):
        """seginfo0 bits (LSB first): marker_left, marker_right, turn_restr,
        ext_count:2, seginfoID:3; then ext bytes, [restr_left, restr_right],
        [left_node u16], [right_node u16], i32 length (feet)."""
        off, cnt = self.offs["seg"]
        r = self.r
        out = []
        for _ in range(cnt):
            b = r[off]
            off += 1
            ec = (b >> 3) & 3
            ext = list(r[off:off + ec])
            off += ec
            restr = None
            if b & 4:
                restr = (r[off], r[off + 1])
                off += 2
            ln = rn = None
            if b & 1:
                ln = struct.unpack_from("<H", r, off)[0]
                off += 2
            if b & 2:
                rn = struct.unpack_from("<H", r, off)[0]
                off += 2
            length = struct.unpack_from("<i", r, off)[0]
            off += 4
            out.append({"seginfoID": b >> 5, "ext": ext, "restr": restr,
                        "left_node": ln, "right_node": rn, "length_ft": length})
        self._seg_end = off
        return out


    # ---- shapes, names, speeds -------------------------------------------

    def shapes(self):
        """shape_list: (shape_cnt+1) x u16 offsets (relative to list start,
        last = shape_size), then per shape n x (i16 dlon, i16 dlat).
        Shape k belongs to segment k (shaped segments come first). Points are
        cumulative from the segment's lower-indexed node (or its in-parcel
        node when the other end is a border node). mg1-4 have no shapes."""
        off, cnt = self.offs["shape"]
        if not cnt:
            return []
        offs = struct.unpack_from(f"<{cnt + 1}H", self.r, off)
        return [[struct.unpack_from("<hh", self.r, off + a + 4 * j) for j in range((b - a) // 4)]
                for a, b in zip(offs, offs[1:])]

    def name_links(self):
        """abs_addr {u16 seg, u8 nameinfo, u32 roadID} (7 bytes),
        short_addr  + {u8 addrinfo, u8 delta, u16 base} (11 bytes),
        long_addr   + {u8 addrinfo, u8 msb_base, u16 lsw_base, u16 delta} (13 bytes).
        roadID = byte offset into auz.rd, relative to the end of its 0x5E header."""
        out = []
        for name, size in (("abs_addr", 7), ("short_addr", 11), ("long_addr", 13)):
            off, cnt = self.offs[name]
            for i in range(cnt):
                seg, ni, rid = struct.unpack_from("<HBI", self.r, off + size * i)
                out.append({"seg": seg, "nameinfo": ni, "road_off": rid + 0x5E, "kind": name,
                            "range": self.r[off + size * i + 7:off + size * (i + 1)]})
        return out

    def speed_limits(self):
        """adas list: u16 seg, u8 content (bit1 speed_restricted, bits2-3
        no_of_lanes_present), [u8 speed km/h], [u8 lanes], u8 no_of_speed_limits."""
        off, cnt = self.offs["adas"]
        out = []
        for _ in range(cnt):
            seg, content = struct.unpack_from("<HB", self.r, off)
            off += 3
            speed = lanes = None
            if content & 2:
                speed = self.r[off]
                off += 1
            if (content >> 2) & 3:
                lanes = self.r[off]
                off += 1
            n_lim = self.r[off]
            off += 1
            off += bool(content & 0x40) + bool(content & 0x80)   # 5.3 directional speeds
            out.append({"seg": seg, "speed_kmh": speed, "lanes": lanes, "n_limits": n_lim})
        self._adas_end = off
        return out


def parcel_lists(raw):
    """All (offset, count) list fields of pcl_offs by schema name."""
    sch = schema.for_parcel(raw)
    u = struct.unpack_from(f"<{sch.n_fields}H", raw, 0)
    return dict(zip(sch.pcl_fields, u))


PCL_OFFS_FIELDS = schema.V521.pcl_fields   # 5.2.1 names; see schema.py for 5.3


def signposts(raw):
    """attr_list: rec_len u16, seg u16, attrID u16 (1 = sign), then for signs:
    u8 signInfo (signLevel:2, signType:3; type 1 = route/road, 4 = destination),
    u16 msgLen, char exitNum[8] (e.g. 'W4'), msg[msgLen] + NUL, padded to even.
    msg: elements separated by '/', each '4^<display text>$5^<phonetic>'."""
    L = parcel_lists(raw)
    off, out = L["attr_list"], []
    for _ in range(L["attr_cnt"]):
        rl, seg, aid = struct.unpack_from("<HHH", raw, off)
        rec = {"seg": seg, "attrID": aid}
        if aid == 1:
            si = raw[off + 6]
            ml = struct.unpack_from("<H", raw, off + 7)[0]
            msg = raw[off + 17:off + 17 + ml].decode("latin-1")
            rec.update(signLevel=si & 3, signType=(si >> 2) & 7,
                       exit=raw[off + 9:off + 17].rstrip(b"\0").decode("latin-1"),
                       text=[e.split("$5^")[0].removeprefix("4^") for e in msg.split("/")],
                       raw_msg=msg)
        else:
            rec["data"] = raw[off + 6:off + rl]
        out.append(rec)
        off += rl
    return out


def lanes(raw):
    """lane_offs {u16 seg, u16 byte offset into lane_list}; lanes per segment =
    gap to next offset / 3.  lane (3 bytes): direction:3 (3 = left->right,
    2 = right->left), divider:3, exit_entrance:2, arrows:10, lane_use:1
    (AUNZ 2010: divider/exit/arrows/use all at 'unspecified' defaults).
    lane_conn (7 bytes): seg:12, hand:1 (end/node, as turn_res), turn_direction:5
    (always 31), u16 pair_offset, u16 arm_offset ->
    lane_pair u8 (lane_from:4, lane_to:4, 1-based), lane_arm u8 (arm1:4 1-based
    index into the node's arms, arm2:4, 15 = none)."""
    L = parcel_lists(raw)
    lo, lc, la, ln = L["lane_offs_list"], L["lane_offs_cnt"], L["lane_list"], L["lane_cnt"]
    offs = [struct.unpack_from("<HH", raw, lo + 4 * k) for k in range(lc)]
    srt = sorted({o for _, o in offs}) + [3 * ln]
    nl = {o: (b - o) // 3 for o, b in zip(srt, srt[1:])}
    seg_lanes = {}
    for s, o in offs:
        seg_lanes[s] = []
        for k in range(nl[o]):
            v = int.from_bytes(raw[la + o + 3 * k:la + o + 3 * k + 3], "little")
            seg_lanes[s].append({"direction": v & 7, "divider": (v >> 3) & 7,
                                 "exit_entrance": (v >> 6) & 3, "arrows": (v >> 8) & 0x3FF,
                                 "lane_use": (v >> 18) & 1})
    co, cc = L["lane_conn_list"], L["lane_conn_cnt"]
    po, ao = L["lane_pair_list"], L["lane_arm_list"]
    conns = []
    for k in range(cc):
        c = raw[co + 7 * k:co + 7 * k + 7]
        v = int.from_bytes(c[:3], "little")
        p_, a_ = struct.unpack_from("<HH", c, 3)
        conns.append({"seg": v & 0xFFF, "hand": (v >> 12) & 1, "turn_direction": (v >> 13) & 31,
                      "lane_from": raw[po + p_] & 15, "lane_to": raw[po + p_] >> 4,
                      "arm1": raw[ao + a_] & 15, "arm2": raw[ao + a_] >> 4})
    return seg_lanes, conns


def seginfo_index(seg):
    v = seg["seginfoID"]
    for j, e in enumerate(seg["ext"]):
        v |= e << (3 + 8 * j)
    return v


SEGINFO_FIELDS = (  # (name, bit offset, width) of the u32 records in auz.si
    ("rank", 0, 3), ("class", 3, 4), ("divided", 7, 1), ("drivable_lower", 8, 1),
    ("drivable_upper", 9, 1), ("toll_vignette", 10, 1), ("toll_road", 11, 1),
    ("tollbooth_direction", 12, 2), ("hwy_complex", 14, 1), ("restclass", 19, 3),
    ("landmark", 22, 3), ("dbldig", 25, 1), ("detailedcity", 26, 1), ("urban", 27, 1),
    ("bifurcation_left", 28, 1), ("bifurcation_right", 29, 1), ("rnc", 30, 1),
    ("truck_digitized", 31, 1),
)


def load_seginfo(si_decompressed: bytes):
    cnt = struct.unpack_from("<I", si_decompressed, 0x56)[0]
    return struct.unpack_from(f"<{cnt}I", si_decompressed, 0x5E)


def seginfo_fields(v):
    return {n: (v >> o) & ((1 << w) - 1) for n, o, w in SEGINFO_FIELDS}


if __name__ == "__main__":
    m = MapFile(open(sys.argv[1], "rb").read())
    print(f"layer {m.layer} parcels {m.pcl_cnt} kd {m.kd_cnt} depth {m.kd_depth} "
          f"bbox {m.min_long/1e5:.3f},{m.min_lat/1e5:.3f} .. {m.max_long/1e5:.3f},{m.max_lat/1e5:.3f}")
    print("data starts", m.data_off, "first handle", m.handles[0])
