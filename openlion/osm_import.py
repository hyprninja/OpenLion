"""OpenStreetMap -> openlion Graph (roads, attributes, speeds, turn restrictions).

Needs the `osmium` package (pyosmium). Map data (c) OpenStreetMap contributors, ODbL.

Pipeline: read routable ways with node locations -> split at junctions (shared or end
nodes) -> one segment per piece, oriented from its smaller-(lon, lat) end -> seginfo
records from tags -> arms ordered clockwise from north -> U-turn / wrong-way exit
masks -> maxspeed -> turn-restriction relations. Names and address search are a later
stage (they need the city model).
"""
import collections
import math

from .graph import Graph
from .mapfile import SEGINFO_FIELDS

FT = 0.3048

# highway=* -> (rank, class). rank drives the mg layers; class follows the factory
# meaning (0 freeway, 1 highway, 2 arterial, 3 street, 6 ramp, 7 track, 8 busway).
# Ranks calibrated on inner Melbourne: factory km per rank 4/3/2/1 = 19/49/196/68;
# OSM motorway/trunk/primary+secondary/tertiary = 35/41/210/145.
HIGHWAY = {
    "motorway": (4, 0), "trunk": (3, 1), "primary": (2, 1), "secondary": (2, 2),
    "tertiary": (1, 2), "unclassified": (0, 3), "residential": (0, 3),
    "living_street": (0, 3), "service": (0, 3), "road": (0, 3), "track": (0, 7),
    "busway": (0, 8),
    "motorway_link": (None, 6), "trunk_link": (None, 6), "primary_link": (None, 6),
    "secondary_link": (None, 6), "tertiary_link": (None, 6),
}
LINK_RANK = {"motorway_link": 3, "trunk_link": 2, "primary_link": 1, "secondary_link": 1,
             "tertiary_link": 0}
UNSEALED = {"unpaved", "gravel", "dirt", "ground", "earth", "sand", "compacted", "fine_gravel",
            "grass", "mud", "pebblestone"}
NO_ACCESS = {"no", "private"}


def way_ok(tags):
    hw = tags.get("highway")
    if hw not in HIGHWAY:
        return False
    if hw == "service" and tags.get("service") in ("parking_aisle", "drive-through", "driveway"):
        return False
    if tags.get("access") in NO_ACCESS and tags.get("motor_vehicle", tags.get("motorcar")) not in ("yes", "designated"):
        return False
    if tags.get("area") == "yes":
        return False
    return True


def oneway_dir(tags):
    """+1 forward only, -1 backward only, 0 both."""
    ow = tags.get("oneway")
    if ow in ("yes", "true", "1"):
        return 1
    if ow == "-1":
        return -1
    if ow == "no":
        return 0
    if tags.get("highway") in ("motorway",) or tags.get("junction") in ("roundabout", "circular"):
        return 1
    return 0


def parse_speed(v):
    if not v:
        return None
    v = v.strip().lower()
    if v.endswith("mph"):
        try:
            return round(float(v[:-3]) * 1.609)
        except ValueError:
            return None
    try:
        s = int(float(v.split(";")[0]))
    except ValueError:
        return None
    return s if 5 <= s <= 130 else None


def seginfo_fields(tags):
    hw = tags["highway"]
    rank, cls = HIGHWAY[hw]
    if rank is None:
        rank = LINK_RANK[hw]
    f = {n: 0 for n, _, _ in SEGINFO_FIELDS}
    f["rank"], f["class"] = rank, cls
    if tags.get("surface") in UNSEALED or hw == "track":
        f["restclass"] = 3
    if tags.get("toll") == "yes":
        f["toll_road"] = 1
    sp = parse_speed(tags.get("maxspeed"))
    if hw in ("residential", "living_street", "service") or (sp is not None and sp <= 60):
        f["urban"] = 1
        f["detailedcity"] = 1
    if hw in ("motorway", "trunk", "primary") and oneway_dir(tags) != 0:
        f["dbldig"] = 1
    return f


def pack_fields(f):
    v = 0
    for n, o, w in SEGINFO_FIELDS:
        v |= (f[n] & ((1 << w) - 1)) << o
    return v


