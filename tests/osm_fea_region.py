"""Build auz.fea from OSM for a region, read it back and render levels 0 and 1.

Usage: python tests/osm_fea_region.py <extract.osm.pbf> <factory db dir> <out dir> lon0 lat0 lon1 lat1
"""
import collections
import os
import pickle
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import features, osm_fea, osm_import, render_fea  # noqa: E402

if __name__ == "__main__":
    pbf, db, out = sys.argv[1:4]
    bbox = tuple(round(float(v) * 1e5) for v in sys.argv[4:8])
    os.makedirs(out, exist_ok=True)
    t = time.time()
    cache = os.path.join(out, "fea_collected.pickle")
    if os.path.exists(cache):
        c = pickle.load(open(cache, "rb"))
    else:
        c = osm_fea.read(pbf, bbox)
        pickle.dump(c, open(cache, "wb"))
    print(f"read: {len(c.areas)} areas, {len(c.lines)} lines, {len(c.coast)} coastline ways, "
          f"{len(c.places)} places ({time.time() - t:.0f}s)")
    road_lines = []
    rc = os.path.join(out, "collected.pickle")
    if os.path.exists(rc):
        for wid, tags, pts in pickle.load(open(rc, "rb")).ways:
            rank, cls = osm_import.HIGHWAY[tags["highway"]]
            if rank is None:
                continue
            road_lines.append((rank, cls, tags.get("ref"), [(x, y) for _, x, y in pts]))
    t = time.time()
    data, stats = osm_fea.build(c, open(os.path.join(db, "auz.fea"), "rb").read(), bbox, road_lines)
    open(os.path.join(out, "auz.fea"), "wb").write(data)
    print(f"built auz.fea: {len(data):,} bytes ({time.time() - t:.0f}s)")
    by_level = collections.defaultdict(dict)
    for k, v in stats.items():
        if k.startswith("L"):
            lv, typ = k.split(" ", 1)
            by_level[lv][typ] = v
    for lv in sorted(by_level):
        print(f"  {lv}:", dict(sorted(by_level[lv].items())))
    # read back every non-empty parcel
    f = features.FeatureFile(data)
    ok = bad = 0
    for k, lv in enumerate(f.levels):
        for j, (o, n, nz) in enumerate(lv["parcels"]):
            if n:
                try:
                    f.features(k, j % lv["cnt_x"], j // lv["cnt_x"])
                    ok += 1
                except Exception as e:
                    bad += 1
    print(f"read back: {ok} parcels ok, {bad} failed")
    pad = 0
    print(render_fea.render(os.path.join(out, "auz.fea"), os.path.join(out, "fea_l0.png"), bbox, 1300, None, 0))
    render_fea.PALETTE[(8, 190)] = (200, 40, 40)
    orig = features.FeatureFile.features

    def as_lines(self, *a, **k):
        res = orig(self, *a, **k)
        for ft in res:
            if ft["category"] == 8:
                ft["category"] = 1
        return res
    features.FeatureFile.features = as_lines
    print(render_fea.render(os.path.join(out, "auz.fea"), os.path.join(out, "fea_l1.png"), bbox, 1300, None, 1))
