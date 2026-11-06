"""MY10 compact parcel encoding (schema.V521_COMPACT): codecs round-trip, and the
layout is detected from the parcel header. Synthetic data only.

  python -m unittest discover -s tests -p "test_*.py"
"""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import mapfile, mapwrite, schema  # noqa: E402


class Compact(unittest.TestCase):
    def setUp(self):
        self.prev = schema.active()
        schema.use(schema.V521_COMPACT)

    def tearDown(self):
        schema.use(self.prev)

    def test_layout_sizes(self):
        s = schema.V521_COMPACT
        self.assertEqual((s.n_fields, s.trailer_len, s.offs_len), (77, 37, 228))
        self.assertIs(schema.for_parcel(struct.pack("<H", 228) + bytes(226)), s)
        self.assertNotIn("abs_addr_list_2", s.idx)

    def test_pair_example(self):
        # (7, -2): magnitudes interleave to 0b101110; lat sign bit, then one more byte
        out = bytearray()
        mapwrite.write_pair(7, -2, out)
        self.assertEqual(bytes(out), b"\xae\x01")
        self.assertEqual(mapwrite.read_pair(out, 0), ((7, -2), 2))

    def test_pair_round_trip(self):
        vals = [0, 1, -1, 2, 31, -32, 127, 1000, -4096, 32767, -32768]
        for a in vals:
            for b in vals:
                out = bytearray()
                mapwrite.write_pair(a, b, out)
                self.assertLessEqual(len(out), 5)
                self.assertEqual(mapwrite.read_pair(out, 0), ((a, b), len(out)))

    def test_varint_round_trip(self):
        for v in (0, 1, 127, 128, 300, 4095, 0xFFFF, 0x10000, 2 ** 31):
            out = bytearray()
            mapwrite.write_uv(v, out)
            self.assertEqual(mapwrite.read_uv(out, 0), (v, len(out)))

    def test_positions(self):
        blocks = mapwrite.blocks_from_points([(100, 200), (150, 180), (40000, 190), (40001, -5)])
        blob = mapwrite.encode_positions(blocks)
        self.assertEqual(mapwrite.decode_positions(blob), blocks)
        self.assertEqual(len(blocks), 2)            # new block when a delta leaves i16

    def test_nodes(self):
        nodes = [mapwrite.make_node([(3, 0), (700, 1)], 0b10, rank=2, depth=1),
                 mapwrite.make_node([(k, 0) for k in range(6)], 0b101, rank=1)]
        nodes.insert(2, {"continuation": True})
        blob = mapwrite.encode_nodes(nodes)
        self.assertEqual(len(blob), 2 + 1 + 2 + 4 + 6)   # no unused slots
        back = mapwrite.decode_nodes(blob, 3)
        self.assertEqual(mapwrite.encode_nodes(back), blob)
        self.assertEqual(back[1]["slots"], list(range(6)))

    def test_segments_layer0(self):
        segs = [{"seginfo": 2 | (7 << 3), "restr": None, "left_node": 0, "right_node": None,
                 "length_ft": 3573, "shape": [(19, -166), (39, -398)],
                 "names": [("short_addr", 120, b"\x0c\x00\x2a\x01"), ("abs_addr", 100, b"")]},
                {"seginfo": 1, "restr": (1, 2), "left_node": None, "right_node": None,
                 "length_ft": 5, "shape": [], "names": []}]
        blob = mapwrite.encode_segments(segs)
        back = mapwrite.decode_segments(blob, 2, layer0=True)
        self.assertEqual(back[0]["shape"], segs[0]["shape"])
        # abs links come first; road IDs are stored as base + delta
        self.assertEqual(back[0]["names"], [("abs_addr", 100, b""), ("short_addr", 120, b"\x0c\x00\x2a\x01")])
        self.assertEqual(back[1]["names"], [])
        self.assertEqual(mapwrite.encode_segments(back), blob)

    def test_segments_mg(self):
        segs = [{"seginfo": 3, "restr": None, "left_node": None, "right_node": 4, "length_ft": 99999}]
        blob = mapwrite.encode_segments(segs)
        back = mapwrite.decode_segments(blob, 1)
        self.assertNotIn("shape", back[0])
        self.assertEqual(back[0]["length_ft"], 99999)

    def test_parcel_reader(self):
        """A small mp0 parcel built like the tiler does reads back through mapfile.Parcel."""
        sch = schema.V521_COMPACT
        pc = mapwrite.ParcelContainer([0] * sch.n_fields, [0] * sch.trailer_len, {}, set(), sch)
        pts = [(1000, 2000), (1500, 2100)]
        pc.set("pos_list", mapwrite.encode_positions(mapwrite.blocks_from_points(pts)), 2)
        pc.set("exc_pos_list", b"", 0)
        nodes = [mapwrite.make_node([(0, 0)], 0), mapwrite.make_node([(0, 0)], 1)]
        pc.set("node_list", mapwrite.encode_nodes(nodes), 2)
        seg = {"seginfo": 0, "restr": None, "left_node": None, "right_node": None, "length_ft": 1800,
               "shape": [(200, 50)], "names": [("abs_addr", 4321, b"")]}
        pc.set("seg_list", mapwrite.encode_segments([seg]), 1)
        pc.fields[sch.idx["shape_cnt"]] = 1
        pc.fields[sch.idx["shape_size"]] = len(mapwrite.encode_shapes([seg["shape"]]))
        p = mapfile.Parcel(pc.encode())
        self.assertEqual(p.positions(), pts)
        self.assertEqual(len(p.nodes()), 2)
        self.assertEqual(p.segments()[0]["length_ft"], 1800)
        self.assertEqual(p._node_end, p.offs["seg"][0])
        self.assertEqual(p._seg_end, p.offs["attr"][0])
        self.assertEqual(p.shapes(), [[(200, 50)]])
        self.assertEqual([n["road_off"] for n in p.name_links()], [4321 + 0x5E])


if __name__ == "__main__":
    unittest.main()
