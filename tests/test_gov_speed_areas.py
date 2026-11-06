"""gov_speed: NZ-style speed zone polygons (smallest zone wins, unsure near a corridor's edge)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import gov_speed  # noqa: E402


def zone(ring, speed, reason="Speed Area", category="Permanent"):
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {"speedLimitZoneValue": str(speed), "speedCategoryName": category,
                           "speedLimitZoneReasonName": reason}}


def box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


# A 100 km/h background 0.2 deg square (~20 km, so on the coarse grid), an 80 km/h corridor
# ~22 m wide (0.00025 deg) running north-south through it at lon 175.05, and a variable
# school zone that must be ignored.
ZONES = [
    zone(box(174.95, -41.10, 175.15, -40.90), 100, "Default Area"),
    zone(box(175.049875, -41.02, 175.050125, -40.98), 80),
    zone(box(175.10, -41.00, 175.11, -40.99), 30, "The presence of a school", "Variable"),
]


def pts(coords):
    return [(round(x * 1e5), round(y * 1e5)) for x, y in coords]


class AreaIndexTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        src = os.path.join(cls.tmp.name, "nz.geojson")
        with open(src, "w", encoding="utf-8") as fh:
            json.dump({"type": "FeatureCollection", "features": ZONES}, fh)
        cls.bin = os.path.join(cls.tmp.name, "nz.bin")
        cls.stats = gov_speed.prepare(src, cls.bin)
        cls.ix = gov_speed.open_index(cls.bin)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_prepared_as_areas(self):
        self.assertIsInstance(self.ix, gov_speed.AreaIndex)
        self.assertEqual(self.stats["zones"], 2)
        self.assertEqual(self.stats["background zones"], 1)
        self.assertEqual(self.stats["skipped (not permanent)"], 1)

    def test_road_in_the_corridor_takes_its_limit(self):
        self.assertEqual(self.ix.lookup(pts([[175.05, -41.01], [175.05, -40.99]])), 80)

    def test_road_away_from_corridors_takes_the_background(self):
        self.assertEqual(self.ix.lookup(pts([[175.00, -41.05], [175.00, -41.03]])), 100)

    def test_road_just_outside_the_corridor_is_unsure(self):
        # ~21 m east of the corridor's middle, ~10 m outside its edge: not the 100 background
        self.assertIsNone(self.ix.lookup(pts([[175.05025, -41.01], [175.05025, -40.99]])))

    def test_road_well_clear_of_the_corridor_takes_the_background(self):
        # ~29 m east of the middle, ~19 m outside the edge (past NEAR_M)
        self.assertEqual(self.ix.lookup(pts([[175.05035, -41.01], [175.05035, -40.99]])), 100)

    def test_background_is_not_used_for_tracks(self):
        road = pts([[175.00, -41.05], [175.00, -41.03]])
        self.assertIsNone(self.ix.lookup(road, "track"))
        self.assertEqual(self.ix.lookup(pts([[175.05, -41.01], [175.05, -40.99]]), "track"), 80)

    def test_part_time_zone_is_ignored(self):
        self.assertEqual(self.ix.lookup(pts([[175.103, -40.998], [175.107, -40.998]])), 100)

    def test_outside_every_zone(self):
        self.assertIsNone(self.ix.lookup(pts([[176.0, -41.0], [176.0, -40.99]])))

    def test_nz_feature_speed(self):
        self.assertEqual(gov_speed.feature_speed({"speedLimitZoneValue": "50", "speedCategoryName": "Permanent"}), (50, None))
        self.assertIsNone(gov_speed.feature_speed({"speedLimitZoneValue": "30", "speedCategoryName": "Variable"})[0])


if __name__ == "__main__":
    unittest.main()
