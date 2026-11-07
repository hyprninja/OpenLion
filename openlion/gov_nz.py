"""New Zealand addresses and suburbs from LINZ (Toitū Te Whenua Land Information New
Zealand), report 27.

Both come from LINZ's own ArcGIS Online feature services (owner administrator_LINZ,
CC BY 4.0, updated weekly), which answer plain queries without a key:

  LINZ_NZ_Addresses                 2.4 million address points: address_number,
                                    address_number_high, road_id, lifecycle
  LINZ_NZ_Addresses_Roads           road_id -> full_road_name ("Main Road")
  LINZ_NZ_Suburbs_and_Localities    6,563 polygons: name, type, major_name (the town or
                                    city a suburb belongs to: Te Rapa -> Hamilton)

fetch() pages through them (OBJECTID ranges, 10,000 rows a request, a few at a time)
into <folder>/linz/, and keeps the last good copy when a download fails. A copy younger
than MAX_AGE_DAYS isn't downloaded again: the addresses take a few minutes.

read_addresses() returns an osm_addr.Addresses, merged with OSM's like G-NAF is (OSM
first). read_suburbs() returns osm_names.Area boundaries (level 9) for NZ localities.
"""
import collections
import concurrent.futures
import datetime
import gzip
import json
import os
import urllib.parse
import urllib.request

UA = "OpenLion (https://github.com/hyprninja/OpenLion)"
TIMEOUT = 120
BASE = "https://services.arcgis.com/xdsHIIxuCWByZiCB/ArcGIS/rest/services"
ADDRESSES = BASE + "/LINZ_NZ_Addresses/FeatureServer/0"
ROADS = BASE + "/LINZ_NZ_Addresses_Roads/FeatureServer/0"
SUBURBS = BASE + "/LINZ_NZ_Suburbs_and_Localities/FeatureServer/0"
PROVIDER = "Toitū Te Whenua Land Information New Zealand (LINZ), CC BY 4.0"
ATTRIBUTION = "Sourced from the LINZ Data Service and licensed for reuse under CC BY 4.0"
MAX_AGE_DAYS = 30
PAGE = 10000                 # 2,000 a request x maxRecordCountFactor 5
WORKERS = 4
# land types; lakes, coastal and inland bays are water
SUBURB_TYPES = {"Suburb", "Locality", "Island", "Conservation Land"}
TOWN_TYPES = {"City", "Town"}   # LINZ major_name_type a suburb is listed under
SIMPLIFY = 0.0001            # degrees (~10 m): plenty for which-suburb-is-this-street


