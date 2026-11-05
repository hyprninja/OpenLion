"""Card compatibility check: can OpenLion read and re-write this card's map tables exactly?

Every replaceable table is decoded and re-encoded with OpenLion's writers; the card is
compatible only if every table comes back byte-identical. Reads only.

Usage:
  python -m openlion.compat <navigation/> [--db db_aunz] [--json]

With --json, prints one JSON object per line (for the Windows app):
  {"event": "start", "db": ..., "tables": [...]}
  {"event": "progress", "table": ..., "done": i, "total": n}
  {"event": "table", "table": ..., "kind": ..., "identical": bool, ...}
  {"event": "result", "ok": bool, "db": ..., "failed": [...], "missing": [...]}

The report holds no map content: only header fields, record counts, timings and the
position of the first mismatch, so it is safe to attach to a public issue.
"""
import argparse
import json
import os
import re
import struct
import sys
import time
import traceback

if not __package__:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import feawrite, flat, mapfile, mapwrite, roads, schema, search  # noqa: E402
from openlion.common import HDR, build_header, parse_header  # noqa: E402

# Table kinds OpenLion replaces, found by file extension (names differ per database:
# db_aunz has au.cal, db_ag has agz.cal).
KINDS = ["rd", "str", "si", "mp0", "mg1", "mg2", "mg3", "mg4", "fea",
         "ctr", "stt", "cny", "cty", "cl", "ct", "rl", "rt", "cal", "cat"]
FIXED_RECORDS = {"ctr", "stt", "cny", "cl", "rl", "cal", "cat"}
FLAT_MAGIC = b"zip\x01"

LIST_CODECS = [
    ("pos_list", "pos_cnt", lambda b, c: mapwrite.decode_positions(b), mapwrite.encode_positions),
    ("exc_pos_list", "exc_pos_cnt", lambda b, c: mapwrite.decode_positions(b), mapwrite.encode_positions),
    ("node_list", "node_cnt", mapwrite.decode_nodes, mapwrite.encode_nodes),
    ("seg_list", "seg_cnt", mapwrite.decode_segments, mapwrite.encode_segments),
    ("shape_list", "shape_cnt", mapwrite.decode_shapes, mapwrite.encode_shapes),
    ("adas_list", "adas_cnt", mapwrite.decode_adas, mapwrite.encode_adas),
    ("turn_res_list", "turn_res_cnt", lambda b, c: mapwrite.decode_turn_res(b), mapwrite.encode_turn_res),
]


class Out:
    def __init__(self, as_json):
        self.as_json = as_json

    def emit(self, event, **kw):
        if self.as_json:
            print(json.dumps({"event": event, **kw}), flush=True)
        elif event == "table":
            state = "identical" if kw.get("identical") else ("ERROR " + kw["error"] if "error" in kw else "DIFFERENT")
            print(f"{kw['table']:10} {state}  ({kw.get('seconds', 0):.0f}s)", flush=True)
            if kw.get("first_diff"):
                print(f"           first difference: {kw['first_diff']}", flush=True)
        elif event == "result":
            print("COMPATIBLE" if kw["ok"] else f"NOT COMPATIBLE: failed {kw['failed']}, missing {kw['missing']}")
        elif event == "start":
            print(f"checking {kw['db']}: {len(kw['tables'])} tables", flush=True)


def find_tables(db):
    """{kind: filename} plus lists of missing and ambiguous kinds."""
    files = sorted(f for f in os.listdir(db) if os.path.isfile(os.path.join(db, f)))
    found, missing, ambiguous = {}, [], []
    for k in KINDS:
        m = [f for f in files if f.lower().endswith("." + k)]
        if len(m) == 1:
            found[k] = m[0]
        elif not m:
            missing.append(k)
        else:
            ambiguous.append(k)
    return found, missing, ambiguous


def header_info(raw):
    """Common 0x5E header: the five text stamps, IDs and counters, plus its hex."""
    if len(raw) < HDR:
        return {"short": len(raw)}
    h = parse_header(raw)
    stamps = [raw[i:i + 16].split(b"\0")[0].decode("latin-1") for i in range(0, 0x50, 16)]
    return {"stamps": stamps, "dbID": h["dbID"], "fileID": h["fileID"], "rec_cnt": h["rec_cnt"],
            "byte_cnt": h["byte_cnt"], "hex": raw[:HDR].hex()}


