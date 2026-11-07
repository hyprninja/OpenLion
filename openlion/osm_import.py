"""OpenStreetMap -> openlion Graph (roads, attributes, speeds, turn restrictions).

Needs the `osmium` package (pyosmium). Map data (c) OpenStreetMap contributors, ODbL.

Pipeline: read routable ways with node locations -> split at junctions (shared or end
nodes) -> one segment per piece, oriented from its smaller-(lon, lat) end -> seginfo
records from tags -> arms ordered clockwise from north -> U-turn / wrong-way exit
masks -> maxspeed -> turn-restriction relations. Names and address search are a later
stage (they need the city model).
"""
import array
import collections
import math
import sys

from .graph import Graph, Shape, shared
from .mapfile import SEGINFO_FIELDS

# private tag put on collected ways: the catalog of the extract they came from
COUNTRY_TAG = "openlion:country"

FT = 0.3048

# highway=* -> (base rank, class). road_rank() refines rank by speed (urban vs rural);
# class follows the factory meaning (0 freeway, 1 highway, 2 arterial, 3 street,
# 6 ramp, 7 track, 8 busway). Base ranks calibrated on inner Melbourne: factory km per
# rank 4/3/2/1 = 19/49/196/68; OSM motorway/trunk/primary+secondary/tertiary = 35/41/210/145.
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
    for unit in ("km/h", "kmh", "kph"):
        if v.endswith(unit):
            v = v[:-len(unit)].strip()
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


def road_rank(tags):
    """Rank 0-4 (drives the mg layers). Calibrated twice: inner Melbourne (urban
    trunk/primary = 3/2) and all of Australia (factory rank 4 = ~40k km, which needs
    rural trunk roads): motorway 4; trunk 4 if >= 80 km/h or untagged, else 3;
    primary 3 if >= 80 km/h, else 2; secondary 2; tertiary 1; links one below."""
    hw = tags["highway"]
    rank, _ = HIGHWAY[hw]
    if rank is None:
        return LINK_RANK[hw]
    sp = parse_speed(tags.get("maxspeed"))
    if hw == "trunk":
        return 4 if (sp is None or sp >= 80) else 3
    if hw == "primary":
        return 3 if (sp is not None and sp >= 80) else 2
    return rank


LANDMARK_ROUNDABOUT = 2   # .si landmark value on roundabouts (docs/12_osm_import.md)


def seginfo_fields(tags):
    hw = tags["highway"]
    _, cls = HIGHWAY[hw]
    rank = road_rank(tags)
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
    # The factory data marks roundabout segments with landmark = 2. Without it the
    # unit treats a roundabout as ordinary turns ("turn left, then turn left").
    if tags.get("junction") == "roundabout":
        f["landmark"] = LANDMARK_ROUNDABOUT
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


# tags the build reads after way_ok() has passed; the rest are dropped at read time
KEEP_TAGS = ("highway", "oneway", "junction", "maxspeed", "surface", "toll", "name", "ref")


class WayPoints:
    """A way's nodes as two compact arrays (node ids, interleaved x/y in deg * 1e5).
    Behaves like the list of (node_id, x, y) tuples it replaces, at ~16 bytes per
    point instead of ~250."""
    __slots__ = ("ids", "xy")

    def __init__(self, ids, xy):
        self.ids = ids      # array('q')
        self.xy = xy        # array('i'), 2 per point

    def __len__(self):
        return len(self.ids)

    def __getitem__(self, k):
        if isinstance(k, slice):
            return [self[i] for i in range(*k.indices(len(self.ids)))]
        if k < 0:
            k += len(self.ids)
        return self.ids[k], self.xy[2 * k], self.xy[2 * k + 1]

    def __iter__(self):
        xy = self.xy
        for k, nid in enumerate(self.ids):
            yield nid, xy[2 * k], xy[2 * k + 1]

    def __reduce__(self):
        return WayPoints, (self.ids, self.xy)


def compact_tags(tags):
    """Keep only KEEP_TAGS; intern the strings (road types and names repeat a lot)."""
    out = {}
    for k in KEEP_TAGS:
        v = tags.get(k)
        if v is not None:
            out[k] = sys.intern(v)
    return out


