"""Layout-independent road graph: what the tiler writes and what an OSM import builds.

Conventions (all verified on the factory database, see report 11):
- Every segment is oriented left -> right with left = the end with the smaller
  (lon, lat). In a parcel this is the lower node index (positions are sorted by
  (lon, lat)), and tile-edge copies agree, so orientation never changes with tiling.
- A node's arms are ordered clockwise by bearing, starting nearest north. Turn
  restrictions and restr_left/right masks refer to arms in this order.
- Turn restriction = start segment + the junction it enters (one of its ends) +
  the arm index taken at each successive junction.
"""
import collections

from . import mapfile, mapwrite


class Graph:
    def __init__(self):
        self.node_xy = []        # (lon, lat) * 1e5
        self.node_arms = []      # [seg ids] in arm order
        self.node_info = []      # dict: rank, depth, slot_flags, extra (long-node bytes)
        self.seg_a = []          # left node id
        self.seg_b = []          # right node id
        self.seg_len = []        # feet
        self.seg_info = []       # index into the .si table
        self.seg_restr = []      # (left_mask, right_mask) or None
        self.seg_shape = []      # absolute intermediate points left -> right, or None
        self.seg_names = []      # [(kind, nameinfo, road_rel, range_bytes)]
        self.seg_adas = []       # dict (speed etc.) or None
        self.turns = []          # dict: seg, via (node id), arms, direction, unused_arms

    def add_node(self, xy, info=None):
        self.node_xy.append(tuple(xy))
        self.node_arms.append([])
        self.node_info.append(info or {})
        return len(self.node_xy) - 1

    def add_segment(self, a, b, length_ft, seginfo, restr=None, shape=None, names=(), adas=None):
        if self.node_xy[a] > self.node_xy[b]:
            raise ValueError("segment must run from the smaller (lon, lat) end")
        self.seg_a.append(a)
        self.seg_b.append(b)
        self.seg_len.append(length_ft)
        self.seg_info.append(seginfo)
        self.seg_restr.append(restr)
        self.seg_shape.append(shape)
        self.seg_names.append(list(names))
        self.seg_adas.append(adas)
        return len(self.seg_a) - 1

    @property
    def n_nodes(self):
        return len(self.node_xy)

    @property
    def n_segs(self):
        return len(self.seg_a)


# ---- extraction from a factory map file -------------------------------------

