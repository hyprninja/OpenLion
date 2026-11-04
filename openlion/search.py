"""Address-search tables: country/state/county/city, the speller trees and lists.

All are FLAT tables (see flat.py) with the common 0x5E header; layouts from au.mod.

  ctr  country   80 B   u8 countryID, u8 trans, name[35], u8 trans, phonetic[35],
                        iso3[3], iso2[2], u8 speedUnit, u8 drivingSide
  stt  state    152 B   u16 stateID, zoneID, start_countyID, end_countyID, i32 cover[4],
                        u8 trans, name[36], u8 trans, phonetic[36], u16 countryID,
                        u16 alpha_counties[26]
  cny  county   106 B   u16 countyID, stateID, zoneID, u32 start_cityID, end_cityID,
                        i32 cover[4], u8 trans, name[36], u8 trans, phonetic[36], u16 timeinfoID
  cty  city      38 B + nameNum x u32
                        u32 cityID, u16 countyID, i32 cover[4], u16 cataloglistIdFirst,
                        i32 centreLon, centreLat, cityIdMunicipality, u16 nameNum,
                        u32 cityNameOffset[nameNum] (into .str)
                        cityID = byte offset of the record minus 0x5E (what roads reference)
  cal  catalog list 48 B  u16 catalogID, u8 type, u8 trans, name[36], i32 cityTreeOffset,
                        i32 postalcodeTreeOffset (-1 = none)
  cat  catalog   19 B   u16 catalogID, u8 streetFirst, i32 boundingRect[4]
  cl   city list 10 B   u32 cityID, u16 name seqNr, i32 roadTreeOffset (into .rt)
  rl   road list  5 B   u32 roadID (byte offset in .rd minus 0x5E), u8 name seqNr
  ct / rt  speller trees, packed back to back:
        header 12 B: u32 listID_start, u32 listID_end, u32 nodeCount
        node   19 B: char key[5], u32 childIndex, u16 childCount, u32 dataIndex, u32 dataCount
        dataIndex is relative to listID_start. Node 0 is '*', node 1 a structural root
        ('1' or '2'), below that 1-char keys; '' = names ending exactly here (first
        among siblings). Child blocks are laid out depth-first; leaves have childIndex 0.
        The factory trees are sparse: not every prefix has a node; the unit evidently
        scans the (sorted) list range where a node is missing.
  Lists are sorted by the character order in au.abc:
        " &(),-./;ABCDEFGHIJKLMNOPQRSTUVWXYZ[]'0123456789"
"""
import struct

from .common import HDR, build_header

ABC_ORDER = " &(),-./;ABCDEFGHIJKLMNOPQRSTUVWXYZ[]'0123456789"
_RANK = {c: i for i, c in enumerate(ABC_ORDER)}


def collate_key(s: str):
    """Sort key matching the factory list order (unknown chars sort last)."""
    return [_RANK.get(c, 255) for c in s]


# ---- fixed-size record tables ---------------------------------------------

def records(raw: bytes, size: int):
    cnt = struct.unpack_from("<I", raw, 0x56)[0]
    body = raw[HDR:]
    if len(body) != cnt * size:
        raise ValueError("fixed-record table framing")
    return [body[i * size:(i + 1) * size] for i in range(cnt)]


def encode_records(template: bytes, recs) -> bytes:
    body = b"".join(recs)
    return build_header(template, len(recs), HDR + len(body)) + body


RECORD_SIZES = {"ctr": 80, "stt": 152, "cny": 106, "cal": 48, "cat": 19, "cl": 10, "rl": 5,
                "ti": 23, "aff": 44, "typ": 83}


def _name(b):
    return b.split(b"\0")[0].decode("latin-1")


def _pad(s, n):
    b = s.encode("latin-1")[:n]
    return b + b"\0" * (n - len(b))


def parse_county(r):
    a = struct.unpack_from("<3H2I4i", r, 0)
    return {"countyID": a[0], "stateID": a[1], "zoneID": a[2], "start_cityID": a[3],
            "end_cityID": a[4], "cover": a[5:9], "trans1": r[30], "name": _name(r[31:67]),
            "trans2": r[67], "phonetic": _name(r[68:104]),
            "timeinfoID": struct.unpack_from("<H", r, 104)[0]}


def encode_county(c):
    return (struct.pack("<3H2I4i", c["countyID"], c["stateID"], c["zoneID"], c["start_cityID"],
                        c["end_cityID"], *c["cover"])
            + bytes([c["trans1"]]) + _pad(c["name"], 36) + bytes([c["trans2"]])
            + _pad(c["phonetic"], 36) + struct.pack("<H", c["timeinfoID"]))


# ---- cities ------------------------------------------------------------------

CITY = struct.Struct("<IH4iHiiiH")  # 38 bytes


