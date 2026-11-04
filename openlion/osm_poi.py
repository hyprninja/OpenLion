"""OSM amenities -> the unit's POI database (edb/POI/aunz_sql.db3).

The POI database is plain SQLite (report 17). The unit queries it through compiled
statements stored in the database itself (PredefinedStatement), so the builder starts
from a copy of the factory file and keeps its schema, categories, icons, partitions,
languages, statements and database attributes exactly. Only the POI rows and their
strings are replaced:

  Poi_BaseAttributes     Poi_ID, PoiPartition_ID (= category), Coordinate, Name_String_ID,
                         Name, PhoneNumber ("61396704506")
  Poi_AddressAttributes  house number, street, city, region (state), country and phone
                         ("+(61)-3-96704506") as String_BaseAttributes IDs
  Poi_AlertAttributes    one row per POI, all NULL except for the road-sense alerts
  String_BaseAttributes  IDs 1..421 are category/icon/language strings and are kept

Coordinate = Morton code: bit 2k is bit k of x, bit 2k+1 is bit k of y, with
x = (lon + 180) * 2^32 / 360 and y = (lat + 180) * 2^32 / 360.

The road-sense categories (school zone, camera, blackspot, railway crossing, check
speed, school point) are left empty: their direction and camera fields aren't
decoded well enough to warn correctly. The factory link/entry points (categories 82,
83) and POI relations aren't rebuilt either.
"""
import collections
import os
import re
import shutil
import sqlite3
import unicodedata

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
# categories that may be unnamed in OSM; they get this label instead
DEFAULT_NAME = {10: "ATM", 60: "PUBLIC TOILET", 13: "PARKING", 44: "REST AREA", 5: "PETROL STATION"}
FACTORY_STRINGS_MAX = 421          # String_IDs 1..421 belong to categories, icons, languages
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


def phone(raw):
    """'+61 3 9670 4506' -> ('61396704506', '+(61)-3-96704506')."""
    if not raw:
        return None, None
    d = re.sub(r"\D", "", raw.split(";")[0])
    if d.startswith("61"):
        nsn = d[2:]
    elif d.startswith("0"):
        nsn = d[1:]
    elif len(d) in (6, 10) and d.startswith("1"):
        nsn = d
    else:
        return None, None
    if len(nsn) < 6:
        return None, None
    nsn = nsn.lstrip("0")
    pretty = f"+(61)-{nsn}" if nsn.startswith("1") else f"+(61)-{nsn[0]}-{nsn[1:]}"
    return "61" + nsn, pretty


def read(pbf_path, bbox=None):
    """[(category, lon_e5, lat_e5, tags)] for every OSM node / area that maps to a category."""
    import osmium
    import osmium.filter
    out = []
    fp = (osmium.FileProcessor(pbf_path, osmium.osm.NODE | osmium.osm.WAY | osmium.osm.RELATION | osmium.osm.AREA)
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


def build(pois, factory_db, out_path, areas=None, bbox=None, progress=None):
    """Write out_path: the factory POI database with its POIs replaced by `pois`.
    areas: osm_names.read_boundaries() output, for the city / state of each POI.
    bbox: map extent (deg * 1e5); POIs outside it (e.g. Heard Island) are left out."""
    from . import osm_names
    stats = collections.Counter()
    loc_idx = st_idx = None
    if areas:
        locs = [a for a in areas if a.level == 9]
        states = [a for a in areas if a.level == 4]
        loc_idx, st_idx = osm_names.AreaIndex(locs), osm_names.AreaIndex(states, cell=50000)

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
    rows.sort()

    tmp = out_path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    shutil.copyfile(factory_db, tmp)
    con = sqlite3.connect(tmp)
    con.execute("PRAGMA journal_mode=DELETE")
    cur = con.cursor()
    keep = {r[0] for r in cur.execute(
        "select Name_String_ID from Category_BaseAttributes union select Name_String_ID from Icon_BaseAttributes "
        "union select Path_String_ID from Icon_BaseAttributes union select Language_String_ID from Language_BaseAttributes "
        "union select String_ID from String_TranslationString_Relation "
        "union select Translation_String_ID from String_TranslationString_Relation")}
    assert max(keep) <= FACTORY_STRINGS_MAX, "factory string layout changed"
    for t in ("Poi_Relation", "Poi_VSegAttributes", "Poi_AlertAttributes", "Poi_AddressAttributes", "Poi_BaseAttributes"):
        cur.execute(f"delete from {t}")
    cur.execute(f"delete from String_BaseAttributes where String_ID > {FACTORY_STRINGS_MAX}")

    strings = {}

    def sid(s):
        if not s:
            return None
        if s not in strings:
            strings[s] = FACTORY_STRINGS_MAX + 1 + len(strings)
        return strings[s]

    city_cache = {}
    base, addr, alert = [], [], []
    for pid, (code, cat, x, y, name, tags) in enumerate(rows, start=1):
        city = text(tags.get("addr:suburb") or tags.get("addr:city"))
        region = None
        if loc_idx is not None:
            k = (x // 200, y // 200)
            if k not in city_cache:
                li, si_ = loc_idx.find(x, y), st_idx.find(x, y)
                city_cache[k] = (text(loc_idx.areas[li].name) if li is not None else None,
                                 text(st_idx.areas[si_].name) if si_ is not None else None)
            c2, region = city_cache[k]
            city = city or c2
        if cat == 18:
            city = name
        p_raw, p_pretty = phone(tags.get("phone") or tags.get("contact:phone"))
        base.append((pid, cat, code, sid(name), name, p_raw))
        addr.append((pid, sid(text(tags.get("addr:housenumber"), 20)), sid(text(tags.get("addr:street"))), None,
                     sid(city), sid(region), sid("AUSTRALIA"), None, sid(p_pretty)))
        alert.append((pid, None, None, None, None, None, None))
        stats[f"category {cat}"] += 1
    cur.executemany("insert into String_BaseAttributes (String_ID, Value) values (?, ?)",
                    [(i, s) for s, i in strings.items()])
    cur.executemany("insert into Poi_BaseAttributes values (?, ?, ?, ?, ?, ?)", base)
    cur.executemany("insert into Poi_AddressAttributes values (?, ?, ?, ?, ?, ?, ?, ?, ?)", addr)
    cur.executemany("insert into Poi_AlertAttributes values (?, ?, ?, ?, ?, ?, ?)", alert)
    cur.execute("update sqlite_sequence set seq = ? where name = 'Poi_BaseAttributes'", (len(base),))
    cur.execute("update sqlite_sequence set seq = ? where name = 'String_BaseAttributes'",
                (FACTORY_STRINGS_MAX + len(strings),))
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
        d["strings"] = con.execute(f"select * from String_BaseAttributes where String_ID <= {FACTORY_STRINGS_MAX} "
                                   "order by 1").fetchall()
        d["page_size"] = con.execute("pragma page_size").fetchall()
        d["encoding"] = con.execute("pragma encoding").fetchall()
        con.close()
        return d
    a, b = dump(factory_db), dump(new_db)
    return [k for k in a if a[k] != b[k]]
