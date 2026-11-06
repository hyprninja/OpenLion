"""Speed cameras: parsing the government lists, choosing sources per state, snapping onto roads."""
import array
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import cameras, gov_cameras, osm_poi  # noqa: E402
from openlion.osm_import import WayPoints  # noqa: E402


def way(wid, pts, **tags):
    ids = array.array("q", [wid * 100 + k for k in range(len(pts))])
    xy = array.array("i", [v for p in pts for v in p])
    return wid, {"highway": "primary", **tags}, WayPoints(ids, xy)


# a divided road near Euroa: northbound carriageway (east side) and southbound (west side),
# 20 m apart, plus a two-way side street running east-west
X, Y = 14548800, -3679100
NB = way(1, [(X + 10, Y - 500), (X + 10, Y + 500)], oneway="yes", highway="motorway", name="Hume Freeway",
         maxspeed="110")
SB = way(2, [(X - 10, Y + 500), (X - 10, Y - 500)], oneway="yes", highway="motorway", name="Hume Freeway",
         maxspeed="110")
SIDE = way(3, [(X - 600, Y + 300), (X + 600, Y + 300)], highway="residential", name="Smith Street", maxspeed="50")
WAYS = [NB, SB, SIDE]


class StateIndex:
    """Everything is in Victoria, except points west of X - 400 (in "Queensland")."""
    class A:
        def __init__(self, name):
            self.name = name
    areas = [A("Victoria"), A("Queensland")]

    def find(self, x, y):
        return 1 if x < X - 400 else 0


class GovParsing(unittest.TestCase):
    def test_vic_id_suffix_and_bound(self):
        self.assertEqual(gov_cameras.vic_id_heading("H138N"), 0)
        self.assertEqual(gov_cameras.vic_id_heading("W33WB"), 270)
        self.assertEqual(gov_cameras.vic_id_heading("M152E"), 90)
        self.assertIsNone(gov_cameras.vic_id_heading("F07"))
        self.assertEqual(gov_cameras.bound_heading("Hume Freeway, Southbound, Creightons Creek"), 180)
        self.assertEqual(gov_cameras.bound_heading("TUGGERANONG PARKWAY ..., NORTHBOUND"), 0)

    def test_parse_vic(self):
        doc = {"hits": {"hits": [
            {"_source": {"camera_id": "H138N", "latitude": "-36.791", "longitude": "145.4877", "site_type": "Freeway",
                         "street": "Hume Freeway", "offence_location": "Hume Freeway, Northbound, Euroa"}},
            {"_source": {"camera_id": "F07", "latitude": "-37.74", "longitude": "145.02", "site_type": "Intersection",
                         "street": "Albert Street", "offence_location": "Albert Street, at the intersection of ..."}},
            {"_source": {"camera_id": "H99S", "latitude": "-36.98", "longitude": "145.14", "site_type": "Point-to-point",
                         "street": "Hume Freeway", "offence_location": "Hume Freeway, Southbound, Seymour"}},
            {"_source": {"camera_id": "X", "latitude": "", "longitude": "1"}}]}}
        c = gov_cameras.parse_vic(doc)
        self.assertEqual([(x["kind"], x["heading"]) for x in c], [("speed", 0), ("redlight_speed", None), ("p2p", 180)])

    def test_parse_act_skips_mobile_and_decommissioned(self):
        rows = [{"camera_type": "MOBILE SPEED CAMERA", "latitude": "-35.3", "longitude": "149.1"},
                {"camera_type": "FIXED ONLY SPEED CAMERA", "latitude": "-35.31930", "longitude": "149.06346",
                 "location_description": "TUGGERANONG PARKWAY NEAR COTTER ROAD OVERPASS, NORTHBOUND"},
                {"camera_type": "POINT TO POINT CAMERA", "latitude": "-35.39", "longitude": "149.07",
                 "location_description": "ATHLLON DRIVE SOUTHBOUND", "decommissioned_camera_date": "2016-11-01"},
                {"camera_type": "RED LIGHT AND SPEED CAMERA", "latitude": "-35.27941", "longitude": "149.12925",
                 "location_description": "NORTHBOURNE AVENUE/LONDON CIRCUIT"}]
        c = gov_cameras.parse_act(rows)
        self.assertEqual([(x["kind"], x["heading"], x["road"]) for x in c],
                         [("speed", 0, "Tuggeranong Parkway"), ("redlight_speed", None, "Northbourne Avenue")])


