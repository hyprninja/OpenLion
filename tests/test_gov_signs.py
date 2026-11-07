"""Speed limits walked on from speed signs (Queensland), on a small made-up road network."""
import array
import csv
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import gov_signs, gov_speed, osm_import  # noqa: E402

X0, Y0 = 15300000, -2750000     # near Brisbane, in 1e-5 degree


def way(wid, tags, pts):
    ids = array.array("q", (n for n, _, _ in pts))
    xy = array.array("i", (v for _, x, y in pts for v in (x, y)))
    return wid, tags, osm_import.WayPoints(ids, xy)


def network(main_tags=None, last_tags=None):
    """Test Road runs east 3 km (three pieces, split by two cross streets); its last piece
    is a separate way so it can carry its own tags."""
    c = osm_import._Collector(None)
    road = {"highway": "secondary", "name": "Test Road", **(main_tags or {})}
    c.ways.append(way(1, road, [(1, X0, Y0), (2, X0 + 1000, Y0), (3, X0 + 2000, Y0)]))
    c.ways.append(way(2, {**road, **(last_tags or {})}, [(3, X0 + 2000, Y0), (4, X0 + 3000, Y0)]))
    for k, x in ((3, 1000), (4, 2000)):
        c.ways.append(way(10 + k, {"highway": "residential", "name": "Cross Street"},
                          [(100 + k, X0 + x, Y0 - 500), (k - 1, X0 + x, Y0), (200 + k, X0 + x, Y0 + 500)]))
    return c


def sign(lon_units, speed, direction="EAST", street="TEST", st_type="ROAD", num="R4-1B", text=""):
    return {"NUMBER": "1", "LATITUDE": f"{(Y0 + 5) / 1e5:.8f}", "LONGITUDE": f"{(X0 + lon_units) / 1e5:.8f}",
            "SPEED": str(speed), "SIGN_TXT": text, "SIGN_NOTE": "", "SIGN_NUM": num, "SIDE": "LEFT",
            "ST_NAME": street, "ST_TYPE": st_type, "LOCALITY": "TEST", "DIRECTION": direction}


def main_speeds(g):
    """Test Road's pieces west to east -> km/h or None."""
    out = []
    for s in range(g.n_segs):
        if g.seg_label[s][0] == "Test Road":
            out.append((g.node_xy[g.seg_a[s]][0], (g.seg_adas[s] or {}).get("speed_kmh")))
    return [v for _, v in sorted(out)]


class GovSignsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def signs(self, rows):
        path = os.path.join(self.tmp, f"signs{len(os.listdir(self.tmp))}.csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        return gov_speed.load(path, self.tmp)

    def build(self, rows, c=None):
        ss = self.signs(rows)
        g, _, st = osm_import.build_graph(c or network(), sign_fill=gov_speed.sign_fill([ss]))
        return g, st

    def test_limit_runs_on_to_the_next_sign(self):
        g, st = self.build([sign(50, 60), sign(1900, 80)])
        self.assertEqual(main_speeds(g), [60, 60, 80])
        self.assertEqual(st["speed limit from government speed signs"], 3)

    def test_cross_streets_are_not_filled(self):
        g, _ = self.build([sign(50, 60)])
        for s in range(g.n_segs):
            if g.seg_label[s][0] == "Cross Street":
                self.assertIsNone(g.seg_adas[s])

    def test_osm_limit_is_kept(self):
        g, _ = self.build([sign(50, 60)], network(last_tags={"maxspeed": "50"}))
        self.assertEqual(main_speeds(g), [60, 60, 50])

    def test_sign_facing_the_other_way_only_fills_behind_it(self):
        g, _ = self.build([sign(2950, 70, direction="WEST")])
        self.assertEqual(main_speeds(g), [70, 70, 70])
        g, _ = self.build([sign(1050, 70, direction="WEST")])
        self.assertEqual(main_speeds(g), [70, None, None])

    def test_other_way_sign_with_another_speed_ends_the_walk(self):
        g, _ = self.build([sign(50, 60), sign(1950, 80, direction="WEST")])
        self.assertEqual(main_speeds(g), [60, 60, None])

    def test_walk_stops_at_the_cap(self):
        old = gov_signs.CAP_M
        gov_signs.CAP_M = 1500
        try:
            g, _ = self.build([sign(50, 60)])
        finally:
            gov_signs.CAP_M = old
        self.assertEqual(main_speeds(g), [60, None, None])

    def test_sign_named_for_another_road_is_not_used(self):
        g, st = self.build([sign(50, 60, street="OTHER")])
        self.assertEqual(main_speeds(g), [None, None, None])
        self.assertEqual(st["signs whose road has another name in OSM"], 1)

    def test_motorways_are_not_filled(self):
        g, _ = self.build([sign(50, 100)], network(main_tags={"highway": "motorway"}))
        self.assertEqual(main_speeds(g), [None, None, None])

    def test_part_time_and_default_records_are_left_out(self):
        ok, _ = gov_signs.sign_row(sign(50, 60))
        self.assertIsNotNone(ok)
        for row, why in ((sign(50, 40, num="R4-Q01A", text="SCHOOL ZONE 7-9 2-4 SCHOOL DAYS"), "not a plain"),
                         (sign(50, 60, text="ROAD WORK"), "sign with text"),
                         (sign(50, 50, num="UNSIGNED"), "not a plain"),
                         (sign(50, 40, num="R4-10A"), "not a plain"),
                         ({**sign(50, 0), "SPEED": "VARIABLE"}, "no speed"),
                         (sign(50, 60, direction="UP"), "no speed")):
            v, reason = gov_signs.sign_row(row)
            self.assertIsNone(v)
            self.assertTrue(reason.startswith(why), reason)

    def test_names_match_whatever_the_spelling(self):
        self.assertEqual(gov_signs.norm_name("Stuart Drive"), gov_signs.norm_name("STUART DR"))
        self.assertEqual(gov_signs.norm_name("Old Cleveland Road East"), "OLD CLEVELAND RD EAST")
        self.assertIsNone(gov_signs.norm_name(None))

    def test_prepared_file_type(self):
        ss = self.signs([sign(50, 60), sign(1900, 80)])
        self.assertIsInstance(ss, gov_signs.SignSet)
        self.assertEqual(ss.speeds, [60, 80])
        self.assertFalse(ss.covers([(X0, Y0), (X0 + 10, Y0)]))
        prepared = [f for f in os.listdir(self.tmp) if f.endswith(".bin")]
        self.assertIsInstance(gov_speed.open_index(os.path.join(self.tmp, prepared[0])), gov_signs.SignSet)


if __name__ == "__main__":
    unittest.main()
