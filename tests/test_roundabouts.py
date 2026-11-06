"""Roundabouts: OSM junction=roundabout ways get the factory roundabout marker (landmark = 2)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import mapfile  # noqa: E402
from openlion.osm_import import LANDMARK_ROUNDABOUT, oneway_dir, pack_fields, seginfo_fields  # noqa: E402


class Roundabouts(unittest.TestCase):
    def test_roundabout_is_marked(self):
        f = seginfo_fields({"highway": "residential", "junction": "roundabout"})
        self.assertEqual(f["landmark"], LANDMARK_ROUNDABOUT)
        self.assertEqual(oneway_dir({"highway": "residential", "junction": "roundabout"}), 1)

    def test_marker_survives_packing(self):
        v = pack_fields(seginfo_fields({"highway": "primary", "junction": "roundabout", "maxspeed": "60"}))
        self.assertEqual(mapfile.seginfo_fields(v)["landmark"], 2)

    def test_ordinary_roads_unmarked(self):
        for tags in ({"highway": "residential"}, {"highway": "primary", "oneway": "yes"},
                     {"highway": "tertiary", "junction": "circular"}):
            self.assertEqual(seginfo_fields(tags)["landmark"], 0, tags)


if __name__ == "__main__":
    unittest.main()
