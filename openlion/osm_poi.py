"""OSM amenities -> the unit's POI database (edb/POI/aunz_sql.db3).

The POI database is plain SQLite (report 17). The unit queries it through compiled
statements stored in the database itself (PredefinedStatement), so the builder starts
from a copy of the factory file and keeps its schema, categories, icons, partitions,
languages, statements and database attributes exactly. Only the POI rows and their
strings are replaced:

  Poi_BaseAttributes     Poi_ID, PoiPartition_ID (= category), Coordinate, Name_String_ID,
                         Name, PhoneNumber ("61396704506") (MY10: the first four only)
  Poi_AddressAttributes  house number, street, city, region (state), country and phone
                         ("+(61)-3-96704506") as String_BaseAttributes IDs
  Poi_AlertAttributes    one row per POI, all NULL except for the road-sense alerts
  String_BaseAttributes  IDs 1..n are category/icon/language strings and are kept
                         (n = 421, or 537 on MY10)

Coordinate = Morton code: bit 2k is bit k of x, bit 2k+1 is bit k of y, with
x = (lon + 180) * 2^32 / 360 and y = (lat + 180) * 2^32 / 360.

Speed cameras (cameras.py, report 23) go in the "camera" category, found by name since
its ID differs per card: one POI per enforced direction, with Poi_AlertAttributes
SpeedLimit, Direction (travel heading), TimeStamp ('06-NOV-26'), CameraType and
FixtureStatus 1, as on the factory cards. The other road-sense categories (school zone,
blackspot, railway crossing, check speed, school point) are left empty. The factory
link/entry points (categories 82, 83) and POI relations aren't rebuilt.
"""
import collections
import datetime
import os
import re
import shutil
import sqlite3
import unicodedata

COUNTRY_TAG = "openlion:country"     # same private tag as osm_import.COUNTRY_TAG

KEYS = ("amenity", "shop", "tourism", "leisure", "place", "aeroway", "historic", "office",
        "craft", "railway", "highway", "public_transport")

