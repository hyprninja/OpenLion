"""Names + address search from OSM for a region, then a lookup through the new tables.

Usage: python tests/osm_names_region.py <extract.osm.pbf> <factory db dir> <out dir> lon0 lat0 lon1 lat1 [CITY STREET]...
Uses <out>/collected.pickle from tests/osm_region.py if present.
"""
import os
import pickle
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import flat, graph, layers, mapfile, osm_import, osm_names, schema, search, tiler  # noqa: E402


def lookup(tables, city, street, catalog=0):
    """Follow the unit's path: city tree -> city list -> road tree -> road list -> road record.
    catalog: the country's city tree (0 Australia, 1 New Zealand)."""
    st, cty, cl, ct, rl, rt, rd = (tables[k] for k in ("auz.str", "auz.cty", "auz.cl", "auz.ct", "auz.rl", "auz.rt", "auz.rd"))
    S = lambda o: st[0x5E + o + 1:st.index(b"\0", 0x5E + o)].decode("latin-1")
    C = {c["cityID"]: c for c in search.parse_cities(cty)}
    CL = [struct.unpack("<IHi", r) for r in search.records(cl, 10)]
    RL = [struct.unpack("<IB", r) for r in search.records(rl, 5)]
    trees_c, trees_r = search.parse_trees(ct), search.parse_trees(rt)

    def descend(t, text):
        N = t["nodes"]
        i, used = 1, 0
        while used < len(text):
            k, ci, cc, di, dc = N[i]
            nxt = [c for c in range(ci, ci + cc) if N[c][0].rstrip(b"\0").decode("latin-1") == text[used]]
            if not nxt:
                break
            i, used = nxt[0], used + 1
        return t["listID_start"] + N[i][3], N[i][4], used

    a, n, used_c = descend(list(trees_c.values())[catalog], city)
    hits = [CL[j] for j in range(a, a + n) if S(C[CL[j][0]]["name_offsets"][CL[j][1]]) == city]
    if not hits:
        return f"city {city!r}: not found (tree covered {used_c} chars, {n} candidates)"
    cid, q, rto = hits[0]
    a, n, used_r = descend(trees_r[rto], street)
    for j in range(a, a + n):
        rid, q = RL[j]
        off = 0x5E + rid
        R = schema.active().road_rec          # 29 bytes (5.2.1) or 26 (5.3)
        rec = R.unpack_from(rd, off)
        nmo = struct.unpack_from("<I", rd, off + R.size + 5 * q)[0]
        if S(nmo) == street:
            return (f"{city} / {street}: tree {used_c}+{used_r} chars -> road record {rid} "
                    f"at {rec[3] / 1e5:.5f},{rec[4] / 1e5:.5f} +{rec[5] / 1e5:.4f},{rec[6] / 1e5:.4f}", rid)
    return f"{city} / {street}: street not found ({n} candidates)"


if __name__ == "__main__":
    pbf, db, out = sys.argv[1:4]
    lon0, lat0, lon1, lat1 = (float(v) for v in sys.argv[4:8])
    queries = sys.argv[8:]
    bbox = tuple(round(v * 1e5) for v in (lon0, lat0, lon1, lat1))
    os.makedirs(out, exist_ok=True)
    cache = os.path.join(out, "collected.pickle")
    c = pickle.load(open(cache, "rb")) if os.path.exists(cache) else osm_import.read(pbf, bbox)
    if not os.path.exists(cache):
        pickle.dump(c, open(cache, "wb"))
    g, si, st0 = osm_import.build_graph(c)
    rank = layers.node_ranks(g, si)
    for n in range(g.n_nodes):
        g.node_info[n]["rank"] = rank[n]
    bcache = os.path.join(out, "boundaries.pickle")
    t = time.time()
    if os.path.exists(bcache):
        areas = pickle.load(open(bcache, "rb"))
    else:
        areas = osm_names.read_boundaries(pbf, bbox)
        pickle.dump(areas, open(bcache, "wb"))
    print(f"boundaries: {len(areas)} areas ({time.time() - t:.0f}s):",
          {name: sum(1 for a in areas if a.level == lv) for name, lv in (("postcodes", 0), ("states", 4), ("LGAs", 6), ("suburbs", 9))})

    def raw(fn):
        d = open(os.path.join(db, fn), "rb").read()
        return flat.unpack(d) if d[:4] == b"zip\x01" else d
    factory = {"stt": raw("auz.stt"), "cny": raw("auz.cny"), "cal": raw("au.cal"), "cat": raw("au.cat")}
    for k, fn in (("str_header", "auz.str"), ("cty_header", "auz.cty"), ("rd_header", "auz.rd"),
                  ("rt_header", "auz.rt"), ("rl_header", "auz.rl"), ("cl_header", "auz.cl"),
                  ("ct_header", "auz.ct")):
        factory[k] = raw(fn)[:0x5E]
    t = time.time()
    tables, stats = osm_names.build(g, g.seg_label, areas, factory)
    print(f"names/search tables ({time.time() - t:.0f}s):", dict(stats))
    # structural checks with the readers
    ok = {}
    from openlion import roads
    ok["str parses"] = bool(roads.parse_strings(tables["auz.str"]))
    ok["cty parses"] = len(search.parse_cities(tables["auz.cty"])) == stats["cities"]
    ok["rd parses"] = sum(1 for _ in roads.iter_roads(tables["auz.rd"])) == stats["road records"]
    st_ = tables["auz.str"]
    S_ = lambda o: st_[0x5E + o + 1:st_.index(b"\0", 0x5E + o)].decode("latin-1")
    rdr = {r["offset"] - 0x5E: r for r in roads.iter_roads(tables["auz.rd"])}
    RL_ = [struct.unpack("<IB", r) for r in search.records(tables["auz.rl"], 5)]
    rl_names = [S_(rdr[rid]["names"][q][0]) for rid, q in RL_]
    ok["road trees valid"] = all(search.check_tree(dict(t, nodes=t["nodes"]), rl_names[t["listID_start"]:t["listID_end"] + 1])
                                 for t in search.parse_trees(tables["auz.rt"]).values())
    CC = {c["cityID"]: c for c in search.parse_cities(tables["auz.cty"])}
    CL_ = [struct.unpack("<IHi", r) for r in search.records(tables["auz.cl"], 10)]
    cl_names = [S_(CC[c]["name_offsets"][q]) for c, q, _ in CL_]
    ok["city tree valid"] = search.check_tree(search.parse_trees(tables["auz.ct"])[0], cl_names)
    ok["lists sorted"] = (cl_names == sorted(cl_names, key=search.collate_key))
    print("structure:", ok)
    for k, v in tables.items():
        open(os.path.join(out, k), "wb").write(v if k.startswith("au.") else flat.pack(v))
    # mp0 with name links
    data = tiler.tile(g, 0, open(os.path.join(db, "auz.mp0"), "rb").read(0x5E))
    open(os.path.join(out, "auz.mp0"), "wb").write(data)
    s1, s2 = graph.summary(g), graph.summary(graph.extract(data))
    print("mp0 with names read back identical:", {k: s1[k] == s2[k] for k in s1})
    for i in range(0, len(queries), 2):
        r = lookup(tables, queries[i], queries[i + 1])
        if isinstance(r, tuple):
            msg, rid = r
            segs = sum(1 for s in range(g.n_segs) if g.seg_names[s] and g.seg_names[s][0][2] == rid)
            print("  ", msg, f"-> {segs} segments linked")
        else:
            print("  ", r)
