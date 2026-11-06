"""Datamodel versions. The card's au.mod names its datamodel ("Arriba 5.2.1" on the
2011 GMH card CD_7606, "Arriba 5.3" on the MY17 card CD_8418). The table version
stamps are the same (5.0.5) on both, so au.mod is the only reliable marker.

What 5.3 changes for the tables OpenLion writes:
  auz.rd      road record 29 -> 26 bytes: u16 maxNumber + u16 minNumber become
              u8 hasNumbers (0/1).
  mp0/mg      mapHeader u16 max_tmcp_chain_len + u16 max_tmcp_location_size become
              u32 maxPclId (= pcl_cnt - 1). Same bytes, so only the value differs.
              pcl_offs 159 -> 163 u16: truck_list/_cnt, truck_speed_list/_cnt and
              truck_oot_list/_cnt follow adas_oot_cnt; tmcp_list/tmcp_size are gone.
              adas and adas_speed records gain optional bytes behind content bits
              that 5.2.1 leaves at 0 (see mapwrite.decode_adas).

The MY15 card (CD_8217, AUNZ 2014Q1) sits in between: au.mod and auz.rd are 5.2.1,
but its map files already use the 5.3 parcel layout, maxPclId and the mp0 file-size
byte_cnt. use_card() spots this from auz.mp0's first parcel (V521_MAPS53).

The MY10 card (CD_7172, AUNZ 2009Q4) names the same datamodel as MY12 in au.mod
("Arriba 5.2.1 ...") but packs its map parcels differently (V521_COMPACT, report 21):
  pcl_offs    114 u16: no *_addr_list_2 fields, and a 37-u16 trailer (10 stats triples).
  positions   deltas are variable-length pairs (mapwrite.read_pair) instead of i16 pairs.
  nodes       arm slots are varints, so a node record has no unused slots.
  segments    length is a varint; in mp0 each segment carries its own shape points and
              road links (layer_0_data). The shape and address lists stay empty, but their
              counts (and shape_size, as MY12 would store it) are still filled in.
use_card() spots this from auz.mp0's first parcel, like the MY15 case.

The active schema is a process-wide setting: pick it from the card with use_card()
before reading or writing tables. Parcels carry their own marker (pos_list, the
first list, starts right after pcl_offs), so parcel decoding detects it per parcel.
"""
import os
import struct


def _fields(truck: bool, tmcp: bool, addr2: bool = True):
    s = (
        "pos_list pos_cnt alt_list alt_cnt exc_pos_list exc_pos_cnt exc_alt_list exc_alt_cnt "
        "node_list node_cnt seg_list seg_cnt attr_list attr_cnt abs_addr_list abs_addr_cnt "
        "short_addr_list short_addr_cnt long_addr_list long_addr_cnt adas_list adas_cnt "
        "adas_speed_list adas_speed_cnt adas_oot_list adas_oot_cnt "
    )
    if truck:
        s += ("truck_list truck_cnt truck_speed_list truck_speed_cnt "
              "truck_oot_list truck_oot_cnt ")
    s += "shape_list shape_cnt shape_size turn_res_list turn_res_cnt "
    if addr2:
        s += ("abs_addr_list_2 abs_addr_cnt_2 short_addr_list_2 short_addr_cnt_2 "
              "long_addr_list_2 long_addr_cnt_2 ")
    s += (
        "turn_info_list turn_info_cnt turn_info_time_dep_cnt "
        "turn_info_vehicle_dep_cnt drivable_time_dependent_list drivable_time_dependent_cnt "
        "drivable_info_list drivable_info_cnt drivable_info_time_and_vehicle_dep_cnt "
        "drivable_oot_list drivable_oot_cnt lane_offs_list lane_offs_cnt lane_list lane_cnt "
        "lane_conn_list lane_conn_cnt lane_pair_list lane_pair_cnt lane_arm_list lane_arm_cnt "
        "slope_offs_list slope_offs_cnt slope_list slope_cnt citylistid_list citylistid_cnt "
    )
    if tmcp:
        s += "tmcp_list tmcp_size "
    s += (
        "intersection_view_list intersection_view_count "
        "intersection_view_arm_list intersection_view_arm_cnt intersection_view_image_name_list "
        "intersection_view_image_name_size junction_view_list junction_view_count "
        "junction_view_image_name_list junction_view_image_name_size traffic_sign_list "
        "traffic_sign_cnt traffic_sign_exc_list traffic_sign_exc_cnt overtaking_list overtaking_cnt "
        "traffic_sign_total_text_length"
    )
    return tuple(s.split())


TRAILER_LEN = 76   # adas2_list, adas2_cnt, 23 x 3 adas_shape_counts, flags, 4 totals

# Factory id order of the stats table (adas_shape_counts) in the mp0 parcel trailer;
# id 8 holds (shaped segments, shape points).
STATS_IDS = (31, 15, 29, 13, 30, 14, 28, 12, 27, 11, 25, 9, 26, 10, 24, 8, 19, 3, 17, 1, 18, 2, 16)
STATS_IDS_COMPACT = (47, 43, 45, 41, 46, 42, 44, 24, 8, 16)