# (tag, values, category) in priority order; values None = any value.
RULES = (
    ("amenity", {"fuel"}, 5),
    ("amenity", {"restaurant", "fast_food", "food_court"}, 4),
    ("amenity", {"cafe"}, 29),
    ("amenity", {"pub", "bar", "nightclub"}, 22),
    ("amenity", {"place_of_worship"}, 3),
    ("amenity", {"bank"}, 9),
    ("amenity", {"atm"}, 10),
    ("amenity", {"library"}, 11),
    ("amenity", {"school"}, 12),
    ("amenity", {"parking"}, 13),
    ("amenity", {"hospital"}, 19),
    ("amenity", {"townhall"}, 21),
    ("amenity", {"car_rental"}, 23),
    ("amenity", {"police"}, 24),
    ("amenity", {"university", "college"}, 27),
    ("amenity", {"theatre", "arts_centre"}, 32),
    ("amenity", {"cinema"}, 36),
    ("amenity", {"bus_station"}, 37),
    ("amenity", {"conference_centre", "exhibition_centre"}, 39),
    ("amenity", {"courthouse"}, 40),
    ("amenity", {"ferry_terminal"}, 41),
    ("amenity", {"post_office"}, 42),
    ("amenity", {"casino"}, 43),
    ("amenity", {"pharmacy"}, 47),
    ("amenity", {"doctors", "clinic", "dentist"}, 48),
    ("amenity", {"embassy"}, 49),
    ("amenity", {"grave_yard"}, 50),
    ("amenity", {"toilets"}, 60),
    ("office", {"diplomatic"}, 49),
    ("office", {"government"}, 62),
    ("shop", {"doityourself", "hardware", "trade"}, 1),
    ("shop", {"car_repair", "tyres", "car_parts"}, 2),
    ("shop", {"mall"}, 7),
    ("shop", {"supermarket", "greengrocer"}, 8),
    ("shop", {"car"}, 20),
    ("shop", {"chemist"}, 47),
    ("shop", {"electronics", "computer", "mobile_phone"}, 53),
    ("shop", {"convenience"}, 61),
    ("shop", {"department_store"}, 65),
    ("shop", {"stationery"}, 66),
    ("shop", {"books"}, 68),
    ("shop", {"clothes", "shoes"}, 69),
    ("shop", {"sports", "outdoor"}, 70),
    ("shop", {"winery"}, 25),
    ("craft", {"winery"}, 25),
    ("tourism", {"wine_cellar"}, 25),
    ("tourism", {"hotel", "motel"}, 6),
    ("tourism", {"museum", "gallery"}, 30),
    ("tourism", {"attraction", "zoo", "aquarium", "viewpoint"}, 31),
    ("tourism", {"information"}, 38),
    ("tourism", {"theme_park"}, 45),
    ("tourism", {"camp_site", "caravan_site"}, 67),
    ("leisure", {"marina"}, 14),
    ("leisure", {"park", "nature_reserve"}, 15),
    ("leisure", {"sports_centre", "stadium"}, 16),
    ("leisure", {"golf_course"}, 17),
    ("leisure", {"bowling_alley"}, 35),
    ("leisure", {"ice_rink"}, 46),
    ("historic", {"monument", "memorial"}, 33),
    ("aeroway", {"aerodrome"}, 26),
    ("railway", {"station"}, 28),
    ("highway", {"rest_area", "services"}, 44),
    ("place", {"city", "town", "village", "suburb", "hamlet", "locality"}, 18),
)
# The rules use the MY12 card's Category_IDs. Other cards number their categories
# differently (MY15/MY17 and MY10 each have their own order), so build() writes each POI
# under the card's ID for the same category name. Partition ID = category ID on every card.
CATEGORY_NAMES = {
    1: "home improvement store", 2: "auto service", 3: "place of worship", 4: "restaurant",
    5: "petrol station", 6: "hotel", 7: "shopping centre", 8: "grocery store", 9: "bank", 10: "atm",
    11: "library", 12: "school", 13: "parking", 14: "marina", 15: "park/recreation area",
    16: "sports complex", 17: "golf course", 18: "city centre", 19: "hospital", 20: "auto dealership",
    21: "city hall", 22: "nightclub", 23: "rental car", 24: "police station", 25: "winery", 26: "airport",
    27: "higher education", 28: "train station", 29: "coffee shop", 30: "museum", 31: "tourist attraction",
    32: "performing arts", 33: "historical monument", 35: "bowling centre", 36: "cinema",
    37: "bus station", 38: "tourist information", 39: "convention centre", 40: "court house",
    41: "ferry terminal", 42: "post office", 43: "casino", 44: "rest area", 45: "amusement park",
    46: "ice scating rink", 47: "pharmacy", 48: "medical service", 49: "embassy", 50: "cemetery",
    53: "consumer electronic store", 60: "public restroom", 61: "convenience store",
    62: "government office", 65: "department store", 66: "office supply store", 67: "campground",
    68: "book store", 69: "clothing store", 70: "sporting goods store",
    55: "camera",                     # speed cameras (cameras.py), not from RULES
}
CAMERA_CAT = 55


# road-sense alert categories (left empty by OSM builds, except "camera": cameras.py)
ROAD_SENSE = ("school zone", "camera", "blackspot", "railway", "check speed", "school point")


def card_categories(con):
    """{category name: Category_ID} from a POI database."""
    return {v: cid for cid, v in con.execute(
        "select c.Category_ID, s.Value from Category_BaseAttributes c "
        "join String_BaseAttributes s on s.String_ID = c.Name_String_ID")}


# categories that may be unnamed in OSM; they get this label instead
DEFAULT_NAME = {10: "ATM", 60: "PUBLIC TOILET", 13: "PARKING", 44: "REST AREA", 5: "PETROL STATION"}
# String_IDs 1..n belong to categories, icons and languages; POI names follow.
# n = 421 on MY12/MY15/MY17, 537 on MY10. factory_strings_max() reads it from the database.

_FACTORY_STRING_REFS = (
    "select Name_String_ID from Category_BaseAttributes union select Name_String_ID from Icon_BaseAttributes "
    "union select Path_String_ID from Icon_BaseAttributes union select Language_String_ID from Language_BaseAttributes "
    "union select String_ID from String_TranslationString_Relation "
    "union select Translation_String_ID from String_TranslationString_Relation")