def _get(url, params, tries=4):
    q = url + "/query?" + urllib.parse.urlencode({**params, "f": "json"})
    for k in range(tries):
        try:
            req = urllib.request.Request(q, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                d = json.loads(r.read().decode("utf-8"))
            if "error" in d:
                raise RuntimeError(d["error"].get("message") or str(d["error"]))
            return d
        except Exception:
            if k == tries - 1:
                raise


def _id_range(url):
    d = _get(url, {"where": "1=1", "outStatistics": json.dumps(
        [{"statisticType": "min", "onStatisticField": "OBJECTID", "outStatisticFieldName": "lo"},
         {"statisticType": "max", "onStatisticField": "OBJECTID", "outStatisticFieldName": "hi"}])})
    a = d["features"][0]["attributes"]
    return int(a["lo"]), int(a["hi"])


def _all(url, fields, geometry=True, extra=None, where="1=1"):
    """Every feature of a layer, in OBJECTID chunks of PAGE fetched WORKERS at a time."""
    lo, hi = _id_range(url)
    chunks = [(a, min(a + PAGE, hi + 1)) for a in range(lo, hi + 1, PAGE)]

    def one(ch):
        return _get(url, {"where": f"({where}) AND OBJECTID >= {ch[0]} AND OBJECTID < {ch[1]}",
                          "outFields": ",".join(fields), "returnGeometry": "true" if geometry else "false",
                          "outSR": "4326", "geometryPrecision": "5", "resultRecordCount": str(PAGE),
                          "maxRecordCountFactor": "5", **(extra or {})})

    out = []
    with concurrent.futures.ThreadPoolExecutor(WORKERS) as ex:
        for d in ex.map(one, chunks):
            if d.get("exceededTransferLimit"):
                raise RuntimeError("a page came back cut short")
            out += d["features"]
    return out


def folder_of(gov_dir):
    return os.path.join(gov_dir, "linz")


def addresses_path(gov_dir):
    return os.path.join(folder_of(gov_dir), "addresses.csv.gz")


def suburbs_path(gov_dir):
    return os.path.join(folder_of(gov_dir), "suburbs.json.gz")


def _meta_path(path):
    return path + ".json"


def meta(path):
    try:
        with open(_meta_path(path), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _fresh(path, today, max_age):
    m = meta(path)
    if not m or not os.path.isfile(path):
        return False
    try:
        age = (today - datetime.date.fromisoformat(m["fetched"])).days
    except (KeyError, ValueError):
        return False
    return 0 <= age < max_age


def _write(path, rows_or_doc, info, csv_header=None):
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8", newline="") as f:
        if csv_header:
            f.write(csv_header + "\n")
            for r in rows_or_doc:
                f.write(r + "\n")
        else:
            json.dump(rows_or_doc, f, separators=(",", ":"))
    os.replace(tmp, path)
    with open(_meta_path(path), "w", encoding="utf-8") as f:
        json.dump(info, f, indent=1)


def fetch_addresses(gov_dir, today):
    roads = {}
    for f in _all(ROADS, ["road_id", "full_road_name"], geometry=False):
        a = f["attributes"]
        if a.get("full_road_name"):
            roads[a["road_id"]] = a["full_road_name"].replace(",", " ")
    rows = []
    for f in _all(ADDRESSES, ["address_number", "address_number_high", "road_id"],
                  where="address_lifecycle = 'Current'"):
        a, g = f["attributes"], f.get("geometry")
        name = roads.get(a.get("road_id"))
        if not g or not name or not a.get("address_number"):
            continue
        rows.append(f"{g['x']:.5f},{g['y']:.5f},{a['address_number']},{a.get('address_number_high') or ''},"
                    f"{a['road_id']},{name}")
    if len(rows) < 100000:
        raise RuntimeError(f"only {len(rows):,} addresses in the download")
    _write(addresses_path(gov_dir), rows, {"fetched": today.isoformat(), "provider": PROVIDER,
                                           "url": ADDRESSES, "addresses": len(rows), "roads": len(roads)},
           csv_header="lon,lat,number,high,road_id,road")
    return len(rows)


def fetch_suburbs(gov_dir, today):
    out = []
    for f in _all(SUBURBS, ["id", "name", "type", "major_name", "major_name_type"],
                  extra={"maxAllowableOffset": str(SIMPLIFY)}):
        a, g = f["attributes"], f.get("geometry")
        if not g or not a.get("name") or a.get("type") not in SUBURB_TYPES:
            continue
        rings = [[(round(x * 1e5), round(y * 1e5)) for x, y in r] for r in g.get("rings", []) if len(r) >= 4]
        if rings:
            out.append({"id": a["id"], "name": a["name"], "type": a["type"], "major": a.get("major_name"),
                        "major_type": a.get("major_name_type"), "rings": rings})
    if len(out) < 1000:
        raise RuntimeError(f"only {len(out):,} suburbs in the download")
    _write(suburbs_path(gov_dir), out, {"fetched": today.isoformat(), "provider": PROVIDER, "url": SUBURBS,
                                        "suburbs": len(out)})
    return len(out)


def fetch(gov_dir, log=print, today=None, max_age=MAX_AGE_DAYS, what=("addresses", "suburbs")):
    """Download what's missing or older than max_age days into gov_dir/linz. A failed
    download keeps the last good copy. Returns {name: {"ok", "error", "fetched", "count"}}."""
    os.makedirs(folder_of(gov_dir), exist_ok=True)
    today = today or datetime.date.today()
    res = {}
    for name, path, fn in (("addresses", addresses_path(gov_dir), fetch_addresses),
                           ("suburbs", suburbs_path(gov_dir), fetch_suburbs)):
        if name not in what:
            continue
        if _fresh(path, today, max_age):
            m = meta(path)
            res[name] = {"ok": True, "error": None, "fetched": m["fetched"], "count": m.get(name)}
            log(f"LINZ {name}: using the copy from {m['fetched']}")
            continue
        try:
            n = fn(gov_dir, today)
            res[name] = {"ok": True, "error": None, "fetched": today.isoformat(), "count": n}
            log(f"LINZ {name}: {n:,} downloaded")
        except Exception as e:  # network, service change: keep the last copy if there is one
            m = meta(path) if os.path.isfile(path) else None
            res[name] = {"ok": False, "error": f"{type(e).__name__}: {e}",
                         "fetched": m["fetched"] if m else None, "count": m.get(name) if m else None}
            log(f"LINZ {name}: download failed ({type(e).__name__}: {e}); "
                + (f"using the copy from {m['fetched']}" if m else "using OpenStreetMap only"))
    return res


# ---- readers -------------------------------------------------------------------

def read_addresses(path, bbox=None, stats=None):
    """LINZ addresses -> osm_addr.Addresses, one point per (road, number): units and flats
    at one number become their mean, as with G-NAF."""
    from .osm_addr import Addresses, MAX_NUMBER
    from .osm_names import normalise
    stats = stats if stats is not None else collections.Counter()
    acc, names = {}, {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        f.readline()
        for line in f:
            p = line.rstrip("\n").split(",", 5)
            if len(p) < 6:
                continue
            try:
                x, y, n = round(float(p[0]) * 1e5), round(float(p[1]) * 1e5), int(p[2])
                hi = int(p[3]) if p[3] else 0
            except ValueError:
                continue
            if not 0 < n <= MAX_NUMBER:
                stats["linz: no number"] += 1
                continue
            if bbox and not (bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]):
                continue
            rid = p[4]
            if rid not in names:
                names[rid] = normalise(p[5])
            k = (rid, n)
            a = acc.get(k)
            last = hi if n < hi <= min(n + 200, MAX_NUMBER) else 0
            if a is None:
                acc[k] = [last, x, y, 1]
            else:
                a[0] = max(a[0], last)
                a[1] += x
                a[2] += y
                a[3] += 1
    out = Addresses()
    for (rid, n), (last, sx, sy, c) in sorted(acc.items()):
        if names[rid]:
            out.add(names[rid], round(sx / c), round(sy / c), [n, last] if last else [n])
    stats["linz addresses"] = len(acc)
    return out


def read_suburbs(path, catalog, bbox=None):
    """LINZ suburbs and localities -> [osm_names.Area] at level 9. Each ring is passed as
    an outer ring: Area.contains() counts crossings over all rings (even-odd), so holes
    work without telling them apart. Area.town = the town or city a suburb is part of
    (None where that's the suburb itself, or where LINZ's parent is a "Major Locality"
    such as Marlborough Sounds rather than a town or city: the factory lists rural
    localities on their own)."""
    from .osm_names import Area, normalise
    with gzip.open(path, "rt", encoding="utf-8") as f:
        doc = json.load(f)
    out = []
    for s in doc:
        rings = [([tuple(p) for p in r], []) for r in s["rings"]]
        a = Area(9, s["name"], None, rings, f"linz:{s['id']}", catalog)
        b = a.bbox
        if bbox and (b[2] < bbox[0] or b[0] > bbox[2] or b[3] < bbox[1] or b[1] > bbox[3]):
            continue
        major = s.get("major")
        if s.get("major_type", "Town") not in TOWN_TYPES:
            major = None
        a.town = major if major and normalise(major) != normalise(s["name"]) else None
        out.append(a)
    return out
