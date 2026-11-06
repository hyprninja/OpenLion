"""Fixed speed and red-light camera lists from governments that publish them openly.

Only sources a plain download can reach, with coordinates, kept current, under an open
licence. Each one becomes <dir>/<STATE>.json in one common form (see normalise()), so a
build can use the last good copy when a download fails. States and countries without a
source here get OpenStreetMap's cameras instead (cameras.py).

  VIC  vic.gov.au "Find a road safety camera" (Cameras Save Lives, CC BY 4.0): every fixed
       digital camera with coordinates; the camera ID ends in its direction (H138N =
       northbound) on freeways, highways and point-to-point sites.
  ACT  data.act.gov.au "Traffic speed camera locations" (Access Canberra, CC BY 4.0):
       fixed, red-light-and-speed and point-to-point cameras; decommissioned ones are dated.

Looked at and left out (2026-11), so they get OSM instead:
  NSW  live map blocks plain downloads; the open-data copy dates from 2021-2023
  QLD  and SA publish road and suburb names only, no coordinates; SA blocks plain downloads
  WA, TAS, NT publish no list.  NZ (NZTA) is behind a CAPTCHA.
"""
import datetime
import json
import os
import re
import urllib.request

UA = "OpenLion (https://github.com/hyprninja/OpenLion)"
TIMEOUT = 60

# kind: "speed" (fixed speed), "redlight_speed" (intersection: red light and speed),
# "redlight" (red light only), "p2p" (point-to-point / average speed site)
KINDS = ("speed", "redlight_speed", "redlight", "p2p")

SOURCES = {
    "VIC": {
        "name": "Victoria",
        "provider": "Victorian Government (Cameras Save Lives), CC BY 4.0",
        "url": "https://www.vic.gov.au/api/tide/elasticsearch/"
               "content-vic__production__sdp_data_pipelines_scl/_search",
    },
    "ACT": {
        "name": "Australian Capital Territory",
        "provider": "ACT Government (Access Canberra), CC BY 4.0",
        "url": "https://www.data.act.gov.au/resource/426s-vdu4.json?$limit=50000",
    },
}

BOUND = {"north": 0, "east": 90, "south": 180, "west": 270}
LETTER = {"N": 0, "E": 90, "S": 180, "W": 270}


def bound_heading(text):
    """'Hume Freeway, Northbound, ...' / 'TUGGERANONG PARKWAY ..., SOUTHBOUND' -> 0 / 180."""
    m = re.search(r"\b(north|south|east|west)[ -]?bound\b", text or "", re.I)
    return BOUND[m.group(1).lower()] if m else None


def vic_id_heading(camera_id):
    """Victorian camera IDs end in their direction: H138N, M152W, W33WB (westbound), GV55N.
    Intersection cameras (F07, A00) have no letter after the number."""
    m = re.search(r"\d+([A-Z]+)$", camera_id or "")
    return LETTER.get(m.group(1)[0]) if m else None


def _http(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Accept": "application/json",
                                                          **({"Content-Type": "application/json"} if data else {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def parse_vic(doc):
    out = []
    for h in doc["hits"]["hits"]:
        s = h["_source"]
        try:
            lat, lon = float(s["latitude"]), float(s["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        site = (s.get("site_type") or "").lower()
        kind = "redlight_speed" if site == "intersection" else "p2p" if "point" in site else "speed"
        head = bound_heading(s.get("offence_location")) if kind != "redlight_speed" else None
        if head is None and kind != "redlight_speed":
            head = vic_id_heading(s.get("camera_id"))
        out.append({"id": s.get("camera_id"), "lat": lat, "lon": lon, "kind": kind, "heading": head,
                    "road": s.get("street") or None, "where": s.get("offence_location")})
    return out


def _act_road(desc):
    """'BARTON HIGHWAY BETWEEN CURRAN DRIVE AND ...' / 'NORTHBOURNE AVENUE/LONDON CIRCUIT' -> road."""
    d = re.split(r"/| BETWEEN | NEAR | (?:NORTH|SOUTH|EAST|WEST)BOUND", desc or "")[0]
    return d.strip().title() or None


def parse_act(rows):
    out = []
    for r in rows:
        t = (r.get("camera_type") or "").upper()
        if not t or "MOBILE" in t or r.get("decommissioned_camera_date"):
            continue
        try:
            lat, lon = float(r["latitude"]), float(r["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        kind = "redlight_speed" if "RED LIGHT" in t else "p2p" if "POINT" in t else "speed"
        desc = r.get("location_description") or ""
        out.append({"id": r.get("camera_location_code") or r.get("location_code"), "lat": lat, "lon": lon,
                    "kind": kind, "heading": bound_heading(desc), "road": _act_road(desc), "where": desc})
    # the list repeats a site without its code when a camera there was replaced
    seen, uniq = set(), []
    for c in out:
        k = (round(c["lat"], 4), round(c["lon"], 4), c["kind"], c["heading"])
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    return uniq


def fetch_one(code):
    src = SOURCES[code]
    if code == "VIC":
        return parse_vic(_http(src["url"], {"size": 10000, "query": {"match_all": {}}}))
    if code == "ACT":
        return parse_act(_http(src["url"]))
    raise ValueError(code)


def path_of(folder, code):
    return os.path.join(folder, f"{code}.json")


def fetch(folder, log=print, today=None):
    """Download every source into folder. A failed or empty download keeps the last good
    copy. Returns {code: {"ok": bool, "error": str|None}}."""
    os.makedirs(folder, exist_ok=True)
    today = today or datetime.date.today().isoformat()
    res = {}
    for code, src in SOURCES.items():
        try:
            cams = fetch_one(code)
            if len(cams) < 5:
                raise ValueError(f"only {len(cams)} cameras in the download")
            doc = {"state": code, "name": src["name"], "provider": src["provider"], "url": src["url"],
                   "fetched": today, "cameras": cams}
            tmp = path_of(folder, code) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=0)
            os.replace(tmp, path_of(folder, code))
            res[code] = {"ok": True, "error": None}
            log(f"speed cameras: {code} {len(cams)} from {src['provider']}")
        except Exception as e:  # network, site change: fall back to the last copy (or OSM)
            res[code] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            log(f"speed cameras: {code} download failed ({type(e).__name__}: {e}); "
                + ("using the copy from " + load(path_of(folder, code))["fetched"]
                   if os.path.isfile(path_of(folder, code)) else "using OpenStreetMap"))
    return res


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def available(folder):
    """[path] of every state file in folder (what a build should use)."""
    if not folder or not os.path.isdir(folder):
        return []
    return [path_of(folder, c) for c in SOURCES if os.path.isfile(path_of(folder, c))]
