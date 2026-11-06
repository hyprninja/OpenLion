"""The phrase table in a voice grammar file (navigation/voices/<lang>/Language_*.bgl).

The unit builds each spoken sentence as text from templates in this file, then plays
it as a row of WAV clips, looking the text up in the phrase table. The table is sorted
longest phrase first and the unit appears to use the longest phrase it has a clip for,
so adding longer phrases ("Turn left", "300 meters ahead.") means fewer joins. See
docs/26_voice_packs.md.

Layout (little-endian, text UTF-16LE):
  header: u32 257, u32 3, u32 0, u32 text_total, ...
      text_total = total characters of all text in the file; adding a phrase adds
      len(text) + len(clip name).
  ... grammar ...
  phrase table: u16 count, u16 count, then count entries, longest text first:
      u8 1, u16 n, text, u16 n, clip name (WAV stem), u32 weight
      weight = 700 for speech; for the pause clips it is the pause in ms.
  ... more grammar ..., "EOF".
"""
import struct

SPEECH_WEIGHT = 700
TEXT_TOTAL_AT = 12


def _entries(d, at, count):
    """Parse count entries at d[at:]; None unless they look like a phrase table."""
    j, out, prev = at, [], None
    for _ in range(count):
        if j + 3 > len(d) or d[j] != 1:
            return None
        n = struct.unpack_from("<H", d, j + 1)[0]
        if not 0 < n < 400 or j + 3 + 2 * n + 2 > len(d):
            return None
        text = d[j + 3:j + 3 + 2 * n].decode("utf-16le", "replace")
        j += 3 + 2 * n
        m = struct.unpack_from("<H", d, j)[0]
        if not 0 < m <= 8 or j + 2 + 2 * m + 4 > len(d):
            return None
        stem = d[j + 2:j + 2 + 2 * m].decode("utf-16le", "replace")
        if not stem.isascii() or not stem.replace("_", "").isalnum():
            return None
        j += 2 + 2 * m
        w = struct.unpack_from("<I", d, j)[0]
        j += 4
        if prev is not None and len(text) > prev:
            return None                       # must be longest first
        prev = len(text)
        out.append((text, stem, w))
    return out, j


def read_table(d):
    """(count offset, [(text, clip name, weight)], end offset) of the phrase table."""
    best = None
    i = d.find(b"\x01", 16)
    while 0 <= i:
        if i >= 4:
            c1, c2 = struct.unpack_from("<HH", d, i - 4)
            if c1 == c2 and c1 > 0 and (best is None or c1 > len(best[1])):
                r = _entries(d, i, c1)
                if r:
                    best = (i - 4, r[0], r[1])
        i = d.find(b"\x01", i + 1)
    if best is None:
        raise ValueError("no phrase table found in the voice grammar file")
    return best


def _entry(text, stem, weight):
    return (b"\x01" + struct.pack("<H", len(text)) + text.encode("utf-16le") +
            struct.pack("<H", len(stem)) + stem.encode("utf-16le") + struct.pack("<I", weight))


def build(d, add=()):
    """The grammar file with extra (text, clip name[, weight]) phrases merged into its table.

    Phrases the table already has are left as they are. Existing entries keep their
    order; new ones go after the existing phrases of the same length.
    """
    at, entries, end = read_table(d)
    have = {t for t, _, _ in entries}
    new, seen = [], set()
    for e in add:
        text, stem = e[0], e[1]
        weight = e[2] if len(e) > 2 else SPEECH_WEIGHT
        if text in have or text in seen:
            continue
        if not (0 < len(stem) <= 8 and stem.isascii() and stem.replace("_", "").isalnum()):
            raise ValueError(f"clip name must be 1-8 letters, digits or _: {stem!r}")
        seen.add(text)
        new.append((text, stem, weight))
    merged = entries + new
    order = sorted(range(len(merged)), key=lambda k: (-len(merged[k][0]), k))
    merged = [merged[k] for k in order]
    if len(merged) > 0xFFFF:
        raise ValueError("too many phrases")
    body = struct.pack("<HH", len(merged), len(merged)) + b"".join(_entry(*e) for e in merged)
    out = bytearray(d[:at] + body + d[end:])
    total = struct.unpack_from("<I", d, TEXT_TOTAL_AT)[0] + sum(len(t) + len(s) for t, s, _ in new)
    struct.pack_into("<I", out, TEXT_TOTAL_AT, total)
    return bytes(out)
