"""Swap carindb on a VY/VZ map disc image for one of any size.

Everything else is kept as the factory mastered it (two copies of the volume descriptor,
names without ';1', record padding). Files and directories after carindb move by the
size difference; their directory records, both path tables and the volume size in both
descriptors are updated to match. The licensing files (DAYTON, TPD/...) keep their bytes.
"""
import struct

from . import carin

S = carin.SECTOR


def _both32(buf, pos, v):
    struct.pack_into("<I", buf, pos, v)
    struct.pack_into(">I", buf, pos + 4, v)


def _dir_records(img, lba, size, seen=None):
    """Every directory record (absolute byte position) under a directory, recursively."""
    seen = seen if seen is not None else set()
    if lba in seen:
        return []
    seen.add(lba)
    out = []
    pos, end = lba * S, lba * S + size
    while pos < end:
        n = img[pos]
        if n == 0:
            pos = (pos // S + 1) * S
            continue
        out.append(pos)
        ext, sz = struct.unpack_from("<I", img, pos + 2)[0], struct.unpack_from("<I", img, pos + 10)[0]
        name = img[pos + 33:pos + 33 + img[pos + 32]]
        if img[pos + 25] & 2 and name not in (b"\0", b"\1"):
            out += _dir_records(img, ext, sz, seen)
        pos += n
    return out


def replace_db(img: bytes, db: bytes) -> bytes:
    f, old = carin.read_db(img)
    if len(db) == len(old):
        return carin.replace_db(img, db)
    old_secs = -(-len(old) // S)
    new_secs = -(-len(db) // S)
    delta = new_secs - old_secs
    tail_from = f.lba + old_secs                    # first sector after carindb
    out = bytearray(img[:f.lba * S])
    out += db.ljust(new_secs * S, b"\0")
    out += img[tail_from * S:]
    pvd = 16 * S
    root = pvd + 156
    root_lba, root_size = struct.unpack_from("<I", img, root + 2)[0], struct.unpack_from("<I", img, root + 10)[0]
    if root_lba >= tail_from:
        raise ValueError("root directory after carindb: unexpected layout")
    # directory records (positions are in the unchanged head or the moved tail)
    for pos in _dir_records(img, root_lba, root_size):
        ext = struct.unpack_from("<I", img, pos + 2)[0]
        npos = pos if pos < f.lba * S else pos + delta * S
        if ext == f.lba and struct.unpack_from("<I", img, pos + 10)[0] == len(old):
            _both32(out, npos + 10, len(db))
        elif ext >= tail_from:
            _both32(out, npos + 2, ext + delta)
    # path tables (L little-endian, M big-endian), both descriptor copies
    for d in (16, 17):
        p = d * S
        if img[p + 1:p + 6] != b"CD001" or img[p] != 1:
            continue
        pt_size = struct.unpack_from("<I", img, p + 132)[0]
        for loc_off, fmt in ((140, "<I"), (148, ">I")):
            t = struct.unpack_from(fmt, img, p + loc_off)[0] * S
            q = t
            while q < t + pt_size:
                ln = img[q]
                ext = struct.unpack(fmt, img[q + 2:q + 6])[0]
                if ext >= tail_from:
                    struct.pack_into(fmt, out, q + 2, ext + delta)
                q += 8 + ln + (ln & 1)
        vs = struct.unpack_from("<I", img, p + 80)[0]
        _both32(out, p + 80, vs + delta)
    return bytes(out)