def factory_strings_max(con):
    """Last String_ID used by categories, icons and languages; they must be exactly 1..n."""
    keep = {r[0] for r in con.execute(_FACTORY_STRING_REFS)} - {None}
    n = max(keep)
    if keep != set(range(1, n + 1)):
        raise ValueError("factory POI string layout changed (category/icon/language strings are not 1..n)")
    return n
MAXLEN = 69                        # longest factory POI name


def text(s, maxlen=MAXLEN):
    """Uppercase ASCII like the factory data."""
    if not s:
        return None
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").upper()
    s = re.sub(r"\s+", " ", s.replace("’", "'")).strip()
    return s[:maxlen] or None


def category(tags):
    for k, vals, cat in RULES:
        v = tags.get(k)
        if v is not None and (vals is None or v in vals):
            if k == "railway" and tags.get("station") in ("subway", "light_rail", "monorail"):
                return 28
            if k == "amenity" and v == "parking":
                # only real car parks, not every kerbside bay
                if tags.get("access") in ("private", "customers", "no") or not (
                        tags.get("name") or tags.get("parking") in ("multi-storey", "underground")):
                    return None
            if k == "amenity" and v == "toilets" and tags.get("access") in ("private", "customers", "no"):
                return None
            if k == "leisure" and v == "park" and not tags.get("name"):
                return None
            if k == "tourism" and v == "information" and tags.get("information") not in ("office", "visitor_centre"):
                return None   # boards, guideposts and maps, not visitor centres
            if k == "aeroway" and (tags.get("aerodrome:type") == "private" or tags.get("access") == "private"):
                return None
            return cat
    return None


def morton(lon_e5, lat_e5):
    x = int((lon_e5 / 1e5 + 180.0) * 4294967296.0 / 360.0) & 0xFFFFFFFF
    y = int((lat_e5 / 1e5 + 180.0) * 4294967296.0 / 360.0) & 0xFFFFFFFF
    v = 0
    for b in range(32):
        v |= ((x >> b) & 1) << (2 * b) | ((y >> b) & 1) << (2 * b + 1)
    return v


def unmorton(v):
    x = y = 0
    for b in range(32):
        x |= ((v >> (2 * b)) & 1) << b
        y |= ((v >> (2 * b + 1)) & 1) << b
    return (round((x * 360.0 / 4294967296.0 - 180.0) * 1e5), round((y * 360.0 / 4294967296.0 - 180.0) * 1e5))


def phone(raw, cc="61"):
    """'+61 3 9670 4506' -> ('61396704506', '+(61)-3-96704506'); cc = dialling code
    (New Zealand '+64 9 379 0000' -> ('6493790000', '+(64)-9-3790000'))."""
    if not raw:
        return None, None
    d = re.sub(r"\D", "", raw.split(";")[0])
    if d.startswith(cc):
        nsn = d[len(cc):]
    elif d.startswith("0"):
        nsn = d[1:]
    elif cc == "61" and len(d) in (6, 10) and d.startswith("1"):     # 13 xx xx, 1300 / 1800
        nsn = d
    else:
        return None, None
    if len(nsn) < 6:
        return None, None
    nsn = nsn.lstrip("0")
    short = cc == "61" and nsn.startswith("1")
    pretty = f"+({cc})-{nsn}" if short else f"+({cc})-{nsn[0]}-{nsn[1:]}"
    return cc + nsn, pretty


