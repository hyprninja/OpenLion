"""House numbers from G-NAF, Australia's national address file, for streets OSM has
no numbers on (report 23).

Source: Geoscape Geocoded National Address File (G-NAF), quarterly, data.gov.au, under
the Open G-NAF End User Licence Agreement (CC BY 4.0 with one added rule: not for
compiling addresses to send mail to). The zip (~1.9 GB, ~9 GB unpacked) is read in
place, state by state:

  <STATE>_STREET_LOCALITY      street name, full street type (STREET), suffix code (N)
  <STATE>_ADDRESS_DETAIL       NUMBER_FIRST / NUMBER_LAST, STREET_LOCALITY_PID,
                               ALIAS_PRINCIPAL (P / A), DATE_RETIRED
  <STATE>_ADDRESS_DEFAULT_GEOCODE  the address's point (GDA2020 lon / lat)

Only current principal addresses with a street number. Units and flats at one street
number become one point (their mean). Geocodes that only mark the street or locality
(STL, LOC) are left out: they don't say where along the street a number is.

read() returns an osm_addr.Addresses. combine() adds them to OSM's: OSM wins, so a
G-NAF number is dropped where OSM has the same number on the same street nearby.
"""
import collections
import io
import re
import zipfile

from .osm_addr import Addresses, MAX_NUMBER

ATTRIBUTION = ("Incorporates or developed using G-NAF © Geoscape Australia licensed by the "
               "Commonwealth of Australia under the Open Geo-coded National Address File (G-NAF) "
               "End User Licence Agreement")
STATES = ("ACT", "NSW", "NT", "OT", "QLD", "SA", "TAS", "VIC", "WA")
SKIP_GEOCODES = {"STL", "LOC", "UNK"}
SAME_RADIUS = 300            # deg * 1e5 (~330 m): OSM's copy of a number counts within this


def _members(z):
    """{(state, table): zip member} for the tables used, any release folder name."""
    out = {}
    pat = re.compile(r"/Standard/(" + "|".join(STATES) + r")_(STREET_LOCALITY|ADDRESS_DETAIL|"
                     r"ADDRESS_DEFAULT_GEOCODE)_psv\.psv$")
    for name in z.namelist():
        m = pat.search(name)
        if m:
            out[(m.group(1), m.group(2))] = name
        elif name.endswith("Authority_Code_STREET_SUFFIX_AUT_psv.psv"):
            out[("", "SUFFIX")] = name
    return out


def _rows(z, name):
    """Rows of a pipe-separated table as dicts of the wanted columns."""
    with z.open(name) as f:
        t = io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline="")
        cols = t.readline().rstrip("\r\n").split("|")
        idx = {c: i for i, c in enumerate(cols)}
        for line in t:
            yield idx, line.rstrip("\r\n").split("|")


def _int(s):
    try:
        return int(s)
    except ValueError:
        return None


