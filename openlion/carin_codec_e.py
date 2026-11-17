"""Codec 0x0E on CARiN street tiles (table 0x00): read the packed bit stream.

See docs/33_codec_0x0e.md for the format and what is proven. Status:

  * structure (name records, roads, nodes, shape points): decoded, checked on all 5,950
    factory tiles;
  * name tail (dictionary, string text, name entries, s0/s1/s2 pointer columns, road
    word +24): decoded on all 5,950. The string text uses one fixed prefix code (SHORT,
    then 9-bit groups for dictionary slots, punctuation, digits and letters);
  * to_plain(model, unit_header) lays the model out as the plain tile bytes; on all
    5,950 it lands on the header's section offsets and round-trips through carin_tile.

    from openlion import carin, carin_codec_e
    raw = db[u.off:u.off + u.size]
    model = carin_codec_e.decode_street(raw, u.logical)
    plain = carin_codec_e.to_plain(model, raw[:8])

Everything in the stream is MSB first. Most record fields are "explicit or same as the
previous record": one flag bit, then the value only if the flag is 1.
"""
import struct

HDR = 0x74
MARKER = "0000000000000000" "1" "0000000000001101" "1" "0000000000000000" "1"


class Bits:
    def __init__(self, data: bytes):
        self.b = "".join(f"{x:08b}" for x in data)
        self.p = 0

    def u(self, n: int) -> int:
        if n == 0:
            return 0
        if self.p + n > len(self.b):
            raise ValueError("codec 0x0E stream ran out")
        v = int(self.b[self.p:self.p + n], 2)
        self.p += n
        return v

    def flag(self) -> bool:
        return self.u(1) == 1

    def coord(self, prev: int, w: int) -> int:
        """'0' + w bits: prev + v; '10' + w bits: prev - v; '11' + 16 bits: absolute."""
        if not self.flag():
            return prev + self.u(w)
        if not self.flag():
            return prev - self.u(w)
        return self.u(16)


