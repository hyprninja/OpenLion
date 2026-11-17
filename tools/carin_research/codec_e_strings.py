"""Starting point for the open part of codec 0x0E: the packed string text (doc 33).
    python codec_e_strings.py FACTORY.iso [TILE_OFFSET_HEX ...]

For each tile (default: the hand-worked 0x26D7800) prints what is known about its string
block: table start/end, the fragment dictionary, the length of the coded text in bits, and
the byte offset of every string (from the name records and name entries), so bits per
character can be judged, and the decoded text (carin_codec_e._text, doc 33).

tokenize() and solve() are the pieces used so far. solve() is a plain backtracking search
for a prefix code that maps a known token sequence onto a bit string. They were used to
work out the code, which is now in carin_codec_e."""
import sys

from common import Disc, carin
from openlion import carin_codec_e as ce

sys.setrecursionlimit(10000)


def tokenize(text: str, dictionary) -> list:
    """Greedy split into dictionary fragments (longest first) and single characters.
    Checked only on 'australia' = aus t r ali a; greedy longest-first is a guess."""
    ents = sorted((e for e in dictionary if e != "A"), key=len, reverse=True)
    out, i = [], 0
    while i < len(text):
        for e in ents:
            if text.startswith(e, i):
                out.append(e); i += len(e)
                break
        else:
            out.append(text[i]); i += 1
    return out


def solve(bits: str, tokens, maxlen=14, minlen=1, limit=5) -> list:
    """All prefix-free code assignments (up to limit) that spell tokens at the start of bits."""
    sols = []

    def rec(i, pos, codes):
        if len(sols) >= limit:
            return
        if i == len(tokens):
            sols.append((pos, dict(codes)))
            return
        t = tokens[i]
        if t in codes:
            if bits.startswith(codes[t], pos):
                rec(i + 1, pos + len(codes[t]), codes)
            return
        for n in range(minlen, maxlen + 1):
            c = bits[pos:pos + n]
            if len(c) < n:
                break
            if not any(o.startswith(c) or c.startswith(o) for o in codes.values()):
                codes[t] = c
                rec(i + 1, pos + n, codes)
                del codes[t]
    rec(0, 0, {})
    return sols


def describe(d: Disc, off: int) -> None:
    u = d.by[off]
    m = ce.decode_street(d.db[u.off:u.off + u.size], u.logical)
    t = m.get("tail")
    if not t:
        print(f"{off:#x}: tail not parsed ({m.get('tail_error', 'no name entries')})")
        return
    starts = {r[k] for s in ("s0", "s1", "s2") for r in m[s] for k in ("str", "str2")}
    starts |= {p for p, _f in t["s14"]}
    starts = sorted(p for p in starts if t["strings_start"] <= p <= t["strings_end"])
    nbytes = t["strings_end"] + 1 - t["strings_start"]
    words = [e.decode("latin-1") for e in t["dictionary"]]
    print(f"{off:#x}: strings {t['strings_start']}..{t['strings_end']} ({nbytes} bytes, "
          f"{len(starts)} strings)")
    print("   dictionary:", words)
    print("   string offsets:", [p - t["strings_start"] for p in starts])
    print("   strings:", [x.decode("latin-1") for x in t["strings"].split(b"\0")[:-1]])


if __name__ == "__main__":
    disc = Disc(sys.argv[1])
    for a in sys.argv[2:] or ["26d7800"]:
        describe(disc, int(a, 16))
