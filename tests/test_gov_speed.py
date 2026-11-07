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


class FeatureSpeedTest(unittest.TestCase):
    def test_wa_signed_limit(self):
        self.assertEqual(gov_speed.feature_speed({"SPEED_LIMIT": "60km/h", "CWY": "Single"}), (60, None))
        self.assertEqual(gov_speed.feature_speed({"SPEED_LIMIT": "110km/h"})[0], 110)

    def test_wa_default_rule_is_left_out(self):
        v = "50km/h applies in built up areas or 110km/h outside built up areas"
        speed, why = gov_speed.feature_speed({"SPEED_LIMIT": v})
        self.assertIsNone(speed)
        self.assertIn("default rule", why)

    def test_wa_odd_values_are_left_out(self):
        for v in ("", None, "Variable", "0km/h", "200km/h"):
            self.assertIsNone(gov_speed.feature_speed({"SPEED_LIMIT": v})[0], v)

    def test_nsw_kept_types(self):
        for kind, v in (("Ordinary Permanent", "80 km/h"), ("Default", "50 km/h"), ("High Pedestrian", "40 km/h"),
                        ("Shared", "10 km/h"), ("Local Traffic", "40 km/h")):
            p = {"sz_type": kind, "sz_status": "Existing", "sz_direction": "Both Directions", "sz_speed": v}
            self.assertEqual(gov_speed.feature_speed(p)[0], int(v.split()[0]), kind)

    def test_nsw_part_time_zones_are_left_out(self):
        for kind in ("School", "School Bus", "Variable", "Wet Weather", "Toll Plaza", "Truck & bus"):
            speed, why = gov_speed.feature_speed({"sz_type": kind, "sz_status": "Existing", "sz_speed": "40 km/h"})
            self.assertIsNone(speed, kind)
            self.assertIn("part-time", why)

    def test_nsw_proposed_is_left_out(self):
        p = {"sz_type": "Ordinary Permanent", "sz_status": "Proposed", "sz_speed": "60 km/h"}
        self.assertIsNone(gov_speed.feature_speed(p)[0])

    def test_act_values(self):
        self.assertEqual(gov_speed.feature_speed({"SPEED_ZONE": "60", "CARRIAGEWAY": "ASCENDING"}), (60, None))
        speed, why = gov_speed.feature_speed({"SPEED_ZONE": "SCHOOL ZONE", "CARRIAGEWAY": "DESCENDING"})
        self.assertIsNone(speed)
        self.assertIn("part-time", why)
        self.assertIsNone(gov_speed.feature_speed({"SPEED_ZONE": "", "CARRIAGEWAY": "ASCENDING"})[0])

    def test_tas_keeps_the_lower_direction(self):
        tas = {"CWAY_CODE": "A", "DSG_NAME": "Tasman HWY"}
        self.assertEqual(gov_speed.feature_speed({**tas, "PD": 60, "CD": 80}), (60, None))
        self.assertEqual(gov_speed.feature_speed({**tas, "PD": 100, "CD": 0}), (100, None))   # one carriageway
        self.assertEqual(gov_speed.feature_speed({**tas, "PD": None, "CD": 70}), (70, None))
        self.assertIsNone(gov_speed.feature_speed({**tas, "PD": None, "CD": None})[0])

    def test_victoria_still_reads(self):
        self.assertEqual(gov_speed.feature_speed({"speed_limit": "80", "zone_conditions": ["Normal Operation"]}), (80, None))
        self.assertIsNone(gov_speed.feature_speed({"speed_limit": "40", "zone_conditions": ["School"]})[0])


if __name__ == "__main__":
    unittest.main()
