"""gov_speed: preparing a speed zone file and matching road pieces onto it."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import gov_speed  # noqa: E402


def feature(coords, speed, direction="North", conditions=("Normal Operation",)):
    return {"type": "Feature", "geometry": {"type": "MultiLineString", "coordinates": [coords]},
            "properties": {"speed_limit": str(speed), "direction": direction,
                           "zone_conditions": list(conditions)}}


# a north-south street at lon 145.0 from lat -37.80 to -37.79 (~1.1 km), as a direction
# pair digitised both ways; an east-west street with a school zone on it only
NS = [[145.0, -37.80], [145.0, -37.795], [145.0, -37.79]]
EW = [[145.01, -37.80], [145.02, -37.80]]
ZONES = [feature(NS, 60, "North"), feature(NS[::-1], 70, "South"),
         feature(EW, 40, "East", ("School",))]


def pts(coords):
    return [(round(x * 1e5), round(y * 1e5)) for x, y in coords]


class GovSpeedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        src = os.path.join(cls.tmp.name, "zones.geojson")
        with open(src, "w", encoding="utf-8") as fh:
            json.dump({"type": "FeatureCollection", "features": ZONES}, fh)
        cls.bin = os.path.join(cls.tmp.name, "zones.bin")
        cls.stats = gov_speed.prepare(src, cls.bin)
        cls.ix = gov_speed.Index(cls.bin)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_pair_kept_once_at_lower_speed(self):
        self.assertEqual(self.stats["lines"], 1)
        self.assertEqual(self.stats["direction pairs disagree"], 1)
        self.assertEqual(self.stats["skipped (not normal operation)"], 1)
        self.assertEqual(list(self.ix.speeds), [60])

    def test_simplify_drops_the_straight_middle_point(self):
        self.assertEqual(self.stats["points"], 2)

    def test_piece_on_the_line_matches(self):
        piece = pts([[145.0, -37.799], [145.0, -37.792]])
        self.assertEqual(self.ix.lookup(piece), 60)
        self.assertEqual(self.ix.lookup(piece[::-1]), 60)

    def test_piece_a_few_metres_off_matches(self):
        # OSM and the zone lines are traced separately: ~5 m apart is normal
        self.assertEqual(self.ix.lookup(pts([[145.00006, -37.799], [145.00006, -37.792]])), 60)

    def test_parallel_road_30_m_away_does_not(self):
        self.assertIsNone(self.ix.lookup(pts([[145.00034, -37.799], [145.00034, -37.792]])))

    def test_cross_street_does_not(self):
        self.assertIsNone(self.ix.lookup(pts([[144.999, -37.795], [145.001, -37.795]])))

    def test_school_zone_is_left_out(self):
        self.assertIsNone(self.ix.lookup(pts([[145.012, -37.80], [145.018, -37.80]])))

    def test_load_prepares_geojson_once(self):
        src = os.path.join(self.tmp.name, "zones.geojson")
        work = os.path.join(self.tmp.name, "work")
        os.makedirs(work)
        a = gov_speed.load(src, work)
        self.assertEqual(len(os.listdir(work)), 1)
        b = gov_speed.load(src, work)
        self.assertEqual(list(a.speeds), list(b.speeds))

    def test_bad_file_is_refused(self):
        bad = os.path.join(self.tmp.name, "bad.bin")
        with open(bad, "wb") as fh:
            fh.write(b"not a zone file")
        with self.assertRaises(ValueError):
            gov_speed.Index(bad)


if __name__ == "__main__":
    unittest.main()