def extract(map_bytes: bytes) -> Graph:
    """Build the global graph from a map file (mp0 or an mg layer)."""
    m = mapfile.MapFile(map_bytes)
    g = Graph()
    local = []           # per parcel: decoded pieces
    gid = []             # per parcel: local node index -> global id (None for continuation)
    border_key = {}      # (inner_xy, outer_xy, length) -> (parcel, local seg)

    for pi in range(m.pcl_cnt):
        raw = m.parcel(pi)
        pc = mapwrite.ParcelContainer.decode(raw)
        f = lambda n: pc.fields[pc.idx(n)]
        P = [p for x, y, d in mapwrite.decode_positions(pc.get("pos_list"))
             for p in _expand(x, y, d)]
        X = [p for x, y, d in mapwrite.decode_positions(pc.get("exc_pos_list"))
             for p in _expand(x, y, d)]
        nodes = mapwrite.decode_nodes(pc.get("node_list"), f("node_cnt"))
        segs = mapwrite.decode_segments(pc.get("seg_list"), f("seg_cnt"))
        shapes = mapwrite.decode_shapes(pc.get("shape_list"), f("shape_cnt")) or []
        adas = mapwrite.decode_adas(pc.get("adas_list"), f("adas_cnt"))
        turns = mapwrite.decode_turn_res(pc.get("turn_res_list"))
        names = collections.defaultdict(list)
        for kind, size in (("abs_addr", 7), ("short_addr", 11), ("long_addr", 13)):
            for rec in mapwrite.decode_fixed(pc.get(kind + "_list"), size):
                seg = int.from_bytes(rec[0:2], "little")
                names[seg].append((kind, rec[2], int.from_bytes(rec[3:7], "little"), rec[7:]))
        ids = []
        for i, n in enumerate(nodes):
            if n.get("continuation"):
                ids.append(None)
                continue
            if n["long"]:
                c = n["b0"] & 15
                info = {"long": True, "rank": n["info"][1] & 7, "depth": n["info"][1] >> 3,
                        "extra": n["info"][2], "slot_flags": [s >> 12 for s in n["slots"][:c]]}
            else:
                c = n["b0"] & 15
                info = {"long": False, "rank": n["info"][0] & 7, "depth": n["info"][0] >> 3,
                        "slot_flags": [s >> 12 for s in n["slots"][:c]]}
            ids.append(g.add_node(P[i], info))
        gid.append(ids)
        ends = collections.defaultdict(list)
        for i, n in enumerate(nodes):
            if n.get("continuation"):
                continue
            for s in n["slots"][:n["b0"] & 15]:
                ends[s & 0xFFF].append(i)
        local.append((P, X, nodes, segs, shapes, adas, turns, names, ends))
        for s, sg in enumerate(segs):
            e = ends.get(s, [])
            if len(e) == 1:
                bi = sg["right_node"] if sg["right_node"] is not None else sg["left_node"]
                # several parallel segments can share coordinates and length (grade
                # separations), so keep every candidate and pair them one-to-one
                border_key.setdefault((P[e[0]], X[bi], sg["length_ft"]), []).append((pi, s))

    # global segments
    seg_gid = [dict() for _ in range(m.pcl_cnt)]
    dangling = {}
    for pi, (P, X, nodes, segs, shapes, adas, turns, names, ends) in enumerate(local):
        adas_by_seg = {a["seg"]: a for a in adas}
        for s, sg in enumerate(segs):
            if s in seg_gid[pi]:
                continue
            e = sorted(ends.get(s, []))
            if len(e) == 2:
                a, b = gid[pi][e[0]], gid[pi][e[1]]
                copies = [(pi, s)]
            else:
                bi = sg["right_node"] if sg["right_node"] is not None else sg["left_node"]
                cands = [c for c in border_key.get((X[bi], P[e[0]], sg["length_ft"]), [])
                         if c[1] not in seg_gid[c[0]]]
                if len(cands) > 1:
                    # prefer a partner with the same attributes and the mirrored marker
                    def score(c):
                        o = local[c[0]][3][c[1]]
                        return (o["seginfo"] == sg["seginfo"], o["restr"] == sg["restr"],
                                (o["left_node"] is None) == (sg["left_node"] is not None))
                    cands.sort(key=score, reverse=True)
                partner = cands[0] if cands else None
                inner = gid[pi][e[0]]
                if partner:
                    qi, t = partner
                    outer = gid[qi][local[qi][8][t][0]]
                    copies = [(pi, s), (qi, t)]
                else:
                    # mg layers: a stub whose far end is not a node of this layer and lies
                    # in another parcel; that end exists only as a border point
                    outer = dangling.get(X[bi])
                    if outer is None:
                        outer = dangling[X[bi]] = g.add_node(X[bi], {"dangling": True})
                    copies = [(pi, s)]
                a, b = (outer, inner) if sg["left_node"] is not None else (inner, outer)
            # attributes: shape / names / adas may sit on either copy
            shape = nm = ad = None
            for ci, cs in copies:
                cP, cX, _, csegs, cshapes, cadas, _, cnames, cends = local[ci]
                if cs < len(cshapes) and shape is None:
                    shape = _abs_shape(cP, cX, csegs[cs], cends.get(cs, []), cshapes[cs])
                if nm is None and cnames.get(cs):
                    nm = cnames[cs]
                if ad is None:
                    ad = {x["seg"]: x for x in cadas}.get(cs)
            ad = dict(ad) if ad else None
            if ad:
                ad.pop("seg")
            new = g.add_segment(a, b, sg["length_ft"], sg["seginfo"], sg["restr"], shape, nm or [], ad)
            for ci, cs in copies:
                seg_gid[ci][cs] = new

    # arms (keep factory arm order, which is the bearing order)
    for pi, (P, X, nodes, segs, *_rest) in enumerate(local):
        for i, n in enumerate(nodes):
            if n.get("continuation"):
                continue
            g.node_arms[gid[pi][i]] = [seg_gid[pi][s & 0xFFF] for s in n["slots"][:n["b0"] & 15]]
    if dangling:
        far = collections.defaultdict(list)
        for s in range(g.n_segs):
            for end in (g.seg_a[s], g.seg_b[s]):
                if g.node_info[end].get("dangling"):
                    far[end].append(s)
        for nid, ss in far.items():
            g.node_arms[nid] = ss

    # turn restrictions: start segment + entered junction. A restriction whose start
    # segment crosses a tile edge is stored in both parcels; keep one.
    seen = set()
    for pi, (P, X, nodes, segs, shapes, adas, turns, names, ends) in enumerate(local):
        for t in turns:
            s = seg_gid[pi][t["seg"]]
            via = g.seg_b[s] if t["hand"] else g.seg_a[s]
            key = (s, via, tuple(t["arms"]), t["direction"])
            if key in seen:
                continue
            seen.add(key)
            g.turns.append({"seg": s, "via": via, "arms": t["arms"], "direction": t["direction"]})
    return g


def _expand(x, y, d):
    pts = [(x, y)]
    for a, b in d:
        x += a
        y += b
        pts.append((x, y))
    return pts


def _abs_shape(P, X, sg, e, deltas):
    if len(e) == 2 or sg["left_node"] is None:
        x, y = P[min(e)]
    else:
        x, y = X[sg["left_node"]]
    out = []
    for dx, dy in deltas:
        x += dx
        y += dy
        out.append((x, y))
    return out


def summary(g: Graph):
    """Layout-independent fingerprint for comparing two graphs."""
    seg = collections.Counter()
    for s in range(g.n_segs):
        seg[(g.node_xy[g.seg_a[s]], g.node_xy[g.seg_b[s]], g.seg_len[s], g.seg_info[s], g.seg_restr[s],
             tuple(g.seg_shape[s]) if g.seg_shape[s] is not None else None,
             tuple(sorted((k, n, r, bytes(x)) for k, n, r, x in g.seg_names[s])),
             tuple(sorted(g.seg_adas[s].items())) if g.seg_adas[s] else None)] += 1
    arms = collections.Counter()
    for n in range(g.n_nodes):
        segkeys = tuple((g.node_xy[g.seg_a[s]], g.node_xy[g.seg_b[s]], g.seg_len[s]) for s in g.node_arms[n])
        if g.node_info[n].get("dangling"):
            # stub ends that are not nodes of this layer: grouping depends on tiling
            for k in segkeys:
                arms[(g.node_xy[n], "dangling end", k)] += 1
        else:
            arms[(g.node_xy[n], segkeys, g.node_info[n].get("rank"))] += 1
    turns = collections.Counter()
    for t in g.turns:
        s = t["seg"]
        turns[((g.node_xy[g.seg_a[s]], g.node_xy[g.seg_b[s]], g.seg_len[s]), g.node_xy[t["via"]],
               tuple(t["arms"]), t["direction"])] += 1
    return {"segments": seg, "nodes": arms, "turns": turns}