def read(zip_path, bbox=None, states=STATES, stats=None):
    from .osm_names import normalise
    stats = stats if stats is not None else collections.Counter()
    out = Addresses()
    with zipfile.ZipFile(zip_path) as z:
        mem = _members(z)
        if not any(k[1] == "ADDRESS_DETAIL" for k in mem):
            raise ValueError(f"{zip_path}: no G-NAF ADDRESS_DETAIL tables in this zip")
        suffix = {}
        if ("", "SUFFIX") in mem:
            for idx, r in _rows(z, mem[("", "SUFFIX")]):
                if len(r) > 1 and r[0]:
                    suffix[r[idx["CODE"]]] = r[idx["NAME"]]
        for st in states:
            if (st, "ADDRESS_DETAIL") not in mem:
                continue
            # streets: STREET_LOCALITY_PID -> normalised name (as the OSM roads are)
            street = {}
            for idx, r in _rows(z, mem[(st, "STREET_LOCALITY")]):
                if r[idx["DATE_RETIRED"]]:
                    continue
                parts = [r[idx["STREET_NAME"]], r[idx["STREET_TYPE_CODE"]],
                         suffix.get(r[idx["STREET_SUFFIX_CODE"]], r[idx["STREET_SUFFIX_CODE"]])]
                name = normalise(" ".join(p for p in parts if p))
                if name:
                    street[r[idx["STREET_LOCALITY_PID"]]] = name
            # addresses: ADDRESS_DETAIL_PID -> (street, number, last number)
            detail = {}
            for idx, r in _rows(z, mem[(st, "ADDRESS_DETAIL")]):
                if r[idx["DATE_RETIRED"]] or r[idx["ALIAS_PRINCIPAL"]] != "P":
                    continue
                s = street.get(r[idx["STREET_LOCALITY_PID"]])
                n = _int(r[idx["NUMBER_FIRST"]])
                if s is None or not n or n > MAX_NUMBER:
                    stats["g-naf: no street or number"] += 1
                    continue
                last = _int(r[idx["NUMBER_LAST"]]) or 0
                detail[r[idx["ADDRESS_DETAIL_PID"]]] = (r[idx["STREET_LOCALITY_PID"]], s, n,
                                                        last if n < last <= min(n + 200, MAX_NUMBER) else 0)
            street = None
            # points, one per (street, number): the mean over its units
            acc = {}
            for idx, r in _rows(z, mem[(st, "ADDRESS_DEFAULT_GEOCODE")]):
                d = detail.get(r[idx["ADDRESS_DETAIL_PID"]])
                if d is None or r[idx["DATE_RETIRED"]] or r[idx["GEOCODE_TYPE_CODE"]] in SKIP_GEOCODES:
                    continue
                try:
                    x = round(float(r[idx["LONGITUDE"]]) * 1e5)
                    y = round(float(r[idx["LATITUDE"]]) * 1e5)
                except ValueError:
                    continue
                if bbox and not (bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]):
                    continue
                k = (d[0], d[2])
                a = acc.get(k)
                if a is None:
                    acc[k] = [d[1], d[3], x, y, 1]
                else:
                    a[2] += x
                    a[3] += y
                    a[4] += 1
                    a[1] = max(a[1], d[3])
            detail = None
            for (_, n), (s, last, sx, sy, c) in sorted(acc.items()):
                out.add(s, round(sx / c), round(sy / c), [n, last] if last else [n])
            stats[f"g-naf {st}"] = len(acc)
            acc = None
    return out


def combine(osm, gnaf, stats=None):
    """OSM addresses plus the G-NAF ones OSM doesn't have: a G-NAF number is dropped when
    OSM has the same number on a street of the same name within SAME_RADIUS."""
    stats = stats if stats is not None else collections.Counter()
    # OSM points by (street, number), packed into ints to keep this small (4.4M for Australia)
    pts = {}
    for i in range(len(osm)):
        k = osm.street[i] << 20 | osm.num[i]
        p = (osm.x[i] + (1 << 27)) << 28 | (osm.y[i] + (1 << 27))
        old = pts.get(k)
        pts[k] = p if old is None else (old + (p,) if isinstance(old, tuple) else (old, p))
    out = Addresses()
    out.extend(osm)
    r2 = SAME_RADIUS * SAME_RADIUS
    sid = osm._ids
    mask = (1 << 28) - 1
    for i in range(len(gnaf)):
        s, n, x, y = gnaf.names[gnaf.street[i]], gnaf.num[i], gnaf.x[i], gnaf.y[i]
        o = sid.get(s)
        hit = pts.get(o << 20 | n) if o is not None else None
        if hit is not None:
            for p in (hit if isinstance(hit, tuple) else (hit,)):
                px, py = (p >> 28) - (1 << 27), (p & mask) - (1 << 27)
                if (px - x) ** 2 + (py - y) ** 2 <= r2:
                    break
            else:
                hit = None
        if hit is not None:
            stats["g-naf: already in OSM"] += 1
            continue
        out.add(s, x, y, [n])
        stats["g-naf: added"] += 1
    return out
