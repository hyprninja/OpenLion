"""School zones, level crossings (alerts.py) and New Zealand's LINZ readers (gov_nz.py)."""
import array
import gzip
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import alerts, gov_nz  # noqa: E402
from openlion.osm_import import WayPoints  # noqa: E402

X, Y = 15160000, -3280000           # near Maitland, NSW


def way(wid, pts, ids=None, **tags):
    ids = array.array("q", ids or [wid * 100 + k for k in range(len(pts))])
    xy = array.array("i", [v for p in pts for v in p])
    return wid, {"highway": "residential", **tags}, WayPoints(ids, xy)


class StateIndex:
    """Everything is in New South Wales, except points east of X + 5000 (in "Victoria")."""
    class A:
        def __init__(self, name):
            self.name = name
    areas = [A("New South Wales"), A("Victoria")]

    def find(self, x, y):
        return 1 if x > X + 5000 else 0


class Tags(unittest.TestCase):
    def test_school_speed(self):
        self.assertEqual(alerts.school_speed({"maxspeed:conditional": "40 @ (Mo-Fr 08:00-09:30,14:30-16:00; SH off)"}), 40)
        self.assertEqual(alerts.school_speed({"maxspeed:conditional": "40 @ (Mo-Fr 07:00-09:00, 14:00-16:00)"}), 40)
        self.assertIsNone(alerts.school_speed({"maxspeed:conditional": "40 @ (07:00-19:00)"}))
        self.assertIsNone(alerts.school_speed({"maxspeed:conditional": "80 @ wet"}))
        self.assertEqual(alerts.school_speed({"hazard": "school_zone"}), 40)
        self.assertIsNone(alerts.school_speed({"highway": "primary"}))

    def test_parse_nsw_crossings_keeps_no_error(self):
        doc = {"features": [
            {"properties": {"ACTIVE": "Y", "LOC_ERROR": "NO ERROR", "ROAD_NAME": "A Road", "CROSSING_NUMBER": "1"},
             "geometry": {"type": "Point", "coordinates": [151.6, -32.8]}},
            {"properties": {"ACTIVE": "Y", "LOC_ERROR": "NO ROAD"}, "geometry": {"type": "Point", "coordinates": [1, 2]}},
            {"properties": {"ACTIVE": "Y", "LOC_ERROR": None}, "geometry": {"type": "Point", "coordinates": [3, 4]}}]}
        out = alerts.parse_nsw_crossings(doc)
        self.assertEqual([c["id"] for c in out], ["1", None])

    def test_parse_wa_crossings_public_in_use(self):
        feats = [{"properties": {"XING_TYPE": "Public", "IN_USE": None, "XING_NO": "1"}, "geometry": {"type": "Point", "coordinates": [1, 2]}},
                 {"properties": {"XING_TYPE": "Private", "XING_NO": "2"}, "geometry": {"type": "Point", "coordinates": [1, 2]}},
                 {"properties": {"XING_TYPE": "Public", "IN_USE": "No", "XING_NO": "3"}, "geometry": {"type": "Point", "coordinates": [1, 2]}}]
        self.assertEqual([c["id"] for c in alerts.parse_wa_crossings(feats)], ["1"])