class _Collector:
    def __init__(self, bbox):
        self.bbox = bbox
        self.ways = []          # (way_id, tags dict (KEEP_TAGS), WayPoints)
        self.restrictions = []  # (type, from_way, via_node, to_way)

    def inside(self, x, y):
        b = self.bbox
        return b is None or (b[0] <= x <= b[2] and b[1] <= y <= b[3])


def read(pbf_path, bbox=None, progress=None, node_index="flex_mem"):
    """Collect routable ways (any node inside bbox, in degrees*1e5) and restrictions.
    node_index: osmium location storage ("flex_mem", or "sparse_file_array,<path>"
    to keep the node locations on disk; see resources.py)."""
    import osmium
    c = _Collector(bbox)
    import osmium.filter
    # nodes are read (in C++) only to fill the location cache; the filters keep them,
    # and untagged / non-road objects, away from Python
    fp = (osmium.FileProcessor(pbf_path, osmium.osm.NODE | osmium.osm.WAY | osmium.osm.RELATION)
          .with_locations(node_index)
          .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY | osmium.osm.RELATION))
          .with_filter(osmium.filter.KeyFilter("highway", "type")))
    n = 0
    for obj in fp:
        if obj.is_way():
            tags = {t.k: t.v for t in obj.tags}
            if not way_ok(tags):
                continue
            ids, xy = array.array("q"), array.array("i")
            hit, ok = False, True
            for nd in obj.nodes:
                if not nd.location.valid():
                    ok = False
                    break
                x, y = round(nd.location.lon * 1e5), round(nd.location.lat * 1e5)
                hit |= c.inside(x, y)
                ids.append(nd.ref)
                xy.append(x)
                xy.append(y)
            if ok and hit and len(ids) >= 2:
                c.ways.append((obj.id, compact_tags(tags), WayPoints(ids, xy)))
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
                c.restrictions.append((sys.intern(r), frm, via, to))
        n += 1
        if progress and n % 2_000_000 == 0:
            progress(n, len(c.ways))
    return c


def merge(collected):
    """[(catalog, collector)] -> one collector; ways tagged with their catalog.
    A way in two extracts (they overlap at the edges) is kept once."""
    out = _Collector(None)
    seen = set()
    for catalog, c in collected:
        for wid, tags, pts in c.ways:
            if wid in seen:
                continue
            seen.add(wid)
            tags[COUNTRY_TAG] = catalog
            out.ways.append((wid, tags, pts))
        out.restrictions += c.restrictions
    return out


def junction_nodes(ways, buckets=8):
    """Node ids where a way must be split: used twice or more (by one way or several,
    a way's two ends counting double). Counted in hash buckets, so at most 1/buckets
    of the ~25M node references are Python objects at a time."""
    out = set()
    for b in range(buckets):
        seen = set()
        for _, _, pts in ways:
            ids = pts.ids
            for k, nid in enumerate(ids):
                if nid % buckets != b:
                    continue
                if nid in seen or k == 0 or k == len(ids) - 1:
                    out.add(nid)
                seen.add(nid)
    return out