def length_ft(points):
    d = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        p1, p2 = math.radians(y1 / 1e5), math.radians(y2 / 1e5)
        dl = math.radians((x2 - x1) / 1e5)
        a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        d += 2 * 6371008.8 * math.asin(min(1.0, math.sqrt(a)))
    return max(1, round(d / FT))


def bearing(p, q):
    """Degrees clockwise from north, from p to q (scaled lon/lat units)."""
    dx = (q[0] - p[0]) * math.cos(math.radians(p[1] / 1e5))
    dy = q[1] - p[1]
    return math.degrees(math.atan2(dx, dy)) % 360


class _Collector:
    def __init__(self, bbox):
        self.bbox = bbox
        self.ways = []          # (way_id, tags dict, [(node_id, x, y)])
        self.restrictions = []  # (type, from_way, via_node, to_way)

    def inside(self, x, y):
        b = self.bbox
        return b is None or (b[0] <= x <= b[2] and b[1] <= y <= b[3])


def read(pbf_path, bbox=None, progress=None):
    """Collect routable ways (any node inside bbox, in degrees*1e5) and restrictions."""
    import osmium
    c = _Collector(bbox)
    import osmium.filter
    # nodes are read (in C++) only to fill the location cache; the filters keep them,
    # and untagged / non-road objects, away from Python
    fp = (osmium.FileProcessor(pbf_path, osmium.osm.NODE | osmium.osm.WAY | osmium.osm.RELATION)
          .with_locations()
          .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY | osmium.osm.RELATION))
          .with_filter(osmium.filter.KeyFilter("highway", "type")))
    n = 0
    for obj in fp:
        if obj.is_way():
            tags = {t.k: t.v for t in obj.tags}
            if not way_ok(tags):
                continue
            pts = []
            hit = False
            for nd in obj.nodes:
                if not nd.location.valid():
                    pts = None
                    break
                x, y = round(nd.location.lon * 1e5), round(nd.location.lat * 1e5)
                hit |= c.inside(x, y)
                pts.append((nd.ref, x, y))
            if pts and hit and len(pts) >= 2:
                c.ways.append((obj.id, tags, pts))
        elif obj.is_relation():
            tags = {t.k: t.v for t in obj.tags}
            if tags.get("type") != "restriction":
                continue
            r = tags.get("restriction") or tags.get("restriction:motorcar")
            if not r:
                continue
            frm = via = to = None
            for mem in obj.members:
                if mem.role == "from" and mem.type == "w":
                    frm = mem.ref
                elif mem.role == "to" and mem.type == "w":
                    to = mem.ref
                elif mem.role == "via" and mem.type == "n":
                    via = mem.ref
            if frm and via and to:
                c.restrictions.append((r, frm, via, to))
        n += 1
        if progress and n % 2_000_000 == 0:
            progress(n, len(c.ways))
    return c