def read(pbf_path, bbox=None, node_index="flex_mem"):
    """[(category, lon_e5, lat_e5, tags)] for every OSM node / area that maps to a category."""
    import osmium
    import osmium.filter
    out = []
    fp = (osmium.FileProcessor(pbf_path, osmium.osm.NODE | osmium.osm.WAY | osmium.osm.RELATION | osmium.osm.AREA)
          .with_locations(node_index)
          .with_areas(osmium.filter.KeyFilter(*KEYS))
          .with_filter(osmium.filter.EntityFilter(osmium.osm.NODE | osmium.osm.AREA))
          .with_filter(osmium.filter.KeyFilter(*KEYS)))
    for o in fp:
        tags = {t.k: t.v for t in o.tags}
        cat = category(tags)
        if cat is None:
            continue
        if o.is_node():
            if not o.location.valid():
                continue
            x, y = round(o.location.lon * 1e5), round(o.location.lat * 1e5)
        else:
            pts = [(n.lon, n.lat) for ring in o.outer_rings() for n in ring]
            if not pts:
                continue
            x = round(sum(p[0] for p in pts) / len(pts) * 1e5)
            y = round(sum(p[1] for p in pts) / len(pts) * 1e5)
        if bbox and not (bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]):
            continue
        keep = {k: tags[k] for k in ("name", "brand", "operator", "addr:housenumber", "addr:street",
                                    "addr:suburb", "addr:city", "phone", "contact:phone") if k in tags}
        out.append((cat, x, y, keep))
    return out


CAMERA_NAME = "Camera"           # factory POI name of every camera


