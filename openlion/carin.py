"""Read (and patch in place) the VY/VZ Commodore map DVD: a Siemens VDO CARiN database.

See docs/29_vz_map_disc.md. Everything here is big-endian.

The disc is plain ISO 9660 holding one big file, `carindb`, plus a few small ones that
belong to the C-IQ licensing layer (DAYTON, TPD/...) and are copied untouched.

`carindb` is a chain of *units*. Each one is a whole number of 512-byte pages and starts
with an 8-byte header:

  u32 addr    (owner offset >> 8) << 8 | page count
              owner offset == the unit's own offset, except table 5 (extension pages),
              whose owner is the unit it extends
  u16 table   table type 0x00..0x28
  u16 codec   0x00nn stored; 0x0Fnn zlib, nn = logical size in pages (header included);
              0x0Enn a second, not yet decoded codec, nn = logical size in pages

The same `addr` encoding (offset>>8 | pages) is how tables point at each other, so units
can only be patched in place for now: a unit may change its contents but not its size.

Coordinates: signed int32, 1 unit = 1.8e-7 degree (1e9 units = 180 degrees).
  lat = y * 1.8e-7;  lon = x * 1.8e-7 - 30

Usage:
  python -m openlion.carin info NAV_DB.iso
  python -m openlion.carin roundtrip NAV_DB.iso
  python -m openlion.carin labels NAV_DB.iso "muirs highway"
  python -m openlion.carin testdisc NAV_DB.iso OUT.iso "muirs highway" "openlion test"
"""
import struct
import sys
import zlib
from dataclasses import dataclass

try:
    from .common import DamagedTable
except ImportError:  # run as a script
    from common import DamagedTable

PAGE = 512
SECTOR = 2048
DB_NAME = b"carindb"

STORED, CODEC_E, ZLIB = 0x00, 0x0E, 0x0F
EXTENSION_TABLE = 0x05
# C-IQ product/activation tables at the end of carindb. Never modified.
LICENCE_TABLES = frozenset(range(0x1F, 0x29))

TABLE_NAMES = {
    0x00: "map tiles, detail level (geometry + labels)",
    0x01: "map tiles, overview level",
    0x02: "map tiles, overview level",
    0x03: "map tiles, overview level",
    0x04: "road network (routing)",
    0x05: "extension pages of another unit",
    0x06: "per-tile network data",
    0x07: "database coverage / root",
    0x08: "unit address index",
    0x09: "small per-tile records",
    0x0A: "database directory",
    0x0B: "database directory",
    0x0C: "place names (search)",
    0x0D: "table directory",
    0x0E: "street names, 'street, suburb' (search)",
    0x0F: "search index",
    0x10: "points of interest",
    0x11: "POI index",
    0x12: "volume header / record size list",
    0x13: "database metadata",
    0x14: "background areas (water, land)",
    0x15: "background areas",
    0x16: "background areas",
    0x1C: "background areas",
    0x1D: "background areas, coarse",
    0x1E: "background areas, coarsest",
}


@dataclass(frozen=True)
class Unit:
    off: int        # byte offset in carindb
    pages: int      # pages on disc (header's page count, plus any headerless pages after it)
    owner: int      # byte offset of the unit this one belongs to (== off unless an extension)
    table: int
    codec: int      # STORED / ZLIB / CODEC_E
    logical: int    # logical size in pages for ZLIB / CODEC_E, 0 for STORED

    @property
    def size(self) -> int:
        return self.pages * PAGE


def _header(db: bytes, off: int):
    a, table, c = struct.unpack_from(">IHH", db, off)
    return (a >> 8) << 8, a & 0xFF, table, c


def _valid_at(db: bytes, off: int) -> bool:
    owner, pages, _t, _c = _header(db, off)
    return pages > 0 and owner <= off


