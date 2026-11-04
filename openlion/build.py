"""Build a navigation/ folder: copy every source file byte-for-byte except the
map tables we regenerate, then verify the untouched files against the source.

The aim is that the head unit sees the same card and version (cdrom.txt, cdrom.toc,
files.cfg, zone.cfg, *.vi, VERSIONS.CFG, dbal DLLs, voices, images unchanged) with
new map data inside.

Usage:
  python -m openlion.build <src navigation/> <dst navigation/> --identity      (writer test)
  python -m openlion.build <src navigation/> <dst navigation/> --tables <dir>  (new map data)
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
    # address search (report 10)
    "db_aunz/db/auz.ctr", "db_aunz/db/auz.stt", "db_aunz/db/auz.cny", "db_aunz/db/auz.cty",
    "db_aunz/db/auz.cl", "db_aunz/db/auz.ct", "db_aunz/db/auz.rl", "db_aunz/db/auz.rt",
    "db_aunz/db/au.cal", "db_aunz/db/au.cat",
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
    out.update(search_identity(db))
    return out


def search_identity(db):
    """Rebuild the address-search tables from their parsed form."""
    from openlion import flat, search
    out = {}

    def load(fn):
        d = open(os.path.join(db, fn), "rb").read()
        return (flat.unpack(d), True) if d[:4] == b"zip\x01" else (d, False)

    for fn, kind in (("auz.ctr", "ctr"), ("auz.stt", "stt"), ("auz.cny", "cny"), ("auz.cl", "cl"),
                     ("auz.rl", "rl"), ("au.cal", "cal"), ("au.cat", "cat")):
        raw, zipped = load(fn)
        new = search.encode_records(raw[:0x5E], search.records(raw, search.RECORD_SIZES[kind]))
        out[f"db_aunz/db/{fn}"] = flat.pack(new) if zipped else new
    raw, _ = load("auz.cty")
    out["db_aunz/db/auz.cty"] = flat.pack(search.encode_cities(raw[:0x5E], search.parse_cities(raw))[0])
    for fn in ("auz.ct", "auz.rt"):
        raw, _ = load(fn)
        out[f"db_aunz/db/{fn}"] = flat.pack(search.encode_trees(raw[:0x5E], list(search.parse_trees(raw).values()))[0])
    return out


def table_replacements(src, tables_dir):
    """Map every table file in tables_dir (named as in db_aunz/db/) onto its card path."""
    out = {}
    for fn in sorted(os.listdir(tables_dir)):
        rel = f"db_aunz/db/{fn}"
        if rel not in REPLACEABLE:
            raise ValueError(f"{fn} is not a replaceable map table")
        if not os.path.exists(os.path.join(src, rel)):
            raise ValueError(f"{fn} has no factory counterpart")
        out[rel] = open(os.path.join(tables_dir, fn), "rb").read()
    return out


FLAT_MAGIC = bytes([0x7A, 0x69, 0x70, 0x01])  # "zip\x01"


def check_headers(src, dst, rels):
    """The 0x5E-byte common header must keep the factory stamps (database ID, file
    ID, version) and only change rec_cnt/byte_cnt. Returns a list of problems."""
    if not __package__:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from openlion import flat
    bad = []
    for rel in rels:
        a = open(os.path.join(src, rel), "rb").read()
        b = open(os.path.join(dst, rel), "rb").read()
        if (a[:4] == FLAT_MAGIC) != (b[:4] == FLAT_MAGIC):
            bad.append(f"{rel}: packing differs from factory")
            continue
        if a[:4] == FLAT_MAGIC:
            a, b = flat.unpack(a), flat.unpack(b)
        if a[:0x56] != b[:0x56]:
            bad.append(f"{rel}: header stamps differ from factory")
    return bad


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Build a navigation/ folder from a factory copy.")
    ap.add_argument("src", help="factory navigation/ folder (read only)")
    ap.add_argument("dst", help="new navigation/ folder (must not exist yet)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--identity", action="store_true", help="rebuild the factory tables (writer test)")
    g.add_argument("--tables", help="folder of new tables named as in db_aunz/db/ (e.g. pipeline out/db)")
    a = ap.parse_args()
    if os.path.exists(a.dst) and os.listdir(a.dst):
        sys.exit(f"{a.dst} already exists and is not empty")
    reps = identity_replacements(a.src) if a.identity else table_replacements(a.src, a.tables)
    rep = build(a.src, a.dst, reps)
    print(f"copied {rep['copied']} files unchanged, verify failures: {rep['verify_failed'] or 'none'}")
    same = [r for r in rep["replaced"] if sha256(os.path.join(a.src, r)) == sha256(os.path.join(a.dst, r))]
    print(f"replaced {len(rep['replaced'])} map tables, byte-identical to factory: {len(same)}/{len(rep['replaced'])}")
    bad = check_headers(a.src, a.dst, rep["replaced"])
    print(f"header stamps kept: {len(rep['replaced']) - len(bad)}/{len(rep['replaced'])}")
    for line in bad:
        print("  " + line)
    sys.exit(1 if rep["verify_failed"] or bad else 0)