class Placing(unittest.TestCase):
    def gov_file(self, d, cams):
        p = os.path.join(d, "VIC.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"state": "VIC", "name": "Victoria", "provider": "test", "fetched": "2026-11-06",
                       "cameras": cams}, f)
        return p

    def test_heading_picks_carriageway(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.gov_file(d, [
                {"lat": Y / 1e5, "lon": X / 1e5, "kind": "speed", "heading": 0, "road": "Hume Freeway"},
                {"lat": Y / 1e5, "lon": X / 1e5, "kind": "speed", "heading": 180, "road": "Hume Freeway"}])
            out, rep = cameras.place(WAYS, {}, [p], StateIndex(), ["AU"])
        self.assertEqual(sorted((c["x"], c["heading"], c["speed"]) for c in out), [(X - 10, 180, 110), (X + 10, 0, 110)])
        vic = [s for s in rep["states"] if s["code"] == "VIC"][0]
        self.assertEqual((vic["source"], vic["cameras"]), ("gov", 2))

    def test_no_direction_oneway_gives_one_and_twoway_gives_two(self):
        osm = {"AU": [{"id": 900, "x": X + 12, "y": Y, "kind": "speed", "dir": None, "maxspeed": None},
                      {"id": 901, "x": X - 450, "y": Y + 305, "kind": "speed", "dir": None, "maxspeed": None}]}
        out, rep = cameras.place(WAYS, osm, [], StateIndex(), ["AU"])
        by_state = {}
        for c in out:
            by_state.setdefault(c["state"], []).append(c["heading"])
        self.assertEqual(by_state["VIC"], [0])                 # on the northbound carriageway
        self.assertEqual(sorted(by_state["QLD"]), [90, 270])   # two-way street: both ways

    def test_official_list_replaces_osm_in_its_state_only(self):
        osm = {"AU": [{"id": 900, "x": X + 12, "y": Y, "kind": "speed", "dir": None, "maxspeed": None},
                      {"id": 901, "x": X - 450, "y": Y + 305, "kind": "redlight", "dir": 90.0, "maxspeed": None}]}
        with tempfile.TemporaryDirectory() as d:
            p = self.gov_file(d, [{"lat": Y / 1e5, "lon": X / 1e5, "kind": "speed", "heading": 180, "road": None}])
            out, rep = cameras.place(WAYS, osm, [p], StateIndex(), ["AU"])
        self.assertEqual(sorted((c["state"], c["heading"], c["type"]) for c in out), [("QLD", 90, 0), ("VIC", 180, 1)])
        self.assertEqual(rep["osm_dropped"], {"VIC": 1})
        src = {s["code"]: s["source"] for s in rep["states"]}
        self.assertEqual((src["VIC"], src["QLD"], src["NSW"]), ("gov", "osm", "osm"))

    def test_osm_forward_backward_on_way(self):
        osm = {"AU": [{"id": 301, "x": X, "y": Y + 300, "kind": "speed", "dir": "backward", "maxspeed": None}]}
        out, _ = cameras.place([SIDE], osm, [], StateIndex(), ["AU"])
        self.assertEqual([c["heading"] for c in out], [270])

    def test_osm_direction_tag(self):
        self.assertEqual(cameras._osm_direction("NE"), 45)
        self.assertEqual(cameras._osm_direction("forward"), "forward")
        self.assertEqual(cameras._osm_direction("190"), 190.0)
        self.assertIsNone(cameras._osm_direction("both ways"))


class PoiRows(unittest.TestCase):
    def test_cameras_written_with_alert_attributes(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "factory.db3")
            con = sqlite3.connect(src)
            con.executescript("""
                CREATE TABLE String_BaseAttributes (String_ID integer primary key autoincrement, Value text);
                CREATE TABLE Category_BaseAttributes (Category_ID integer primary key, Name_String_ID integer, Icon_ID integer);
                CREATE TABLE PoiPartition_Category_Relation (PoiPartition_ID integer, Category_ID integer);
                CREATE TABLE Icon_BaseAttributes (Icon_ID integer primary key, Name_String_ID integer, Path_String_ID integer);
                CREATE TABLE Language_BaseAttributes (Language_ID integer primary key, IsoLanguageCode text, Language_String_ID integer);
                CREATE TABLE String_TranslationString_Relation (String_ID integer, Language_ID integer, Translation_String_ID integer);
                CREATE TABLE Poi_BaseAttributes (Poi_ID integer primary key autoincrement, PoiPartition_ID integer, Coordinate integer, Name_String_ID integer, Name text, PhoneNumber text);
                CREATE TABLE Poi_AddressAttributes (Poi_ID integer primary key, a, b, c, d, e, f, g, h);
                CREATE TABLE Poi_AlertAttributes (Poi_ID integer primary key, SpeedLimit integer, Direction integer, TimeStamp text, CameraType integer, FixtureStatus integer, SeverityRating integer);
                CREATE TABLE Poi_Relation (Poi_ID integer, RelationType integer, Related_Poi_ID integer);
                CREATE TABLE Poi_VSegAttributes (Poi_ID integer primary key, VSeg_ID integer, Direction integer);
                INSERT INTO String_BaseAttributes VALUES (1, 'restaurant'), (2, 'camera');
                INSERT INTO Category_BaseAttributes VALUES (4, 1, 1), (65, 2, 1);
                INSERT INTO PoiPartition_Category_Relation VALUES (4, 4), (65, 65);
            """)
            con.commit()
            con.close()
            out = os.path.join(d, "out.db3")
            import datetime
            st = osm_poi.build([(4, X, Y, {"name": "Cafe"})], src, out, cameras=[
                {"x": X, "y": Y, "heading": 180, "speed": 110, "type": 1, "road": "Hume Freeway", "state": "VIC"}],
                today=datetime.date(2026, 11, 6))
            con = sqlite3.connect(out)
            rows = con.execute("select b.PoiPartition_ID, b.Name, a.SpeedLimit, a.Direction, a.TimeStamp, a.CameraType, "
                               "a.FixtureStatus from Poi_BaseAttributes b join Poi_AlertAttributes a using (Poi_ID) "
                               "order by b.PoiPartition_ID").fetchall()
            con.close()
        self.assertEqual(st["cameras"], 1)
        # the card numbers "camera" 65 (as MY15/MY17 do): the row goes there, not to 55
        self.assertEqual(rows, [(4, "CAFE", None, None, None, None, None),
                                (65, "Camera", 110, 180, "06-NOV-26", 1, 1)])


if __name__ == "__main__":
    unittest.main()