class Crossings(unittest.TestCase):
    def test_osm_crossing_one_poi_per_direction(self):
        # an east-west two-way road with the crossing node in the middle, a one-way street with another
        road = way(1, [(X - 300, Y), (X, Y), (X + 300, Y)], ids=[1, 2, 3], name="Station Street")
        ow = way(2, [(X, Y - 1000), (X, Y - 700)], ids=[10, 11], oneway="yes", name="One Way")
        osm = {"AU": {"crossings": [(2, X, Y), (11, X, Y - 700)], "school": {}}}
        out, rep = alerts.place([road, ow], osm, [], StateIndex(), ["AU"])
        rail = [a for a in out if a["cat"] == "railway"]
        heads = sorted(a["heading"] for a in rail)
        self.assertEqual(heads, [0, 90, 270])             # both ways on the road, northbound on the one-way
        self.assertTrue(all(a["state"] == "NSW" and a["speed"] is None for a in rail))
        nsw = next(s for s in rep["states"] if s["code"] == "NSW")
        self.assertEqual(nsw["crossings"]["crossings"], 2)

    def test_gov_crossing_added_only_where_osm_has_none(self):
        road = way(1, [(X - 300, Y), (X + 300, Y)], ids=[1, 2], name="A Road")
        far = way(2, [(X - 300, Y + 2000), (X + 300, Y + 2000)], ids=[5, 6], name="B Road")
        osm = {"AU": {"crossings": [], "school": {}}}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "NSW_crossings.json")
            items = [{"id": "near", "lon": X / 1e5, "lat": (Y + 10) / 1e5, "road": "A Road"},
                     {"id": "far", "lon": X / 1e5, "lat": (Y + 2015) / 1e5, "road": "B Road"}]
            json.dump({"state": "NSW", "kind": "crossings", "provider": "p", "fetched": "2026-11-08", "items": items},
                      open(p, "w", encoding="utf-8"))
            osm["AU"]["crossings"] = [(1, X - 300, Y)]      # OSM has the first one (within 100 m? no: 300 m away)
            out, rep = alerts.place([road, far], osm, [p], StateIndex(), ["AU"])
        rail = [a for a in out if a["cat"] == "railway"]
        # OSM crossing at the road's end (one direction arriving), both gov crossings snapped (two each)
        self.assertEqual(len(rail), 1 + 2 + 2)
        self.assertEqual(rep["stats"]["railway: NSW list, added (OSM didn't have them)"], 2)

    def test_gov_crossing_on_a_closed_line_is_left_out(self):
        road = way(1, [(X - 300, Y), (X + 300, Y)], ids=[1, 2], name="A Road")
        far = way(2, [(X - 300, Y + 2000), (X + 300, Y + 2000)], ids=[5, 6], name="B Road")
        cells = {(X // alerts.RAIL_CELL, Y // alerts.RAIL_CELL)}          # only the first road has a working line
        osm = {"AU": {"crossings": [], "school": {}, "rail_cells": cells}}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "WA_crossings.json")
            items = [{"id": "open", "lon": X / 1e5, "lat": (Y + 10) / 1e5, "road": "A Road"},
                     {"id": "closed", "lon": X / 1e5, "lat": (Y + 2015) / 1e5, "road": "B Road"}]
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"state": "WA", "kind": "crossings", "provider": "p", "fetched": "2026-11-08", "items": items}, f)
            out, rep = alerts.place([road, far], osm, [p], StateIndex(), ["AU"])
        self.assertEqual(len([a for a in out if a["cat"] == "railway"]), 2)      # both ways at the open one
        self.assertEqual(rep["stats"]["railway: WA list, not on a working railway (closed line)"], 1)


class SchoolZones(unittest.TestCase):
    def test_osm_zone_entries_from_side_streets_and_continuations(self):
        # zone way (2) in the middle of a north-south road, a side street joining its middle
        south = way(1, [(X, Y - 600), (X, Y - 200)], ids=[1, 2], name="Main Road")
        zone = way(2, [(X, Y - 200), (X, Y), (X, Y + 200)], ids=[2, 3, 4], name="Main Road")
        north = way(3, [(X, Y + 200), (X, Y + 600)], ids=[4, 5], name="Main Road")
        side = way(4, [(X + 400, Y), (X, Y)], ids=[6, 3], name="School Lane")
        osm = {"AU": {"crossings": [], "school": {2: 40}}}
        out, _ = alerts.place([south, zone, north, side], osm, [], StateIndex(), ["AU"])
        zones = [a for a in out if a["cat"] == "school_zone"]
        pts = [a for a in out if a["cat"] == "school_point"]
        self.assertEqual(len(zones), 1)
        self.assertEqual((zones[0]["speed"], zones[0]["heading"]), (40, None))
        # northbound from the south end, southbound from the north end, westbound from the side street
        self.assertEqual(sorted(a["heading"] for a in pts), [0, 180, 270])
        self.assertTrue(all(a["speed"] == 40 for a in pts))

    def test_gov_polygon_entries_and_osm_zone_dropped_in_that_state(self):
        road = way(1, [(X - 1000, Y), (X + 1000, Y)], ids=[1, 2], name="Main Road")
        zone_way = way(2, [(X - 100, Y + 3000), (X + 100, Y + 3000)], ids=[7, 8])
        osm = {"AU": {"crossings": [], "school": {2: 40}}}
        ring = [(X - 200, Y - 20), (X + 200, Y - 20), (X + 200, Y + 20), (X - 200, Y + 20), (X - 200, Y - 20)]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "NSW_school_zones.json")
            json.dump({"state": "NSW", "kind": "school_zones", "provider": "p", "fetched": "2026-11-08",
                       "items": [{"id": "z", "name": "A School", "speed": 40, "oneway": False,
                                  "rings": [[(x / 1e5, y / 1e5) for x, y in ring]]}]}, open(p, "w", encoding="utf-8"))
            out, rep = alerts.place([road, zone_way], osm, [p], StateIndex(), ["AU"])
        pts = [a for a in out if a["cat"] == "school_point"]
        self.assertEqual(sorted(a["heading"] for a in pts), [90, 270])
        self.assertTrue(any(abs(a["x"] - (X - 200)) <= 1 for a in pts))     # on the polygon's edge
        self.assertEqual(len([a for a in out if a["cat"] == "school_zone"]), 1)   # the OSM one in NSW is dropped
        self.assertEqual(rep["stats"]["school zones: OSM zone in NSW (official list used)"], 1)