def decode_street(unit: bytes, logical_pages: int, tail: bool = True) -> dict:
    """unit: the unit's bytes on disc (header included). Returns a dict model."""
    b = unit.rstrip(b"\0")
    secs = [struct.unpack_from(">HH", b, 8 + 4 * i) for i in range(15)]
    n = [c for _o, c in secs]
    p0, p1 = b[HDR], b[HDR + 1]
    q = HDR + 2
    s3 = bytes(b[q:q + 32]); q += 32
    s9 = bytes(b[q:q + 8 * n[9]]); q += 8 * n[9]
    s10 = bytes(b[q:q + 8 * n[10]]); q += 8 * n[10]
    r = Bits(b[q:] + bytes(n[4] // 8 + 8))         # trailing zero flags were trimmed with the padding
    P = (logical_pages * 512 - 1).bit_length()      # width of an in-tile byte offset
    W4, W7 = n[4].bit_length(), max(1, n[7].bit_length())
    W10, Ws = max(1, n[10].bit_length()), (n[2] - 1).bit_length()
    m = dict(secs=secs, p0=p0, p1=p1, P=P, s3=s3, s9=s9, s10=s10, header=bytes(b[:HDR]),
             logical_pages=logical_pages)

    # -- name hierarchy s0, s1, s2: (str2, parent, str); short form repeats str2/parent --
    def name_records(count):
        out, str2, parent = [], 0, 0
        for _ in range(count):
            if r.flag():
                str2, parent = r.u(P), r.u(P - 1) * 2
            out.append(dict(str=r.u(P), str2=str2, parent=parent))
        return out
    m["s0"], m["s1"], m["s2"] = name_records(n[0]), name_records(n[1]), name_records(n[2])

    # -- roads, then the closing record ---------------------------------------------------
    roads, ptrs, cls, sh, srec = [], (0, 0, 0), (0, 0, 0), 0, 0
    for _ in range(n[4]):
        if r.flag():
            ptrs = (r.u(W10), r.u(1), r.u(1))       # indices into s10, s11, s12
        if r.flag():
            cls = (r.u(16), r.u(16), r.u(16))       # class, flags, attributes
        rd = dict(p10=ptrs[0], cls=cls[0], fl=cls[1], attr=cls[2],
                  A=r.u(P - 2) * 4, B=r.u(P - 1) * 4, x=r.u(1))
        if r.flag():
            sh = r.u(W7)
        rd.update(sh=sh, nA=r.u(W4), nB=r.u(W4))    # road indices; n4 = none
        rd["id"] = r.u(32) if r.flag() else r.u(16 + p0)
        if r.flag():
            srec = r.u(Ws)
        rd["srec"] = srec                           # index of the s2 record
        roads.append(rd)
    if r.flag():
        ptrs = (r.u(W10), r.u(1), r.u(1))
    if r.flag():
        sh = r.u(W7)
    m["roads"], m["fence"] = roads, dict(p10=ptrs[0], sh=sh)

    # -- nodes: s5 then s6; each section starts with an absolute 16 + 16 bit position -----
    nodes, fl, x, y = [], 0, 0, 0
    for k in range(n[5] + n[6]):
        if r.flag():
            fl = r.u(11)                            # top 11 bits of the 16-bit flag word
        if k in (0, n[5]):
            x, y = r.u(16), r.u(16)
        else:
            x, y = r.coord(x, p1), r.coord(y, p1)
        nd = dict(x=x, y=y, flags=fl << 5, first_road=r.u(W4))
        if k >= n[5]:
            nd["neighbour"], nd["partner"] = r.u(32), r.u(16)
        nodes.append(nd)
    m["s5"], m["s6"] = nodes[:n[5]], nodes[n[5]:]

    # -- shape points: x, y, 3 flag bits ---------------------------------------------------
    pts = []
    for k in range(n[7]):
        if k == 0:
            x, y = r.u(16), r.u(16)
        else:
            x, y = r.coord(x, p1), r.coord(y, p1)
        pts.append((x, y, r.u(3)))
    m["s7"] = pts
    m["structure_end"] = r.p
    if tail and n[14]:
        try:
            m["tail"] = _tail(r, n, P)
        except ValueError as e:
            m["tail_error"] = str(e)
    return m


# String text: one fixed prefix code for the whole disc (doc 33). Short codes for the 14
# commonest characters, then 9-bit groups: a prefix and a 3/4/5-bit index.
SHORT = {"000": b"a", "001": b"e", "0100": b"s", "0101": b"t", "0110": b"r", "0111": b"\0",
         "10000": b" ", "10001": b"d", "10010": b"g", "10011": b"h", "10100": b"i",
         "10101": b"l", "10110": b"n", "10111": b"o"}
PUNCT = {4: b"/", 6: b"."}                         # 110101 + 3 bits; others never seen


def _text(r: Bits, dictionary, nbytes: int) -> bytes:
    """Decode nbytes of string text. Raises ValueError on a code never seen on the disc."""
    out = bytearray()
    while len(out) < nbytes:
        for code, ch in SHORT.items():
            if r.b.startswith(code, r.p):
                r.p += len(code)
                out += ch
                break
        else:
            if r.b.startswith("110100", r.p):         # dictionary slot 1-6, 7 = apostrophe
                r.p += 6
                k = r.u(3)
                if 1 <= k <= 6:
                    out += dictionary[k - 1]
                elif k == 7:
                    out += b"'"
                else:
                    raise ValueError("string code 110100 000")
            elif r.b.startswith("110101", r.p):       # punctuation
                r.p += 6
                k = r.u(3)
                if k not in PUNCT:
                    raise ValueError(f"string code 110101 {k:03b}")
                out += PUNCT[k]
            elif r.b.startswith("11011", r.p):        # digit
                r.p += 5
                k = r.u(4)
                if k > 9:
                    raise ValueError(f"string code 11011 {k:04b}")
                out += b"%d" % k
            elif r.b.startswith("1111", r.p):         # any letter by number, a = 1
                r.p += 4
                k = r.u(5)
                if not 1 <= k <= 26:
                    raise ValueError(f"string code 1111 {k:05b}")
                out.append(0x60 + k)
            else:
                raise ValueError(f"string code {r.b[r.p:r.p + 9]} not known")
    if len(out) != nbytes:
        raise ValueError("a dictionary entry ran past the end of the string table")
    return bytes(out)


def _tail(r: Bits, n, P) -> dict:
    t = dict(strings_start=r.u(P), strings_end=r.u(P))
    t["dictionary"] = [bytes(r.u(7) for _ in range(r.u(5))) for _ in range(6)]
    # all strings in table order, each with its NUL; strings_end is the last NUL
    t["strings"] = _text(r, t["dictionary"], t["strings_end"] + 1 - t["strings_start"])
    # s0 word 1, explicit-or-previous: on every factory tile 0, 13, 0 (the MARKER bits)
    if not r.b.startswith("1" + MARKER, r.p):
        raise ValueError("no s0 column after the string block")
    t["s0_word1"], v = [], 0
    for _ in range(n[0]):
        if r.flag():
            v = r.u(16)
        t["s0_word1"].append(v)
    r.p += 1                                        # the MARKER's last bit: the next column's flag
    if r.u(n[4] + 1):
        raise ValueError("per-road column is not all 'same'")
    entries, ptr, hi, lo = [], 0, 0, 0
    for _ in range(n[14]):
        if r.flag():
            ptr = r.u(P)
        if r.flag():
            hi = r.u(8)
        if r.flag():
            lo = r.u(5)
        entries.append((ptr, hi << 8 | lo))
    t["s14"] = entries
    s2, d3, d4, v3, v4 = [], 0, 0, 0, 0
    for _ in range(n[2]):                           # deltas; flag 0 repeats the last delta
        if r.flag():
            d3 = r.u(P - 1) * 2
        if r.flag():
            d4 = r.u(P - 1) * 2
        v3, v4 = v3 + d3, v4 + d4
        s2.append((v3, v4))
    t["s2_entries"] = s2
    t["s1_entries"] = [(r.u(P - 1) * 2, r.u(P - 1) * 2) for _ in range(n[1])]
    t["s0_entries"] = [r.u(P - 1) * 2 for _ in range(n[0])]
    # road word +24, per road and the closing record: two 0 bits, then explicit-or-previous
    # 16-bit values (slip-road type 0x100/0x200/0x300 and the rare 0x400 / 0x1000, doc 32)
    if r.u(2):
        raise ValueError("unexpected bits before the +24 column")
    x24, v = [], 0
    for _ in range(n[4] + 1):
        if r.flag():
            v = r.u(16)
        x24.append(v)
    t["x24"] = x24
    if r.b[r.p:].strip("0"):
        raise ValueError("bits left after the +24 column")
    return t


def to_plain(model: dict, unit_header: bytes) -> bytes:
    """Lay a decoded street tile out as the plain tile bytes (what carin.decode() gives
    for a stored or zlib unit): unit header, sections in the factory order, strings.

    Every pointer is rebuilt from the section offsets in the tile's own plain header, and
    carin_tile.layout() must land on exactly those offsets (checked here)."""
    from . import carin_tile as ct
    secs = model["secs"]
    O = [o for o, _c in secs]
    n = [c for _o, c in secs]
    t = model["tail"]
    hdr = model["header"]
    x0, y0, x1, y1 = struct.unpack_from(">iiii", hdr, 0x44)
    tile = ct.Tile(True, (x0, y0, x1, y1), bytes(hdr[0x54:HDR]))

    def node_off(k):
        return O[5] + 8 * k if k < n[5] else O[6] + 16 * (k - n[5])

    def rec(i, s, k):
        r = model[s][k]
        if s == "s0":                                  # country: (str, 13, entry, 0, 0)
            return struct.pack(">5H", r["str"], t["s0_word1"][k], t["s0_entries"][k], 0, 0)
        e3, e4 = t[s + "_entries"][k]
        return struct.pack(">5H", r["str"], r["str2"], r["parent"], e3, e4)
    tile.s0 = [rec(0, "s0", k) for k in range(n[0])]
    tile.s1 = [rec(1, "s1", k) for k in range(n[1])]
    tile.s2 = [rec(2, "s2", k) for k in range(n[2])]
    tile.s3 = model["s3"]

    road_off = lambda k: 0 if k >= n[4] else O[4] + 32 * k      # n4 = none
    roads = bytearray()
    for k, r in enumerate(model["roads"]):
        roads += struct.pack(">HHHHHHIHHHHHHHH",
                             r["A"], r["B"], O[7] + 6 * r["sh"], road_off(r["nA"]), road_off(r["nB"]),
                             r["cls"], r["id"], r["fl"], O[10] + 8 * r["p10"], O[11], O[12],
                             t["x24"][k], O[2] + 10 * r["srec"], r["attr"], O[13])
    f = model["fence"]
    roads += struct.pack(">HHHHHHIHHHHHHHH", 0, 0, O[7] + 6 * f["sh"], 0, 0, 0, 0, 0,
                         O[10] + 8 * f["p10"], O[11], O[12], t["x24"][n[4]], 0, 0, O[13])
    tile.roads, tile.nroads = bytes(roads), n[4]

    tile.s5 = b"".join(struct.pack(">HHHH", d["x"], d["y"], road_off(d["first_road"]), d["flags"])
                       for d in model["s5"])
    tile.s6 = b"".join(struct.pack(">HHHHIHH", d["x"], d["y"], road_off(d["first_road"]), d["flags"],
                                   d["neighbour"], d["partner"], 0) for d in model["s6"])
    tile.s7 = b"".join(struct.pack(">HHH", x, y, z << 8) for x, y, z in model["s7"])
    tile.s9, tile.s10 = model["s9"], model["s10"]
    tile.n5, tile.n6, tile.n7, tile.n8, tile.n9, tile.n10 = n[5], n[6], n[7], 0, n[9], n[10]
    tile.names = list(t["s14"])
    tile.strings = t["strings"]
    offs, _end = ct.layout(tile)
    if offs != O:
        raise ValueError(f"plain layout {offs} differs from the header's {O}")
    out = ct.write(tile, unit_header[:8])
    if len(out) > model["logical_pages"] * 512:
        raise ValueError("plain tile is larger than the unit's stated size")
    return out
