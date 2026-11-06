"""House-number ranges (report 24): encoding, number parsing, fitting, outliers."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import mapwrite, osm_addr  # noqa: E402


class RangeEncoding(unittest.TestCase):
    def test_round_trip(self):
        for code in mapwrite.HN_SIDE:
            for base, delta in ((1, 0), (9, -8), (444, 22), (65535, -511), (2, 511), (70000, 300),
                                (10, 4000), (100000, -131072)):
                kind, rng = mapwrite.encode_range(code, base, delta)
                self.assertEqual(len(rng), 4 if kind == "short_addr" else 6)
                self.assertEqual(mapwrite.decode_range(kind, rng), (code, base, delta))

    def test_short_layout(self):
        # 10-bit signed delta: -8 = 0x3F8 -> top bits 3 in addrinfo, 0xF8 in the delta byte
        self.assertEqual(mapwrite.encode_range(13, 9, -8), ("short_addr", bytes([0xCD, 0xF8, 9, 0])))
        self.assertEqual(mapwrite.encode_range(3, 444, 22), ("short_addr", bytes([3, 22, 0xBC, 1])))
        self.assertEqual(mapwrite.encode_range(3, 1, 512)[0], "long_addr")

    def test_too_big(self):
        with self.assertRaises(ValueError):
            mapwrite.encode_range(3, 1 << 24, 0)


class Numbers(unittest.TestCase):
    def test_parse(self):
        cases = {"12": [12], "12A": [12], "3/45": [45], "1-5": [1, 5], "12;14": [12, 14],
                 "Unit 3, 45": [45], "Lot 5": [], "": [], "0": [], "100-1": [100]}
        for text, want in cases.items():
            self.assertEqual(osm_addr.numbers(text), want, text)

    def test_fit(self):
        self.assertEqual(osm_addr._fit([(0.1, 2), (0.5, 10), (0.9, 18)]), ("even", 2, 20))
        self.assertEqual(osm_addr._fit([(0.1, 19), (0.5, 11), (0.9, 3)]), ("odd", 21, 1))
        self.assertEqual(osm_addr._fit([(0.5, 7)]), ("odd", 7, 7))
        self.assertEqual(osm_addr._fit([(0.2, 1), (0.4, 2), (0.6, 3), (0.8, 4)])[0], "mixed")
        # one stray odd number on an even side is dropped, not made "mixed"
        obs = [(t / 10, 2 * t) for t in range(1, 10)] + [(0.5, 7)]
        self.assertEqual(osm_addr._fit(obs)[0], "even")

    def test_trim_to_segment(self):
        # a stray huge number on a short segment side is dropped, not stretched over
        obs = [(0.1, 3), (0.3, 5), (0.5, 7), (0.9, 132647)]
        self.assertEqual(osm_addr._fit(obs, max_span=60), ("odd", 1, 11))
        self.assertLessEqual(max(osm_addr._fit([(0.5, 65535), (0.6, 65533)])[1:]), 65535)

    def test_outliers(self):
        a = osm_addr.Addresses()
        for k in range(10):
            a.add("LONG STREET", 100, 1000 + 30 * k, [21 + 2 * k])
        a.add("LONG STREET", 101, 1000 + 30 * 8 + 5, [7])         # a stray '7' among 37, 39
        bad = osm_addr.outliers(a)
        self.assertEqual([a.num[i] for i in range(len(a)) if bad[i]], [7])

    def test_pickle(self):
        import pickle
        a = osm_addr.Addresses()
        a.add("A STREET", 1, 2, [3, 5])
        b = pickle.loads(pickle.dumps(a))
        b.add("A STREET", 1, 2, [7])
        self.assertEqual((b.names, list(b.num), list(b.street)), (["A STREET"], [3, 5, 7], [0, 0, 0]))


if __name__ == "__main__":
    unittest.main()