class Linz(unittest.TestCase):
    def test_addresses_units_merge_and_ranges(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.csv.gz")
            with gzip.open(p, "wt", encoding="utf-8") as f:
                f.write("lon,lat,number,high,road_id,road\n")
                f.write("175.10000,-37.70000,12,,9,Main Road\n")
                f.write("175.10002,-37.70002,12,,9,Main Road\n")       # a unit at 12
                f.write("175.10100,-37.70100,20,24,9,Main Road\n")
                f.write("175.20000,-37.80000,5,,10,Te Awa Street\n")
            a = gov_nz.read_addresses(p)
        got = sorted((a.names[a.street[i]], a.num[i]) for i in range(len(a)))
        self.assertEqual(got, [("MAIN ROAD", 12), ("MAIN ROAD", 20), ("MAIN ROAD", 24), ("TE AWA STREET", 5)])

    def test_suburbs_town_and_holes(self):
        sq = lambda x0, y0, s: [(x0, y0), (x0 + s, y0), (x0 + s, y0 + s), (x0, y0 + s), (x0, y0)]  # noqa: E731
        doc = [{"id": 1, "name": "Te Rapa", "type": "Suburb", "major": "Hamilton", "rings": [sq(0, 0, 100), sq(40, 40, 20)]},
               {"id": 2, "name": "Lyttelton", "type": "Suburb", "major": "Lyttelton", "rings": [sq(500, 0, 50)]},
               {"id": 3, "name": "Elaine Bay", "type": "Locality", "major": "Marlborough Sounds",
                "major_type": "Major Locality", "rings": [sq(900, 0, 50)]}]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json.gz")
            with gzip.open(p, "wt", encoding="utf-8") as f:
                json.dump(doc, f)
            areas = gov_nz.read_suburbs(p, 1)
        tr, ly, eb = areas
        self.assertEqual((tr.town, ly.town, eb.town, tr.level, tr.country), ("Hamilton", None, None, 9, 1))
        self.assertEqual(tr.osm_id, "linz:1")
        self.assertTrue(tr.contains(10, 10))
        self.assertFalse(tr.contains(50, 50))          # in the hole
        self.assertTrue(ly.contains(510, 10))


class GovDataByCountry(unittest.TestCase):
    """Only the chosen countries' official lists are offered: LINZ only with New Zealand on the card."""

    def test_sources_follow_the_countries(self):
        from openlion import countries, make_card
        with tempfile.TemporaryDirectory() as d:
            au = [s["id"] for s in make_card.gov_data_sources(d, [(countries.AU, "au.pbf")], {})]
            nz = [s["id"] for s in make_card.gov_data_sources(d, [(countries.NZ, "nz.pbf")], {})]
        self.assertFalse(any(i.startswith("NZ_") for i in au))
        self.assertIn("NSW_school_zones", au)
        self.assertEqual(nz, ["NZ_addresses", "NZ_suburbs"])         # no Australian lists on an NZ-only card


if __name__ == "__main__":
    unittest.main()
