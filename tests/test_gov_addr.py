"""G-NAF reader and OSM-first merge (report 25), on a tiny made-up zip in G-NAF's layout."""
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import gov_addr, osm_addr  # noqa: E402

REL = "G-NAF/G-NAF TEST 2026/"
STREETS = """STREET_LOCALITY_PID|DATE_CREATED|DATE_RETIRED|STREET_CLASS_CODE|STREET_NAME|STREET_TYPE_CODE|STREET_SUFFIX_CODE|LOCALITY_PID|GNAF_STREET_PID|GNAF_STREET_CONFIDENCE|GNAF_RELIABILITY_CODE
ST1|2020-01-01||C|EXAMPLE|STREET||L1|1|-1|4
ST2|2020-01-01||C|TEST|ROAD|N|L1|2|-1|4
ST3|2020-01-01|2021-01-01|C|GONE|LANE||L1|3|-1|4
"""
DETAIL_COLS = ("ADDRESS_DETAIL_PID|DATE_CREATED|DATE_LAST_MODIFIED|DATE_RETIRED|BUILDING_NAME|LOT_NUMBER_PREFIX|"
               "LOT_NUMBER|LOT_NUMBER_SUFFIX|FLAT_TYPE_CODE|FLAT_NUMBER_PREFIX|FLAT_NUMBER|FLAT_NUMBER_SUFFIX|"
               "LEVEL_TYPE_CODE|LEVEL_NUMBER_PREFIX|LEVEL_NUMBER|LEVEL_NUMBER_SUFFIX|NUMBER_FIRST_PREFIX|NUMBER_FIRST|"
               "NUMBER_FIRST_SUFFIX|NUMBER_LAST_PREFIX|NUMBER_LAST|NUMBER_LAST_SUFFIX|STREET_LOCALITY_PID|"
               "LOCATION_DESCRIPTION|LOCALITY_PID|ALIAS_PRINCIPAL|POSTCODE|PRIVATE_STREET|LEGAL_PARCEL_ID|CONFIDENCE|"
               "ADDRESS_SITE_PID|LEVEL_GEOCODED_CODE|PROPERTY_PID|GNAF_PROPERTY_PID|PRIMARY_SECONDARY").split("|")


def detail(pid, first, street, last="", flat="", alias="P", retired=""):
    v = dict.fromkeys(DETAIL_COLS, "")
    v.update(ADDRESS_DETAIL_PID=pid, NUMBER_FIRST=first, NUMBER_LAST=last, STREET_LOCALITY_PID=street,
             FLAT_NUMBER=flat, ALIAS_PRINCIPAL=alias, DATE_RETIRED=retired)
    return "|".join(v[c] for c in DETAIL_COLS)


DETAILS = "\n".join(["|".join(DETAIL_COLS),
                     detail("A1", "1", "ST1"),
                     detail("A2", "3", "ST1", flat="1"), detail("A3", "3", "ST1", flat="2"),   # 2 units at 3
                     detail("A4", "5", "ST1", last="9"),
                     detail("A5", "7", "ST1", alias="A"),                                      # alias
                     detail("A6", "11", "ST1", retired="2022-01-01"),                          # retired
                     detail("A7", "", "ST1"),                                                  # no number
                     detail("A8", "2", "ST2"),
                     detail("A9", "4", "ST3"),                                                 # retired street
                     detail("A10", "6", "ST2")]) + "\n"
GEOCODES = """ADDRESS_DEFAULT_GEOCODE_PID|DATE_CREATED|DATE_RETIRED|ADDRESS_DETAIL_PID|GEOCODE_TYPE_CODE|LONGITUDE|LATITUDE
1|2020-01-01||A1|FCS|149.00000|-35.00000
2|2020-01-01||A2|PC|149.00010|-35.00020
3|2020-01-01||A3|PC|149.00030|-35.00040
4|2020-01-01||A4|FCS|149.00050|-35.00050
5|2020-01-01||A5|FCS|149.00070|-35.00070
6|2020-01-01||A6|FCS|149.00090|-35.00090
7|2020-01-01||A7|FCS|149.00100|-35.00100
8|2020-01-01||A8|FCS|149.10000|-35.10000
9|2020-01-01||A9|FCS|149.20000|-35.20000
10|2020-01-01||A10|STL|149.10000|-35.10000
"""
SUFFIX = "CODE|NAME|DESCRIPTION\nN|NORTH|NORTH\n"


class GnafRead(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.zip = os.path.join(cls.dir.name, "g-naf_test.zip")
        with zipfile.ZipFile(cls.zip, "w") as z:
            z.writestr(REL + "Standard/ACT_STREET_LOCALITY_psv.psv", STREETS)
            z.writestr(REL + "Standard/ACT_ADDRESS_DETAIL_psv.psv", DETAILS)
            z.writestr(REL + "Standard/ACT_ADDRESS_DEFAULT_GEOCODE_psv.psv", GEOCODES)
            z.writestr(REL + "Authority Code/Authority_Code_STREET_SUFFIX_AUT_psv.psv", SUFFIX)

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def rows(self, a):
        return sorted((a.names[a.street[i]], a.num[i], a.x[i], a.y[i]) for i in range(len(a)))

    def test_read(self):
        a = gov_addr.read(self.zip)
        self.assertEqual(self.rows(a), [
            ("EXAMPLE STREET", 1, 14900000, -3500000),
            ("EXAMPLE STREET", 3, 14900020, -3500030),     # mean of its two units
            ("EXAMPLE STREET", 5, 14900050, -3500050),
            ("EXAMPLE STREET", 9, 14900050, -3500050),     # 5-9: both ends
            ("TEST ROAD NORTH", 2, 14910000, -3510000),    # suffix code spelled out
        ])                                                  # alias, retired, no number, STL left out

    def test_bbox(self):
        a = gov_addr.read(self.zip, bbox=(14905000, -3515000, 14915000, -3505000))
        self.assertEqual([r[:2] for r in self.rows(a)], [("TEST ROAD NORTH", 2)])

    def test_not_gnaf(self):
        other = os.path.join(self.dir.name, "other.zip")
        with zipfile.ZipFile(other, "w") as z:
            z.writestr("readme.txt", "hello")
        with self.assertRaises(ValueError):
            gov_addr.read(other)

    def test_combine_osm_first(self):
        osm = osm_addr.Addresses()
        osm.add("EXAMPLE STREET", 14900003, -3500002, [1])          # same number, 3 m away
        osm.add("EXAMPLE STREET", 14980000, -3500000, [5])          # same number, 80 km away
        g = gov_addr.read(self.zip)
        c = gov_addr.combine(osm, g)
        got = sorted((c.names[c.street[i]], c.num[i], c.x[i]) for i in range(len(c)))
        self.assertEqual([r for r in got if r[1] == 1], [("EXAMPLE STREET", 1, 14900003)])   # OSM's kept, G-NAF's dropped
        self.assertEqual(sum(1 for r in got if r[1] == 5), 2)                                # far apart: both kept
        self.assertEqual(len(c), 2 + len(g) - 1)


if __name__ == "__main__":
    unittest.main()