def first_diff(a, b):
    n = min(len(a), len(b))
    i = next((i for i in range(0, n, 4096) if a[i:i + 4096] != b[i:i + 4096]), None)
    if i is not None:
        i += next(j for j in range(4096) if a[i + j] != b[i + j])
    elif len(a) != len(b):
        i = n
    else:
        return None
    return {"offset": i, "len_original": len(a), "len_rebuilt": len(b)}


def check_flat_table(kind, d):
    """Tables stored in the FLAT container (or plain): decode records, re-encode, compare."""
    packed = d[:4] == FLAT_MAGIC
    raw = flat.unpack(d) if packed else d
    info = {"packed": packed, "header": header_info(raw)}
    if packed:
        info["flat_repack_identical"] = flat.pack(raw) == d
    if kind == "rd":
        recs = list(roads.iter_roads(raw))
        new = roads.encode_roads(raw[:HDR], recs)
        info["records"] = len(recs)
    elif kind == "str":
        strs = [(t, x) for _, t, x in roads.parse_strings(raw)]
        new = roads.encode_strings(raw[:HDR], strs)[0]
        info["records"] = len(strs)
    elif kind == "si":
        cnt = struct.unpack_from("<I", raw, 0x56)[0]
        new = build_header(raw[:HDR], cnt, HDR + 4 * cnt) + raw[HDR:]
        info["records"] = cnt
    elif kind in FIXED_RECORDS:
        size = search.RECORD_SIZES[kind]
        recs = search.records(raw, size)
        new = search.encode_records(raw[:HDR], recs)
        info["records"] = len(recs)
        info["record_size"] = size
    elif kind == "cty":
        cities = search.parse_cities(raw)
        new = search.encode_cities(raw[:HDR], cities)[0]
        info["records"] = len(cities)
    elif kind in ("ct", "rt"):
        trees = list(search.parse_trees(raw).values())
        new = search.encode_trees(raw[:HDR], trees)[0]
        info["records"] = len(trees)
    else:
        raise ValueError(kind)
    diff = first_diff(raw, new)
    if diff and kind in FIXED_RECORDS and diff["offset"] >= HDR:
        diff["record"] = (diff["offset"] - HDR) // info["record_size"]
    info["identical"] = diff is None and info.get("flat_repack_identical", True)
    info["first_diff"] = diff
    return info


def check_map(kind, d, progress):
    m = mapfile.MapFile(d)
    info = {"header": header_info(d),
            "map_header": {"min_long": m.min_long, "min_lat": m.min_lat, "max_long": m.max_long,
                           "max_lat": m.max_lat, "layer": m.layer, "pcl_cnt": m.pcl_cnt,
                           "kd_cnt": m.kd_cnt, "kd_depth": m.kd_depth}}
    lists_diff = {}
    first_bad = None
    errors = {}  # error message -> {"parcels": count, "first_parcel": i}
    zipped, sizes = [], []
    for i in range(m.pcl_cnt):
        if i % 250 == 0:
            progress(i, m.pcl_cnt)
        z0, _ = m.parcel_raw(i)
        try:
            raw = m.parcel(i)
            pc = mapwrite.ParcelContainer.decode(raw)
        except Exception as e:  # keep going so the report shows how widespread it is
            msg = f"{error_name(e)}: {e}"
            key = re.sub(r"\d+", "#", msg)  # group errors that differ only in sizes/offsets
            if key not in errors:
                errors[key] = {"parcels": 0, "first_parcel": i, "example": msg, "traceback": safe_traceback()}
            errors[key]["parcels"] += 1
            continue
        bad_here = []
        for lname, cname, dec, enc in LIST_CODECS:
            blob = pc.get(lname)
            if enc(dec(blob, pc.fields[pc.idx(cname)])) != blob:
                lists_diff[lname] = lists_diff.get(lname, 0) + 1
                bad_here.append(lname)
        new = pc.encode()
        if new != raw:
            bad_here.append("container")
            lists_diff["container"] = lists_diff.get("container", 0) + 1
        z = mapwrite.compress_parcel(new)
        if z != z0:
            lists_diff["zlib"] = lists_diff.get("zlib", 0) + 1
        if bad_here and first_bad is None:
            first_bad = {"parcel": i, "lists": bad_here, "raw_size": len(raw),
                         "counts": {c: pc.fields[pc.idx(c)] for _, c, _, _ in LIST_CODECS}}
        zipped.append(z)
        sizes.append(len(new))
    progress(m.pcl_cnt, m.pcl_cnt)
    if errors:
        info["parcel_errors"] = errors
        info["parcels_with_differences"] = lists_diff
        info["first_bad_parcel"] = first_bad
        info["identical"] = False
        info["error"] = f"{sum(e['parcels'] for e in errors.values())} of {m.pcl_cnt} parcels could not be decoded: " \
                        + "; ".join(f"{e['example']} ({e['parcels']} parcels)" for e in errors.values())
        return info
    hdr = {"min_long": m.min_long, "min_lat": m.min_lat, "max_long": m.max_long, "max_lat": m.max_lat,
           "layer": m.layer, "kd_depth": m.kd_depth, "max_tmcp_chain_len": m.max_tmcp_chain_len,
           "max_tmcp_location_size": m.max_tmcp_location_size}
    out = mapwrite.encode_mapfile(d[:HDR], hdr, m.kd, zipped, sizes)
    info["parcels_with_differences"] = lists_diff
    info["first_bad_parcel"] = first_bad
    info["first_diff"] = first_diff(d, out)
    info["identical"] = info["first_diff"] is None
    return info