def build_graph(c, max_shape_points=500):
    """Collected OSM data -> (Graph, seginfo record list, stats)."""
    use = collections.Counter()
    for _, _, pts in c.ways:
        for k, (nid, _, _) in enumerate(pts):
            use[nid] += 1
        use[pts[0][0]] += 1
        use[pts[-1][0]] += 1
    g = Graph()
    g.seg_label = []      # OSM (name, ref) per segment, for osm_names
    nodes = {}
    si_index, si_records = {}, []
    stats = collections.Counter()
    piece_of = collections.defaultdict(list)   # osm way id -> [(seg, a_osm, b_osm)]

    def node(nid, x, y):
        if nid not in nodes:
            nodes[nid] = g.add_node((x, y))
        return nodes[nid]

    for wid, tags, pts in c.ways:
        rec = pack_fields(seginfo_fields(tags))
        ow = oneway_dir(tags)
        speed = parse_speed(tags.get("maxspeed"))
        # split points: junctions, plus extra cuts to bound shape length / avoid self-loops
        cuts = [0]
        for k in range(1, len(pts) - 1):
            if use[pts[k][0]] >= 2 or k - cuts[-1] >= max_shape_points:
                cuts.append(k)
        cuts.append(len(pts) - 1)
        pieces = list(zip(cuts, cuts[1:]))
        fixed = []
        for i, j in pieces:
            if pts[i][0] == pts[j][0] and j - i >= 2:     # closed piece: split in the middle
                m = (i + j) // 2
                fixed += [(i, m), (m, j)]
            else:
                fixed.append((i, j))
        for i, j in fixed:
            seq = pts[i:j + 1]
            if seq[0][0] == seq[-1][0]:
                stats["dropped zero-length loops"] += 1
                continue
            xy = [(x, y) for _, x, y in seq]
            a, b = node(*seq[0]), node(*seq[-1])
            fwd = True
            if g.node_xy[a] > g.node_xy[b]:
                a, b, xy, fwd = b, a, xy[::-1], False
            shape = []
            for p in xy[1:-1]:
                if p != (shape[-1] if shape else xy[0]):
                    shape.append(p)
            lower = ow == 0 or (ow == 1) == fwd     # drivable left -> right
            upper = ow == 0 or (ow == -1) == fwd    # drivable right -> left
            v = rec | (int(lower) << 8) | (int(upper) << 9)
            if v not in si_index:
                si_index[v] = len(si_records)
                si_records.append(v)
            adas = {"content": 2, "speed_kmh": speed, "lanes": None, "n_limits": 0} if speed else None
            s = g.add_segment(a, b, length_ft(xy), si_index[v], None, shape or None, [], adas)
            g.seg_shape[s] = shape if shape else None
            g.seg_label.append((tags.get("name"), tags.get("ref")))
            piece_of[wid].append((s, seq[0][0], seq[-1][0]))
            stats["segments"] += 1
            stats["with speed limit"] += bool(speed)

    # arms: clockwise from north by the bearing of the first step away from the node
    for s in range(g.n_segs):
        g.node_arms[g.seg_a[s]].append(s)
        g.node_arms[g.seg_b[s]].append(s)

    def first_step(n, s):
        sh = g.seg_shape[s] or []
        if g.seg_a[s] == n:
            q = sh[0] if sh else g.node_xy[g.seg_b[s]]
        else:
            q = sh[-1] if sh else g.node_xy[g.seg_a[s]]
        return bearing(g.node_xy[n], q)

    for n in range(g.n_nodes):
        g.node_arms[n].sort(key=lambda s: (first_step(n, s), s))
        if len(g.node_arms[n]) > 8:
            stats["nodes with >8 arms (unsupported)"] += 1

    # U-turn and wrong-way exit masks at each end
    def can_leave(n, s):
        v = si_records[g.seg_info[s]]
        return bool(v >> 8 & 1) if g.seg_a[s] == n else bool(v >> 9 & 1)

    for s in range(g.n_segs):
        masks = []
        for n in (g.seg_a[s], g.seg_b[s]):
            arms = g.node_arms[n]
            m = 0
            if len(arms) >= 2:
                for k, t in enumerate(arms):
                    if t == s or not can_leave(n, t):
                        m |= 1 << k
            masks.append(m)
        g.seg_restr[s] = tuple(masks) if any(masks) else None

    # turn restriction relations (single via node); several relations can state the same ban
    seen_turns = set()
    for kind, frm, via, to in c.restrictions:
        if via not in nodes:
            continue
        vn = nodes[via]
        fs = [s for s, a, b in piece_of.get(frm, []) if via in (a, b)]
        ts = [s for s, a, b in piece_of.get(to, []) if via in (a, b)]
        if len(fs) != 1 or len(ts) != 1:
            stats["restrictions skipped (ambiguous)"] += 1
            continue
        arms = g.node_arms[vn]
        if kind.startswith("no_"):
            banned = [arms.index(ts[0])]
        elif kind.startswith("only_"):
            banned = [k for k, t in enumerate(arms) if t != ts[0] and t != fs[0]]
        else:
            continue
        for k in banned:
            key = (fs[0], vn, k)
            if k < 8 and key not in seen_turns:
                seen_turns.add(key)
                g.turns.append({"seg": fs[0], "via": vn, "arms": [k], "direction": 0})
                stats["turn restrictions"] += 1
    stats["nodes"] = g.n_nodes
    stats["seginfo records"] = len(si_records)
    return g, si_records, stats
