"""build.build() replaces files whatever the card's folder case (MY10 has edb/poi, not
edb/POI), keeps the card's spelling, and refuses a replacement it can't place.

  python -m unittest discover -s tests -p "test_*.py"
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import build  # noqa: E402


class ReplacePaths(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "src")
        os.makedirs(os.path.join(self.src, "db_aunz", "edb", "poi"))
        with open(os.path.join(self.src, "db_aunz", "edb", "poi", "aunz_sql.db3"), "wb") as f:
            f.write(b"factory")
        with open(os.path.join(self.src, "keep.txt"), "wb") as f:
            f.write(b"keep")

    def tearDown(self):
        self.tmp.cleanup()

    def test_case_differs(self):
        dst = os.path.join(self.tmp.name, "dst")
        rep = build.build(self.src, dst, {build.POI_DB: b"new"})
        self.assertEqual(rep["replaced"], ["db_aunz/edb/poi/aunz_sql.db3"])
        self.assertEqual(rep["copied"], 1)
        with open(os.path.join(dst, "db_aunz", "edb", "poi", "aunz_sql.db3"), "rb") as f:
            self.assertEqual(f.read(), b"new")

    def test_missing_target_is_an_error(self):
        os.remove(os.path.join(self.src, "db_aunz", "edb", "poi", "aunz_sql.db3"))
        with self.assertRaises(ValueError):
            build.build(self.src, os.path.join(self.tmp.name, "dst"), {build.POI_DB: b"new"})


if __name__ == "__main__":
    unittest.main()
