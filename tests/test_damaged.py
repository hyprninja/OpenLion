"""Damaged or hostile card tables must fail with DamagedTable, not crash or allocate wildly.

  python -m unittest discover -s tests -p "test_*.py"
"""
import os
import struct
import sys
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import flat, mapfile, schema  # noqa: E402
from openlion.common import DamagedTable  # noqa: E402

MAGIC = b"zip\x01"


class FlatContainer(unittest.TestCase):
    def test_round_trip(self):
        raw = bytes(range(256)) * 50
        self.assertEqual(flat.unpack(flat.pack(raw)), raw)

    def test_empty_table_packs(self):
        self.assertEqual(flat.unpack(flat.pack(b"")), b"")

    def test_zero_block_size(self):
        d = MAGIC + struct.pack("<III", 0, 100, 0) + struct.pack("<I", 20)
        with self.assertRaises(DamagedTable):
            flat.unpack(d)

    def test_huge_total(self):
        d = MAGIC + struct.pack("<III", 0xC00, 0xFFFFFFF0, 0) + struct.pack("<3I", 28, 28, 28)
        with self.assertRaises(DamagedTable):
            flat.unpack(d)

    def test_offsets_past_end(self):
        d = MAGIC + struct.pack("<III", 0xC00, 0xC00 * 100, 0) + struct.pack("<2I", 24, 24)
        with self.assertRaises(DamagedTable):
            flat.unpack(d)

    def test_truncated_header(self):
        with self.assertRaises(DamagedTable):
            flat.unpack(MAGIC + b"\x00\x0c")

    def test_corrupt_block(self):
        d = bytearray(flat.pack(b"x" * 5000))
        d[-3] ^= 0xFF
        with self.assertRaises(DamagedTable):
            flat.unpack(bytes(d))

    def test_offsets_out_of_order(self):
        z = zlib.compress(b"a" * 10)
        d = MAGIC + struct.pack("<III", 0xC00, 10, len(z)) + struct.pack("<2I", 30, 24 + len(z)) + z
        with self.assertRaises(DamagedTable):
            flat.unpack(d)


def map_header(pcl_cnt, kd_cnt):
    return bytes(0x5E) + mapfile.MAPHDR.pack(0, 0, 0, 0, 0, pcl_cnt, kd_cnt, 0, 0, 0)


class MapFileHeaders(unittest.TestCase):
    def test_truncated_header(self):
        with self.assertRaises(DamagedTable):
            mapfile.MapFile(bytes(100))

    def test_huge_kd_count(self):
        with self.assertRaises(DamagedTable):
            mapfile.MapFile(map_header(5, 0x7FFFFFFF))

    def test_directory_past_end(self):
        with self.assertRaises(DamagedTable):
            mapfile.MapFile(map_header(5, 3))

    def test_parcel_outside_file(self):
        d = map_header(1, 0) + mapfile.HNDL.pack(1 << 20, 10, 10)
        m = mapfile.MapFile(d)
        with self.assertRaises(DamagedTable):
            m.parcel(0)

    def test_corrupt_parcel(self):
        head = map_header(1, 0)
        d = head + mapfile.HNDL.pack(len(head) + 8, 10, 4) + b"\x00\x01\x02\x03"
        with self.assertRaises(DamagedTable):
            mapfile.MapFile(d).parcel(0)

    def test_short_parcel_layout(self):
        with self.assertRaises(ValueError):
            schema.for_parcel(b"\x01")
        with self.assertRaises(ValueError):
            schema.for_parcel(struct.pack("<H", schema.V521.offs_len))


if __name__ == "__main__":
    unittest.main()