class Schema:
    def __init__(self, name, model, road_numbers, pcl_fields, max_pcl_id,
                 stats_ids=STATS_IDS, compact=False):
        self.name = name                    # short id used in reports
        self.model = model                  # datamodel string in au.mod
        self.road_numbers = road_numbers    # "u16pair" (5.2.1) or "flag" (5.3)
        self.pcl_fields = pcl_fields        # named pcl_offs fields before the trailer
        self.stats_ids = tuple(stats_ids)   # trailer stats table ids (mp0)
        self.trailer_len = 2 + 3 * len(stats_ids) + 1 + 4
        self.compact = compact              # MY10 varint parcels (see module doc)
        self.n_fields = len(pcl_fields)
        self.n_offs = self.n_fields + self.trailer_len
        self.offs_len = 2 * self.n_offs     # bytes; first list starts here
        self.list_fields = [i for i, n in enumerate(pcl_fields) if n.endswith("_list")]
        self.idx = {n: i for i, n in enumerate(pcl_fields)}
        self.max_pcl_id = max_pcl_id        # mapHeader carries u32 maxPclId
        rec = "<BIBiiii" + ("HH" if road_numbers == "u16pair" else "B") + "BH"
        self.road_rec = struct.Struct(rec)

    def __repr__(self):
        return f"Schema({self.name})"


V521 = Schema("5.2.1", "Arriba 5.2.1", "u16pair", _fields(truck=False, tmcp=True), False)
V53 = Schema("5.3", "Arriba 5.3", "flag", _fields(truck=True, tmcp=False), True)
ALL = (V521, V53)
# MY15 card (CD_8217, AUNZ 2014Q1): au.mod and auz.rd are 5.2.1, but the map files
# (mp0/mg) already use the 5.3 parcel layout and mapHeader maxPclId, and mp0's
# byte_cnt is the file size. au.mod can't tell this apart, so use_card probes auz.mp0.
V521_MAPS53 = Schema("5.2.1+5.3maps", "Arriba 5.2.1", "u16pair",
                     _fields(truck=True, tmcp=False), True)
# MY10 card (CD_7172, AUNZ 2009Q4): au.mod says 5.2.1, map parcels use the compact
# encoding. (Its au.mod calls the last two traffic-sign lists no_overtaking and
# protected_overtaking; they sit in the same slots, and AU leaves them empty.)
V521_COMPACT = Schema("5.2.1-compact", "Arriba 5.2.1", "u16pair",
                      _fields(truck=False, tmcp=True, addr2=False), False,
                      stats_ids=STATS_IDS_COMPACT, compact=True)
# Parcel layouts, told apart by the size of pcl_offs
PARCEL_LAYOUTS = (V521, V53, V521_COMPACT)

_active = V521


def active() -> Schema:
    return _active


def use(s: Schema) -> Schema:
    global _active
    _active = s
    return s


def detect(au_mod: bytes) -> Schema:
    """Datamodel from au.mod. The string sits after the 'Arriba' entry near 0x60."""
    head = au_mod[:0x100]
    i = head.find(b"Arriba\0")
    if i < 0:
        raise ValueError("au.mod: no Arriba datamodel string")
    j = head.index(b"\0", i + 9)
    ver = head[i + 9:j].split(b" ")[0].decode("latin-1")
    for s in ALL:
        if s.name == ver:
            return s
    raise ValueError(f"unsupported datamodel Arriba {ver}")


def find_au_mod(path):
    """au.mod from a db folder, a db_xxx folder or a navigation folder."""
    for cand in (os.path.join(path, "au.mod"), os.path.join(path, "db", "au.mod"),
                 os.path.join(path, "db_aunz", "db", "au.mod")):
        if os.path.isfile(cand):
            return cand
    return None


def use_card(path) -> Schema:
    """Activate the schema of the card/db folder at path (5.2.1 if au.mod is absent)."""
    p = find_au_mod(path)
    if p is None:
        return use(V521)
    with open(p, "rb") as f:
        s = detect(f.read())
    mp0 = os.path.join(os.path.dirname(p), "auz.mp0")
    if s is V521 and os.path.isfile(mp0):
        layout = map_parcel_layout(mp0)
        if layout is V53:
            s = V521_MAPS53
        elif layout is V521_COMPACT:
            s = V521_COMPACT
    return use(s)


def map_parcel_layout(path) -> Schema:
    """Parcel layout of a map file, from its first parcel (reads only that parcel)."""
    import zlib
    with open(path, "rb") as f:
        head = f.read(0x5E + 0x20)
        pcl_cnt, kd_cnt = struct.unpack_from("<Ii", head, 0x5E + 18)
        if pcl_cnt == 0:
            return None
        f.seek(0x5E + 0x20 + 13 * kd_cnt)
        off, _raw_n, zip_n = struct.unpack("<iHH", f.read(8))
        f.seek(off)
        raw = zlib.decompress(f.read(zip_n))
    return for_parcel(raw)


def for_parcel(raw: bytes) -> Schema:
    """pos_list (first u16) is where the lists start = size of pcl_offs."""
    if len(raw) < 2:
        raise ValueError("parcel too short")
    first = struct.unpack_from("<H", raw, 0)[0]
    for s in PARCEL_LAYOUTS:
        if first == s.offs_len and len(raw) >= first:
            return s
    raise ValueError(f"unknown parcel layout (pcl_offs size {first})")
