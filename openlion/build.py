"""Build a navigation/ folder: copy every source file byte-for-byte except the
map tables we regenerate, then verify the untouched files against the source.

The aim is that the head unit sees the same card and version (cdrom.txt, cdrom.toc,
files.cfg, zone.cfg, *.vi, VERSIONS.CFG, dbal DLLs, voices, images unchanged) with
new map data inside.

Usage (identity build, proves the writers):
  python -m openlion.build <src navigation/> <dst navigation/> --identity
"""
import hashlib
import os
import shutil
import sys

# Files the converter is allowed to replace (relative to navigation/). Everything
# else is copied unchanged and verified.
REPLACEABLE = {
    "db_aunz/db/auz.rd", "db_aunz/db/auz.str", "db_aunz/db/auz.si",
    "db_aunz/db/auz.mp0", "db_aunz/db/auz.mg1", "db_aunz/db/auz.mg2",
    "db_aunz/db/auz.mg3", "db_aunz/db/auz.mg4", "db_aunz/db/auz.fea",
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def build(src, dst, replacements):
    """replacements: {relative path: bytes}. Returns a report dict."""
    bad = set(replacements) - REPLACEABLE
    if bad:
        raise ValueError(f"refusing to replace protected files: {sorted(bad)}")
    if os.path.abspath(dst).startswith(os.path.abspath(src)):
        raise ValueError("destination must not be inside the source")
    report = {"copied": 0, "replaced": [], "verify_failed": []}
    for dp, _, files in os.walk(src):
        for fn in files:
            s = os.path.join(dp, fn)
            rel = os.path.relpath(s, src).replace(os.sep, "/")
            d = os.path.join(dst, rel)
            os.makedirs(os.path.dirname(d), exist_ok=True)
            if rel in replacements:
                with open(d, "wb") as f:
                    f.write(replacements[rel])
                shutil.copystat(s, d)  # keep factory timestamps
                report["replaced"].append(rel)
            else:
                shutil.copy2(s, d)
                report["copied"] += 1
                if sha256(s) != sha256(d):
                    report["verify_failed"].append(rel)
    return report


def identity_replacements(src):
    """Rebuild every replaceable table from its parsed form (no new data)."""
    if not __package__:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from openlion import flat, roads, mapfile, mapwrite, feawrite
    from openlion.common import build_header
    import struct
    db = os.path.join(src, "db_aunz", "db")
    out = {}
    raw = flat.unpack(open(os.path.join(db, "auz.rd"), "rb").read())
    out["db_aunz/db/auz.rd"] = flat.pack(roads.encode_roads(raw[:0x5E], list(roads.iter_roads(raw))))
    raw = flat.unpack(open(os.path.join(db, "auz.str"), "rb").read())
    out["db_aunz/db/auz.str"] = flat.pack(
        roads.encode_strings(raw[:0x5E], [(t, x) for _, t, x in roads.parse_strings(raw)])[0])
    raw = flat.unpack(open(os.path.join(db, "auz.si"), "rb").read())
    cnt = struct.unpack_from("<I", raw, 0x56)[0]
    out["db_aunz/db/auz.si"] = flat.pack(build_header(raw[:0x5E], cnt, 0x5E + 4 * cnt) + raw[0x5E:])
    for layer in ("mp0", "mg1", "mg2", "mg3", "mg4"):
        d = open(os.path.join(db, f"auz.{layer}"), "rb").read()
        m = mapfile.MapFile(d)
        zipped, sizes = [], []
        for i in range(m.pcl_cnt):
            pc = mapwrite.ParcelContainer.decode(m.parcel(i))
            for lname, cname, dec, enc in (
                    ("pos_list", "pos_cnt", lambda b, c: mapwrite.decode_positions(b), mapwrite.encode_positions),
                    ("exc_pos_list", "exc_pos_cnt", lambda b, c: mapwrite.decode_positions(b), mapwrite.encode_positions),
                    ("node_list", "node_cnt", mapwrite.decode_nodes, mapwrite.encode_nodes),
                    ("seg_list", "seg_cnt", mapwrite.decode_segments, mapwrite.encode_segments),
                    ("shape_list", "shape_cnt", mapwrite.decode_shapes, mapwrite.encode_shapes),
                    ("adas_list", "adas_cnt", mapwrite.decode_adas, mapwrite.encode_adas),
                    ("turn_res_list", "turn_res_cnt", lambda b, c: mapwrite.decode_turn_res(b), mapwrite.encode_turn_res)):
                pc.set(lname, enc(dec(pc.get(lname), pc.fields[pc.idx(cname)])))
            new = pc.encode()
            zipped.append(mapwrite.compress_parcel(new))
            sizes.append(len(new))
        hdr = {"min_long": m.min_long, "min_lat": m.min_lat, "max_long": m.max_long,
               "max_lat": m.max_lat, "layer": m.layer, "kd_depth": m.kd_depth,
               "max_tmcp_chain_len": m.max_tmcp_chain_len,
               "max_tmcp_location_size": m.max_tmcp_location_size}
        out[f"db_aunz/db/auz.{layer}"] = mapwrite.encode_mapfile(d[:0x5E], hdr, m.kd, zipped, sizes)
    out["db_aunz/db/auz.fea"] = feawrite.roundtrip(open(os.path.join(db, "auz.fea"), "rb").read())[0]
    return out


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    if "--identity" not in sys.argv:
        sys.exit("only --identity builds are implemented so far")
    reps = identity_replacements(src)
    rep = build(src, dst, reps)
    same = [r for r in rep["replaced"] if sha256(os.path.join(src, r)) == sha256(os.path.join(dst, r))]
    print(f"copied {rep['copied']} files unchanged, verify failures: {rep['verify_failed'] or 'none'}")
    print(f"rebuilt {len(rep['replaced'])} map tables, byte-identical to source: {len(same)}/{len(rep['replaced'])}")
