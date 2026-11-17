"""Codec 0x0E on CARiN street tiles (table 0x00): read the packed bit stream.

See docs/33_codec_0x0e.md for the format and what is proven. Status:

  * structure (name records, roads, nodes, shape points): decoded, checked on 5,949 of
    the 5,950 factory tiles (the odd one has p1 = 0);
  * name tail (dictionary, name entries, s0/s1/s2 pointer columns): decoded on 5,105;
  * string text: NOT decoded. The tile carries a 6-entry fragment dictionary followed by
    a prefix-coded token stream whose code table is not recovered yet, so this module
    returns a model, not yet the byte-exact plain tile.

    from openlion import carin, carin_codec_e
    model = carin_codec_e.decode_street(db[u.off:u.off + u.size], u.logical)

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
    r = Bits(b[q:])
    P = (logical_pages * 512 - 1).bit_length()      # width of an in-tile byte offset
    W4, W7 = n[4].bit_length(), max(1, n[7].bit_length())
    W10, Ws = max(1, n[10].bit_length()), (n[2] - 1).bit_length()
    m = dict(secs=secs, p0=p0, p1=p1, P=P, s3=s3, s9=s9, s10=s10, header=bytes(b[:HDR]))

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


def _tail(r: Bits, n, P) -> dict:
    t = dict(strings_start=r.u(P), strings_end=r.u(P))
    t["dictionary"] = [bytes(r.u(7) for _ in range(r.u(5))) for _ in range(6)]
    i = r.b.find(MARKER, r.p)
    if i < 0:
        raise ValueError("no s0 column after the string block")
    t["coded_text"] = r.b[r.p:i]                    # prefix-coded tokens, table unknown
    r.p = i + len(MARKER)
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
    t["final"] = r.b[r.p:].rstrip("0")
    return t