def check_fea(d):
    out, st = feawrite.roundtrip(d)
    diff = first_diff(d, out)
    return {"header": header_info(d), "stats": dict(st), "first_diff": diff, "identical": diff is None}


def check_table(kind, path, progress):
    d = open(path, "rb").read()
    if kind.startswith("m") and kind[1:2] in ("p", "g") and kind[2:].isdigit():
        return check_map(kind, d, progress)
    if kind == "fea":
        return check_fea(d)
    return check_flat_table(kind, d)


def error_name(e):
    t = type(e)
    return t.__name__ if t.__module__ == "builtins" else f"{t.__module__}.{t.__name__}"


def safe_traceback():
    """Traceback with the user's home folder replaced by ~ (it may contain their name)."""
    tb = traceback.format_exc()
    home = os.path.expanduser("~")
    return tb.replace(home, "~") if home and home != "~" else tb


def run(nav, db_name="db_aunz", as_json=False):
    out = Out(as_json)
    db = os.path.join(nav, db_name, "db")
    if not os.path.isdir(db):
        out.emit("result", ok=False, db=db_name, failed=[], missing=["(database folder)"],
                 error=f"{db_name}/db not found on this card")
        return False
    try:
        sch = schema.use_card(db)
    except ValueError as e:  # a datamodel OpenLion has never seen
        out.emit("result", ok=False, db=db_name, failed=["au.mod"], missing=[], error=str(e))
        return False
    found, missing, ambiguous = find_tables(db)
    out.emit("start", db=db_name, datamodel=sch.name, tables=[found[k] for k in KINDS if k in found],
             missing=missing, ambiguous=ambiguous)
    failed = []
    for k in KINDS:
        if k not in found:
            continue
        fn = found[k]
        t0 = time.time()

        def progress(done, total, fn=fn):
            out.emit("progress", table=fn, done=done, total=total)

        out.emit("progress", table=fn, done=0, total=1)
        try:
            info = check_table(k, os.path.join(db, fn), progress)
        except Exception as e:  # report and carry on with the other tables
            info = {"identical": False, "error": f"{error_name(e)}: {e}", "traceback": safe_traceback()}
        info["size"] = os.path.getsize(os.path.join(db, fn))
        info["seconds"] = round(time.time() - t0, 1)
        if not info["identical"]:
            failed.append(fn)
        out.emit("table", table=fn, kind=k, **info)
    ok = not failed and not missing and not ambiguous
    out.emit("result", ok=ok, db=db_name, datamodel=sch.name, failed=failed, missing=missing,
             ambiguous=ambiguous)
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("navigation", help="the card's navigation/ folder (read only)")
    ap.add_argument("--db", default="db_aunz", help="database folder to check (default db_aunz)")
    ap.add_argument("--json", action="store_true", help="one JSON object per line")
    a = ap.parse_args()
    sys.exit(0 if run(a.navigation, a.db, a.json) else 1)