def units(db: bytes) -> list:
    """Walk carindb from start to end. Raises DamagedTable if the chain doesn't add up."""
    if len(db) % PAGE or len(db) < PAGE:
        raise DamagedTable(f"carindb size {len(db)} is not a whole number of pages")
    out, off = [], 0
    while off < len(db):
        owner, pages, table, c = _header(db, off)
        if not pages or owner > off:
            raise DamagedTable(f"no unit header at {off:#x}")
        end = off + pages * PAGE
        if end > len(db):
            raise DamagedTable(f"unit at {off:#x} runs past the end of the file")
        # The volume header unit at 0 runs on into a page with no header of its own.
        while end < len(db) and not _valid_at(db, end):
            end += PAGE
        codec, logical = c >> 8, c & 0xFF
        if codec not in (STORED, ZLIB, CODEC_E):
            raise DamagedTable(f"unit at {off:#x}: unknown codec {c:#06x}")
        out.append(Unit(off, (end - off) // PAGE, owner, table, codec, logical))
        off = end
    if off != len(db):
        raise DamagedTable(f"unit chain ends at {off:#x}, file is {len(db):#x}")
    return out


class UnsupportedCodec(ValueError):
    pass


def decode(db: bytes, u: Unit) -> bytes:
    """Logical bytes of a unit: the 8-byte header followed by the (decompressed) payload.
    Offsets stored inside a unit are relative to its first header byte."""
    raw = db[u.off:u.off + u.size]
    if u.codec == STORED:
        return raw
    if u.codec == ZLIB:
        z = zlib.decompressobj()
        try:
            body = z.decompress(raw[8:])
        except zlib.error as e:
            raise DamagedTable(f"unit at {u.off:#x}: {e}") from None
        if len(body) != u.logical * PAGE - 8 or z.unused_data.strip(b"\0"):
            raise DamagedTable(f"unit at {u.off:#x}: zlib size {len(body)} != {u.logical} pages")
        return raw[:8] + body
    raise UnsupportedCodec(f"unit at {u.off:#x} uses codec 0x0E (not decoded yet)")


def encode_in_place(u: Unit, logical: bytes, level: int = 9) -> bytes:
    """Encode logical unit bytes for the unit's existing slot (same offset, same page count).
    zlib level 9 reproduces the factory units byte for byte."""
    if len(logical) % PAGE or not 0 < len(logical) // PAGE <= 0xFF:
        raise ValueError("logical unit must be 1..255 whole pages")
    addr, table = struct.unpack_from(">IH", logical)  # slot address is unchanged
    if (addr >> 8) << 8 != u.owner or table != u.table:
        raise ValueError(f"unit at {u.off:#x}: header does not belong to this slot")
    if u.codec == STORED:
        if len(logical) != u.size:
            raise ValueError("stored unit cannot change size")
        out = logical
    elif u.codec == ZLIB:
        c = (ZLIB << 8) | (len(logical) // PAGE)
        out = struct.pack(">IHH", addr, table, c) + zlib.compress(logical[8:], level)
        if len(out) > u.size:
            raise ValueError(f"unit at {u.off:#x}: {len(out)} bytes no longer fit in {u.pages} pages")
        out = out.ljust(u.size, b"\0")
    else:
        raise UnsupportedCodec("codec 0x0E units cannot be re-encoded yet")
    return out


def to_lonlat(x: int, y: int):
    return x * 1.8e-7 - 30.0, y * 1.8e-7


def from_lonlat(lon: float, lat: float):
    return round((lon + 30.0) / 1.8e-7), round(lat / 1.8e-7)


# ---------------------------------------------------------------- ISO 9660

@dataclass(frozen=True)
class IsoFile:
    path: str
    lba: int
    size: int
    record: int  # byte offset of the directory record in the image


def iso_files(img: bytes) -> list:
    """Files on the disc. The factory mastering tool writes names without ';1'."""
    if img[16 * SECTOR + 1:16 * SECTOR + 6] != b"CD001":
        raise DamagedTable("not an ISO 9660 image")
    root = 16 * SECTOR + 156
    out = []

    def walk(lba, size, path, depth=0):
        if depth > 8:
            raise DamagedTable("directory tree too deep")
        pos, end = lba * SECTOR, lba * SECTOR + size
        while pos < end:
            n = img[pos]
            if n == 0:
                pos = (pos // SECTOR + 1) * SECTOR
                continue
            ext, sz = struct.unpack_from("<I", img, pos + 2)[0], struct.unpack_from("<I", img, pos + 10)[0]
            flags, nl = img[pos + 25], img[pos + 32]
            name = img[pos + 33:pos + 33 + nl]
            if name not in (b"\0", b"\1") and ext != lba:
                p = path + "/" + name.decode("latin-1").split(";")[0]
                if flags & 2:
                    walk(ext, sz, p, depth + 1)
                else:
                    out.append(IsoFile(p, ext, sz, pos))
            pos += n

    walk(struct.unpack_from("<I", img, root + 2)[0], struct.unpack_from("<I", img, root + 10)[0], "")
    return out


def read_db(img: bytes):
    f = next((f for f in iso_files(img) if f.path == "/" + DB_NAME.decode()), None)
    if f is None:
        raise DamagedTable("no carindb on this disc")
    return f, img[f.lba * SECTOR:f.lba * SECTOR + f.size]


def replace_db(img: bytes, db: bytes) -> bytes:
    """New image with carindb swapped for a same-size copy. Everything else, including the
    licensing files, stays byte for byte."""
    f, old = read_db(img)
    if len(db) != len(old):
        raise ValueError("carindb must keep its size (relocation not written yet)")
    start = f.lba * SECTOR
    return img[:start] + db + img[start + len(db):]


# ---------------------------------------------------------------- label edits (test disc)

MAP_TILE_TABLES = frozenset({0x00, 0x01, 0x02, 0x03})


def find_label(db: bytes, text: bytes, tables=MAP_TILE_TABLES):
    """Units (that can be re-encoded) holding text as a whole NUL-terminated string.
    Defaults to the map tiles: the search tables are sorted, so renaming there would
    break their order."""
    hits = []
    needle = b"\0" + text + b"\0"
    for u in units(db):
        if u.codec == CODEC_E or u.table in LICENCE_TABLES or u.table not in tables:
            continue
        body = decode(db, u)
        if needle in body:
            hits.append((u, body.count(needle)))
    return hits


def rename_label(db: bytes, old: bytes, new: bytes):
    """Replace a whole string with another of the same length in every map tile that can be
    re-encoded. Returns (new_db, units_changed). Used to make an in-car test disc."""
    if len(old) != len(new):
        raise ValueError("replacement must be the same length")
    out = bytearray(db)
    changed = 0
    for u, _n in find_label(db, old):
        body = decode(db, u).replace(b"\0" + old + b"\0", b"\0" + new + b"\0")
        out[u.off:u.off + u.size] = encode_in_place(u, body)
        changed += 1
    return bytes(out), changed


# ---------------------------------------------------------------- CLI

def _summary(db: bytes):
    from collections import defaultdict
    s = defaultdict(lambda: [0, 0, 0, 0, 0])
    for u in units(db):
        r = s[u.table]
        r[0] += 1
        r[1] += u.size
        r[2 + (0 if u.codec == STORED else 1 if u.codec == ZLIB else 2)] += 1
    return s


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd, img = argv[0], open(argv[1], "rb").read()
    f, db = read_db(img)
    if cmd == "info":
        for x in iso_files(img):
            print(f"{x.lba:8d} {x.size:10d}  {x.path}")
        us = units(db)
        print(f"\ncarindb: {len(db):,} bytes, {len(us):,} units")
        print("table  units       bytes  stored   zlib  0x0E  role")
        for t, (n, b, st, zl, ce) in sorted(_summary(db).items()):
            role = "C-IQ licensing (left alone)" if t in LICENCE_TABLES else TABLE_NAMES.get(t, "?")
            print(f" 0x{t:02X} {n:6d} {b:11,d} {st:6d} {zl:6d} {ce:5d}  {role}")
    elif cmd == "roundtrip":
        out = bytearray(db)
        n = 0
        for u in units(db):
            if u.codec == ZLIB:
                out[u.off:u.off + u.size] = encode_in_place(u, decode(db, u))
                n += 1
        same = bytes(out) == db
        print(f"re-encoded {n} zlib units: {'identical' if same else 'DIFFERENT'}")
        return 0 if same else 1
    elif cmd == "labels":
        for u, n in find_label(db, argv[2].encode(), tables=set(range(0x1F)) - {0x05}):
            print(f"table 0x{u.table:02X} unit {u.off:#x} x{n}")
    elif cmd == "testdisc":
        new_db, n = rename_label(db, argv[3].encode(), argv[4].encode())
        if not n:
            print("label not found in any editable unit")
            return 1
        open(argv[2], "wb").write(replace_db(img, new_db))
        print(f"renamed in {n} units, wrote {argv[2]}")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
