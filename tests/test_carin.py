"""VY/VZ map disc (CARiN carindb): unit chain, codecs, in-place patching and the ISO
wrapper. Synthetic data only.

  python -m unittest discover -s tests -p "test_*.py"
"""
import os
import struct
import sys
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import carin  # noqa: E402
from openlion.common import DamagedTable  # noqa: E402

P = carin.PAGE


def unit(off, table, payload, codec=carin.STORED, owner=None):
    """One unit at `off`. Payload excludes the 8-byte header."""
    addr = ((owner if owner is not None else off) >> 8) << 8
    if codec == carin.ZLIB:
        logical = -(-(8 + len(payload)) // P)
        payload = payload.ljust(logical * P - 8, b"\0")
        body = zlib.compress(payload, 9)
        pages = -(-(8 + len(body)) // P)
        return struct.pack(">IHH", addr | pages, table, 0x0F00 | logical) + body.ljust(pages * P - 8, b"\0")
    pages = -(-(8 + len(payload)) // P)
    return struct.pack(">IHH", addr | pages, table, 0) + payload.ljust(pages * P - 8, b"\0")


def sample_db():
    db = bytearray()
    db += unit(0, 0x12, b"volume header")
    db += bytes(P)                      # headerless page that belongs to the volume header
    db += unit(len(db), 0x00, b"\0tile\0muirs highway\0western australia\0")
    db += unit(len(db), 0x0E, b"\0muirs highway, kendenup\0" * 40, codec=carin.ZLIB)
    db += unit(len(db), 0x05, b"extension", owner=len(db) - P)
    db += unit(len(db), 0x01, (b"\0muirs highway\0" + bytes(range(256))) * 8, codec=carin.ZLIB)
    db += unit(len(db), 0x21, b"product activated")
    return bytes(db)


def iso_with(files):
    """Minimal ISO 9660 image in the factory layout: PVD at 16, root dir at 21, no ';1'."""
    lba = 22
    recs, data = b"", b""
    for name, blob in files:
        r = bytearray(34 + len(name) + (1 - len(name) % 2))
        r[0] = len(r)
        struct.pack_into("<I", r, 2, lba)
        struct.pack_into("<I", r, 10, len(blob))
        r[32] = len(name)
        r[33:33 + len(name)] = name
        recs += bytes(r)
        data += blob.ljust(-(-len(blob) // 2048) * 2048, b"\0")
        lba += -(-len(blob) // 2048)
    dot = bytearray(34)
    dot[0] = 34
    struct.pack_into("<I", dot, 2, 21)
    struct.pack_into("<I", dot, 10, 2048)
    dot[25] = 2
    dot[32] = 1
    dotdot = bytearray(dot)
    dotdot[33] = 1
    root = (bytes(dot) + bytes(dotdot) + recs).ljust(2048, b"\0")
    pvd = bytearray(2048)
    pvd[0:6] = b"\x01CD001"
    pvd[156:190] = dot
    img = bytes(16 * 2048) + bytes(pvd) + bytes(4 * 2048) + root + data
    return img


class Chain(unittest.TestCase):
    def test_walk(self):
        db = sample_db()
        us = carin.units(db)
        self.assertEqual([u.table for u in us], [0x12, 0x00, 0x0E, 0x05, 0x01, 0x21])
        self.assertEqual(us[0].pages, 2)  # headerless page folded into the volume header
        self.assertEqual(us[3].owner, us[2].off + (us[2].pages - 1) * P)
        self.assertEqual(sum(u.size for u in us), len(db))

    def test_truncated(self):
        db = sample_db()
        overlong = bytearray(db)
        overlong[carin.units(db)[-1].off + 3] = 3  # last unit claims 3 pages, file has 1
        with self.assertRaises(DamagedTable):
            carin.units(bytes(overlong))
        with self.assertRaises(DamagedTable):
            carin.units(db[:100])

    def test_decode_and_reencode_identical(self):
        db = sample_db()
        for u in carin.units(db):
            logical = carin.decode(db, u)
            if u.codec == carin.ZLIB:
                self.assertEqual(len(logical), u.logical * P)
            self.assertEqual(carin.encode_in_place(u, logical), db[u.off:u.off + u.size])

    def test_unknown_codec_refused(self):
        db = bytearray(sample_db())
        u = carin.units(bytes(db))[1]
        struct.pack_into(">H", db, u.off + 6, 0x0E03)
        u = carin.units(bytes(db))[1]
        with self.assertRaises(carin.UnsupportedCodec):
            carin.decode(bytes(db), u)

    def test_grown_unit_rejected(self):
        db = sample_db()
        u = carin.units(db)[2]
        noisy = carin.decode(db, u)[:8] + os.urandom(u.logical * P - 8)
        with self.assertRaises(ValueError):
            carin.encode_in_place(u, noisy)


class Labels(unittest.TestCase):
    def test_rename_only_touches_map_tiles(self):
        db = sample_db()
        new, n = carin.rename_label(db, b"muirs highway", b"openlion test")
        self.assertEqual(n, 2)
        self.assertEqual(len(new), len(db))
        us = carin.units(new)
        by_table = {u.table: carin.decode(new, u) for u in us}
        self.assertIn(b"\0openlion test\0", by_table[0x00])
        self.assertIn(b"\0openlion test\0", by_table[0x01])
        self.assertIn(b"muirs highway, kendenup", by_table[0x0E])  # sorted search table untouched
        self.assertEqual(by_table[0x21], carin.decode(db, us[-1]))
        with self.assertRaises(ValueError):
            carin.rename_label(db, b"muirs highway", b"short")


class Iso(unittest.TestCase):
    def test_replace_db(self):
        db = sample_db()
        img = iso_with([(b"DAYTON", b"\x00\x2a" + bytes(40)), (b"carindb", db)])
        f, got = carin.read_db(img)
        self.assertEqual(got, db)
        new, _ = carin.rename_label(db, b"muirs highway", b"openlion test")
        out = carin.replace_db(img, new)
        self.assertEqual(len(out), len(img))
        self.assertEqual(carin.read_db(out)[1], new)
        dayton = next(x for x in carin.iso_files(out) if x.path == "/DAYTON")
        self.assertEqual(out[dayton.lba * 2048:dayton.lba * 2048 + 42], img[dayton.lba * 2048:dayton.lba * 2048 + 42])
        with self.assertRaises(ValueError):
            carin.replace_db(img, db + bytes(P))


class Coords(unittest.TestCase):
    def test_known_points(self):
        # POI records on the factory disc: Perth CBD and Broome.
        lon, lat = carin.to_lonlat(0x304BC800, struct.unpack(">i", bytes.fromhex("f56b4300"))[0])
        self.assertAlmostEqual(lon, 115.85, delta=0.01)
        self.assertAlmostEqual(lat, -31.95, delta=0.01)
        lon, lat = carin.to_lonlat(0x326828C0, struct.unpack(">i", bytes.fromhex("fa0d3b00"))[0])
        self.assertAlmostEqual(lon, 122.22, delta=0.01)
        self.assertAlmostEqual(lat, -17.96, delta=0.01)

    def test_inverse(self):
        x, y = carin.from_lonlat(149.13, -35.28)
        lon, lat = carin.to_lonlat(x, y)
        self.assertAlmostEqual(lon, 149.13, places=6)
        self.assertAlmostEqual(lat, -35.28, places=6)


if __name__ == "__main__":
    unittest.main()