def parse_cities(raw: bytes):
    cnt = struct.unpack_from("<I", raw, 0x56)[0]
    out, off = [], HDR
    for _ in range(cnt):
        v = CITY.unpack_from(raw, off)
        nn = v[-1]
        names = list(struct.unpack_from(f"<{nn}I", raw, off + CITY.size))
        out.append({"cityID": v[0], "countyID": v[1], "cover": v[2:6], "cataloglistIdFirst": v[6],
                    "centre": (v[7], v[8]), "cityIdMunicipality": v[9], "name_offsets": names,
                    "_offset": off - HDR})
        off += CITY.size + 4 * nn
    if off != len(raw):
        raise ValueError("city table framing")
    return out


def encode_cities(template: bytes, cities):
    """cityID of each record must equal its byte offset - 0x5E; pass cities with
    cityID=None to have it assigned. Returns (table bytes, [cityID per city])."""
    body, ids, pos = bytearray(), [], 0
    for c in cities:
        cid = pos if c.get("cityID") is None else c["cityID"]
        ids.append(cid)
        rec = CITY.pack(cid, c["countyID"], *c["cover"], c["cataloglistIdFirst"], *c["centre"],
                        c["cityIdMunicipality"], len(c["name_offsets"]))
        rec += struct.pack(f"<{len(c['name_offsets'])}I", *c["name_offsets"])
        body += rec
        pos += len(rec)
    return build_header(template, len(cities), HDR + len(body)) + bytes(body), ids


# ---- speller trees -----------------------------------------------------------

TREE_HDR = struct.Struct("<III")
NODE_TAIL = struct.Struct("<IHII")


def parse_trees(raw: bytes):
    """-> {offset: {"listID_start", "listID_end", "nodes": [(key, ci, cc, di, dc)]}} in file order."""
    out, off = {}, HDR
    while off < len(raw):
        ls, le, n = TREE_HDR.unpack_from(raw, off)
        nodes = []
        for i in range(n):
            q = off + 12 + 19 * i
            nodes.append((raw[q:q + 5],) + NODE_TAIL.unpack_from(raw, q + 5))
        out[off - HDR] = {"listID_start": ls, "listID_end": le, "nodes": nodes}
        off += 12 + 19 * n
    return out


def encode_tree(t) -> bytes:
    out = bytearray(TREE_HDR.pack(t["listID_start"], t["listID_end"], len(t["nodes"])))
    for key, ci, cc, di, dc in t["nodes"]:
        k = key if isinstance(key, bytes) else key.encode("latin-1")
        out += k[:5] + b"\0" * (5 - len(k[:5])) + NODE_TAIL.pack(ci, cc, di, dc)
    return bytes(out)


def encode_trees(template: bytes, trees):
    """trees: list in layout order. Returns (table bytes, [offset of each tree])."""
    body, offs = bytearray(), []
    for t in trees:
        offs.append(len(body))
        body += encode_tree(t)
    return build_header(template, 0, HDR + len(body)) + bytes(body), offs


def build_tree(names, list_start, root_key=b"1", leaf_max=1, max_depth=40):
    """Build a speller tree over `names` (already sorted with collate_key).

    Full-trie strategy: every prefix with more than `leaf_max` entries gets
    children for each next character ('' first for names ending exactly there).
    The factory trees are sparser; this is a superset of their structure."""
    n = len(names)
    nodes = [[b"*", 1, 1, 0, n], [root_key, 0, 0, 0, n]]

    def children(lo, hi, depth):
        groups, i = [], lo
        while i < hi:
            c = names[i][depth] if len(names[i]) > depth else ""
            j = i
            while j < hi and ((names[j][depth] if len(names[j]) > depth else "") == c):
                j += 1
            groups.append((c, i, j))
            i = j
        return groups

    def expand(idx, lo, hi, depth):
        if hi - lo <= leaf_max or depth >= max_depth:
            return
        groups = children(lo, hi, depth)
        if len(groups) == 1 and groups[0][0] == "":
            return
        first = len(nodes)
        nodes[idx][1], nodes[idx][2] = first, len(groups)
        for c, a, b in groups:
            nodes.append([c.encode("latin-1"), 0, 0, a, b - a])
        for k, (c, a, b) in enumerate(groups):
            if c:
                expand(first + k, a, b, depth + 1)

    expand(1, 0, n, 0)
    return {"listID_start": list_start, "listID_end": list_start + n - 1,
            "nodes": [tuple(x) for x in nodes]}


def check_tree(t, names):
    """True if every node's data range holds exactly names starting with its prefix."""
    nodes = t["nodes"]
    stack = [(0, "")]
    while stack:
        i, prefix = stack.pop()
        key, ci, cc, di, dc = nodes[i]
        k = key.rstrip(b"\0").decode("latin-1") if isinstance(key, bytes) else key
        p = prefix if i < 2 else prefix + k
        if not all(x.startswith(p) for x in names[di:di + dc]):
            return False
        stack.extend((c, p) for c in range(ci, ci + cc))
    return True