def build_graph(c, max_shape_points=500, consume=False, speed_fill=None, sign_fill=None):
    """Collected OSM data -> (Graph, seginfo record list, stats).
    consume=True empties c.ways as it goes, so the raw ways and the finished graph are
    never in memory together (the pipeline takes what else it needs from them first).
    speed_fill: optional callable(xy, highway) -> km/h or None, asked for road pieces OSM has
    no maxspeed for (gov_speed.Index.lookup). Service roads and ramps are never asked:
    they run beside bigger roads and would pick up those roads' limits.
    sign_fill: optional callable(graph, highway per segment, stats) -> (segment, km/h) pairs, run
    once the roads are joined up (gov_speed.sign_fill: limits walked on from speed signs); only
    segments still without a limit are filled."""
    junction = junction_nodes(c.ways)
    in_restriction = {w for _, frm, _, to in c.restrictions for w in (frm, to)}
    g = Graph()
    g.seg_label = []      # OSM (name, ref) per segment, for osm_names
    g.seg_country = []    # catalog (countries.Country.catalog) per segment
    nodes = {}
    si_index, si_records = {}, []
    stats = collections.Counter()
    piece_of = collections.defaultdict(list)   # osm way id -> [(seg, a_osm, b_osm)]
    seg_hw = [] if sign_fill is not None else None

    def node(nid, x, y):
        if nid not in nodes:
            nodes[nid] = g.add_node((x, y))
        return nodes[nid]

    for i in range(len(c.ways)):
        wid, tags, pts = c.ways[i]
        if consume:
            c.ways[i] = None
        rec = pack_fields(seginfo_fields(tags))
        ow = oneway_dir(tags)
        speed = parse_speed(tags.get("maxspeed"))
        label = shared((tags.get("name"), tags.get("ref")))
        # split points: junctions, plus extra cuts to bound shape length / avoid self-loops
        cuts = [0]
        ids = pts.ids
        for k in range(1, len(pts) - 1):
            if ids[k] in junction or k - cuts[-1] >= max_shape_points:
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
            piece_speed = speed
            hw = tags["highway"]
            if speed is None and speed_fill is not None and hw != "service" and not hw.endswith("_link"):
                piece_speed = speed_fill(xy, hw)
                stats["speed limit from government data"] += bool(piece_speed)
            adas = shared({"content": 2, "speed_kmh": piece_speed, "lanes": None, "n_limits": 0}) if piece_speed else None
            s = g.add_segment(a, b, length_ft(xy), si_index[v], None, Shape(shape) if shape else None, [], adas)
            g.seg_label.append(label)
            g.seg_country.append(tags.get(COUNTRY_TAG, 0))
            if seg_hw is not None:
                seg_hw.append(hw)
            if wid in in_restriction:
                piece_of[wid].append((s, seq[0][0], seq[-1][0]))
            stats["segments"] += 1
            stats["with speed limit"] += bool(piece_speed)

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

    if sign_fill is not None:
        for s, v in list(sign_fill(g, seg_hw, stats)):
            if g.seg_adas[s] is None:
                g.seg_adas[s] = shared({"content": 2, "speed_kmh": v, "lanes": None, "n_limits": 0})
                stats["speed limit from government speed signs"] += 1
                stats["with speed limit"] += 1
        seg_hw = None

    # The format allows at most 8 arms per node. Split bigger junctions: move arms
    # beyond 7 to a twin node at the same spot, joined by a 1 ft two-way connector.
    for n in range(g.n_nodes):
        while len(g.node_arms[n]) > 8:
            arms = g.node_arms[n]
            keep, move = arms[:7], arms[7:]
            twin = g.add_node(g.node_xy[n])
            # connector runs from the lower id (the format orders equal coordinates by index)
            rank_seg = max(move + keep, key=lambda s: si_records[g.seg_info[s]] & 7)
            v = (si_records[g.seg_info[rank_seg]] & ~(3 << 8)) | (3 << 8)   # same class, two-way
            if v not in si_index:
                si_index[v] = len(si_records)
                si_records.append(v)
            con = g.add_segment(n, twin, 1, si_index[v], None, None, [], None)
            g.seg_label.append(shared((None, None)))
            g.seg_country.append(g.seg_country[keep[0]])
            for s in move:
                if g.seg_a[s] == n:
                    g.seg_a[s] = twin
                if g.seg_b[s] == n:
                    g.seg_b[s] = twin
            g.node_arms[n] = keep + [con]
            g.node_arms[twin] = move + [con]
            for m in (n, twin):
                g.node_arms[m].sort(key=lambda s, m=m: (first_step(m, s) if g.seg_a[s] != g.seg_b[s] else 0, s))
            stats["junctions split (>8 arms)"] += 1

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
        g.seg_restr[s] = shared(tuple(masks)) if any(masks) else None

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
        if ts[0] not in arms or fs[0] not in arms:
            stats["restrictions skipped (split junction)"] += 1
            continue
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
    if consume:
        c.ways = []
    stats["nodes"] = g.n_nodes
    stats["seginfo records"] = len(si_records)
    return g, si_records, stats