def build(pois, factory_db, out_path, areas=None, bbox=None, progress=None, places=(), cameras=(), today=None):
    """Write out_path: the factory POI database with its POIs replaced by `pois`.
    areas / places: osm_names.read_boundaries() / read_places() output, for the city /
    state of each POI. A POI's country is the catalog in tags[COUNTRY_TAG] (default 0).
    bbox: map extent (deg * 1e5); POIs outside it (e.g. Heard Island) are left out.
    cameras: cameras.place() output, written to the card's camera category."""
    from . import countries, osm_names
    stats = collections.Counter()
    loc_idx = st_idx = None
    if areas:
        loc_idx = osm_names.Localities(areas, places)
        st_idx = osm_names.AreaIndex([a for a in areas if a.level == 4], cell=50000)

    # names, de-duplication (same category and name within ~100 m: node + area of one place)
    rows, seen = [], set()
    for cat, x, y, tags in pois:
        if bbox and not (bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]):
            stats["skipped: outside the map"] += 1
            continue
        name = text(tags.get("name")) or text(tags.get("brand")) or DEFAULT_NAME.get(cat)
        if cat == 5 and tags.get("brand") and not tags.get("name"):
            name = text(tags["brand"])
        if not name:
            stats["skipped: no name"] += 1
            continue
        key = (cat, name, x // 100, y // 100)
        if key in seen:
            stats["skipped: duplicate"] += 1
            continue
        seen.add(key)
        rows.append((morton(x, y), cat, x, y, name, tags))
    if cameras:
        from . import countries as _c
        for cam in cameras:
            x, y = cam["x"], cam["y"]
            if bbox and not (bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]):
                stats["skipped: camera outside the map"] += 1
                continue
            tags = {"addr:street": cam.get("road"), "_camera": cam,
                    COUNTRY_TAG: _c.NZ.catalog if cam.get("state") == "NZ" else _c.AU.catalog}
            rows.append((morton(x, y), CAMERA_CAT, x, y, CAMERA_NAME, tags))
    stamp = (today or datetime.date.today()).strftime("%d-%b-%y").upper()
    rows.sort(key=lambda r: r[:5])

    tmp = out_path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    shutil.copyfile(factory_db, tmp)
    con = sqlite3.connect(tmp)
    con.execute("PRAGMA journal_mode=DELETE")
    cur = con.cursor()
    n_fixed = factory_strings_max(con)
    on_card = card_categories(con)
    card_cat = {c: on_card.get(n) for c, n in CATEGORY_NAMES.items()}
    # MY10 has no Name/PhoneNumber columns in Poi_BaseAttributes (the name is in the strings)
    base_cols = len(con.execute("pragma table_info(Poi_BaseAttributes)").fetchall())
    for t in ("Poi_Relation", "Poi_VSegAttributes", "Poi_AlertAttributes", "Poi_AddressAttributes", "Poi_BaseAttributes"):
        cur.execute(f"delete from {t}")
    cur.execute(f"delete from String_BaseAttributes where String_ID > {n_fixed}")

    strings = {}

    def sid(s):
        if not s:
            return None
        if s not in strings:
            strings[s] = n_fixed + 1 + len(strings)
        return strings[s]

    city_cache = {}
    base, addr, alert = [], [], []
    missing = [r for r in rows if card_cat.get(r[1]) is None]
    if missing:
        stats["skipped: category not on card"] += len(missing)
        rows = [r for r in rows if card_cat.get(r[1]) is not None]
    for pid, (code, cat, x, y, name, tags) in enumerate(rows, start=1):
        country = countries.BY_CATALOG.get(tags.get(COUNTRY_TAG, 0), countries.AU)
        city = text(tags.get("addr:suburb") or tags.get("addr:city"))
        region = None
        if loc_idx is not None:
            k = (country.catalog, x // 200, y // 200)
            if k not in city_cache:
                li, si_ = loc_idx.find(x, y, country.catalog), st_idx.find(x, y)
                city_cache[k] = (text(loc_idx.loc[li].name) if li is not None else None,
                                 text(st_idx.areas[si_].name) if si_ is not None else None)
            c2, region = city_cache[k]
            city = city or c2
        if cat == 18:
            city = name
        p_raw, p_pretty = phone(tags.get("phone") or tags.get("contact:phone"), country.phone_cc)
        base.append((pid, card_cat[cat], code, sid(name), name, p_raw)[:base_cols])
        addr.append((pid, sid(text(tags.get("addr:housenumber"), 20)), sid(text(tags.get("addr:street"))), None,
                     sid(city), sid(region), sid(country.name), None, sid(p_pretty)))
        cam = tags.get("_camera")
        if cam is not None:
            alert.append((pid, cam.get("speed"), cam.get("heading"), stamp, cam["type"], 1, None))
            stats["cameras"] += 1
        else:
            alert.append((pid, None, None, None, None, None, None))
        stats[f"category {cat}"] += 1
    cur.executemany("insert into String_BaseAttributes (String_ID, Value) values (?, ?)",
                    [(i, s) for s, i in strings.items()])
    cur.executemany(f"insert into Poi_BaseAttributes values ({', '.join('?' * base_cols)})", base)
    cur.executemany("insert into Poi_AddressAttributes values (?, ?, ?, ?, ?, ?, ?, ?, ?)", addr)
    cur.executemany("insert into Poi_AlertAttributes values (?, ?, ?, ?, ?, ?, ?)", alert)
    cur.execute("update sqlite_sequence set seq = ? where name = 'Poi_BaseAttributes'", (len(base),))
    cur.execute("update sqlite_sequence set seq = ? where name = 'String_BaseAttributes'",
                (n_fixed + len(strings),))
    con.commit()
    if progress:
        progress("POI rows written, compacting")
    con.execute("VACUUM")
    ok = con.execute("PRAGMA integrity_check").fetchone()[0]
    con.close()
    if ok != "ok":
        raise RuntimeError(f"POI database integrity check failed: {ok}")
    os.replace(tmp, out_path)
    stats.update({"pois": len(base), "strings": len(strings)})
    return stats


def compare_schema(factory_db, new_db):
    """Differences in everything the builder must keep: schema, statements, categories."""
    def dump(p):
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        d = {"schema": con.execute("select type, name, tbl_name, sql from sqlite_master order by name").fetchall()}
        for t in ("PredefinedStatement", "DatabaseAttribute", "Category_BaseAttributes", "Category_ParentCategory_Relation",
                  "Icon_BaseAttributes", "PoiPartition_BaseAttributes", "PoiPartition_Category_Relation",
                  "Language_BaseAttributes", "String_TranslationString_Relation"):
            d[t] = con.execute(f"select * from {t} order by 1").fetchall()
        d["strings"] = con.execute("select * from String_BaseAttributes where String_ID <= ? order by 1",
                                   (factory_strings_max(con),)).fetchall()
        d["page_size"] = con.execute("pragma page_size").fetchall()
        d["encoding"] = con.execute("pragma encoding").fetchall()
        con.close()
        return d
    a, b = dump(factory_db), dump(new_db)
    return [k for k in a if a[k] != b[k]]
