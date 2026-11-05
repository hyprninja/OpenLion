"""Round-trip test: parse factory files with openlion and re-write them.
Every file should come back byte-identical.

Usage: python tests/roundtrip.py <path to navigation/db_aunz/db> [mp0 mg1 ... fea flat]
Needs your own card copy; nothing from it is stored in this repo.
"""
import collections
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import flat, roads, mapfile, mapwrite, features, feawrite, schema  # noqa: E402
from openlion.common import build_header  # noqa: E402

LIST_CODECS = {
    "pos_list": ("pos_cnt", lambda b, c: mapwrite.decode_positions(b), lambda o: mapwrite.encode_positions(o)),
    "exc_pos_list": ("exc_pos_cnt", lambda b, c: mapwrite.decode_positions(b), lambda o: mapwrite.encode_positions(o)),
    "node_list": ("node_cnt", mapwrite.decode_nodes, mapwrite.encode_nodes),
    "seg_list": ("seg_cnt", mapwrite.decode_segments, mapwrite.encode_segments),
    "shape_list": ("shape_cnt", mapwrite.decode_shapes, mapwrite.encode_shapes),
    "adas_list": ("adas_cnt", mapwrite.decode_adas, mapwrite.encode_adas),
    "turn_res_list": ("turn_res_cnt", lambda b, c: mapwrite.decode_turn_res(b), mapwrite.encode_turn_res),
}


def check_flat(db):
    res = {}
    for fn in sorted(os.listdir(db)):
        d = open(os.path.join(db, fn), "rb").read()
        if d[:4] != b"zip\x01":
            continue
        raw = flat.unpack(d)
        ok = flat.pack(raw) == d
        if fn.endswith(".rd"):
            ok &= roads.encode_roads(raw[:0x5E], list(roads.iter_roads(raw))) == raw
        if fn.endswith(".str"):
            ok &= roads.encode_strings(raw[:0x5E], [(t, x) for _, t, x in roads.parse_strings(raw)])[0] == raw
        if fn.endswith(".si"):
            cnt = struct.unpack_from("<I", raw, 0x56)[0]
            ok &= build_header(raw[:0x5E], cnt, 0x5E + 4 * cnt) + raw[0x5E:] == raw
        res[fn] = ok
    return res


def check_map(path):
    d = open(path, "rb").read()
    m = mapfile.MapFile(d)
    st = collections.Counter()
    zipped, sizes = [], []
    for i in range(m.pcl_cnt):
        z0, n0 = m.parcel_raw(i)
        raw = m.parcel(i)
        pc = mapwrite.ParcelContainer.decode(raw)
        for lname, (cname, dec, enc) in LIST_CODECS.items():
            blob = pc.get(lname)
            cnt = pc.fields[pc.idx(cname)]
            if enc(dec(blob, cnt)) == blob:
                st[lname + " ok"] += 1
            else:
                st[lname + " DIFF"] += 1
        new = pc.encode()
        st["container ok" if new == raw else "container DIFF"] += 1
        z = mapwrite.compress_parcel(new)
        st["zlib ok" if z == z0 else "zlib DIFF"] += 1
        zipped.append(z)
        sizes.append(len(new))
    hdr = {"min_long": m.min_long, "min_lat": m.min_lat, "max_long": m.max_long, "max_lat": m.max_lat,
           "layer": m.layer, "kd_depth": m.kd_depth, "max_tmcp_chain_len": m.max_tmcp_chain_len,
           "max_tmcp_location_size": m.max_tmcp_location_size}
    out = mapwrite.encode_mapfile(d[:0x5E], hdr, m.kd, zipped, sizes)
    st["FILE IDENTICAL" if out == d else "FILE DIFF"] += 1
    return st


def check_search(db):
    """Address-search tables re-encoded from their parsed form."""
    from openlion import build
    reps = build.search_identity(db)
    return {k.split("/")[-1]: v == open(os.path.join(db, k.split("/")[-1]), "rb").read() for k, v in reps.items()}


def check_fea(path):
    d = open(path, "rb").read()
    out, st = feawrite.roundtrip(d)
    st["FILE IDENTICAL" if out == d else "FILE DIFF"] += 1
    return st


if __name__ == "__main__":
    db = sys.argv[1]
    print("datamodel: Arriba", schema.use_card(db).name)
    want = sys.argv[2:] or ["flat", "search", "mp0", "mg1", "mg2", "mg3", "mg4", "fea"]
    for w in want:
        t = time.time()
        if w == "search":
            r = check_search(db)
            print("search tables:", "all identical" if all(r.values()) else r)
        elif w == "flat":
            r = check_flat(db)
            print("flat tables:", "all identical" if all(r.values()) else r)
        elif w == "fea":
            print("auz.fea:", dict(check_fea(os.path.join(db, "auz.fea"))))
        else:
            print(f"auz.{w}:", dict(check_map(os.path.join(db, f"auz.{w}"))), f"({time.time() - t:.0f}s)")
