"""Tile a Graph into a map file (mp0 or an mg layer): kd-tree, parcels, lists.

Factory rules this follows (report 11):
- kd-tree: nodes stored in pre-order; parcels numbered in leaf order; kd_info bit 0 =
  cut axis (0 lon, 1 lat), bits 2/3 = lo empty/parcel, bits 6/7 = hi empty/parcel.
  A point on the cut belongs to the low side (value <= edge).
- A node lives in the parcel whose cell contains it. Every segment touching a parcel's
  node is written there; a segment crossing cells is written in both, each copy with
  one node end and one border point (exc_pos). Both copies carry shape, names, speed.
- In a parcel: positions sorted by (lon, lat) (node i = position i; a long node uses
  two indices with the position repeated); shaped segments first (shape k = seg k).
- Segment ends that are not nodes of this layer (mg stubs) are always border points.
- A turn restriction is stored in every parcel holding a copy of its start segment.
"""
import struct

from . import mapwrite, schema

# Factory id order of the stats table in the mp0 parcel trailer: schema.STATS_IDS

# Factory envelope: largest parcel 38,880 B raw, 854 segments, 588 node records.
DEFAULT_LIMITS = {"max_nodes": 450, "max_segs": 650, "max_bytes": 32000}


# ---- kd-tree ---------------------------------------------------------------

def build_kd(g, node_ids, limits=None):
    """Split node_ids into cells. Returns (kd_nodes, leaves, depth); leaves is a list
    of node-id lists in parcel order; kd_nodes are (edge, lo, hi, info) tuples."""
    limits = limits or DEFAULT_LIMITS
    xy = g.node_xy
    kd, leaves = [], []
    max_depth = [0]

    def segs_of(ids):
        s = set()
        for n in ids:
            s.update(g.node_arms[n])
        return s

    def records(ids):
        return sum(2 if len(g.node_arms[n]) >= 5 else 1 for n in ids)

    def est_bytes(rec, segs):
        """Upper-bound estimate of the encoded parcel size."""
        b = schema.active().offs_len + 14 * rec + 13 * len(segs)
        for s in segs:
            sh = g.seg_shape[s]
            if sh is not None:
                b += 2 + 4 * len(sh)
            b += 13 * len(g.seg_names[s]) + (6 if g.seg_adas[s] else 0)
        return b

    def small(ids):
        rec = records(ids)
        if rec > limits["max_nodes"]:
            return False
        segs = segs_of(ids)
        return len(segs) <= limits["max_segs"] and est_bytes(rec, segs) <= limits["max_bytes"]

    def cut(ids, box):
        w, h = box[2] - box[0], box[3] - box[1]
        for ax in ((0, 1) if w >= h else (1, 0)):
            vals = sorted(xy[n][ax] for n in ids)
            k = len(vals) // 2
            # largest edge that leaves a non-empty high side, closest to the median
            for j in list(range(k - 1, -1, -1)) + list(range(k, len(vals) - 1)):
                if vals[j] < vals[j + 1]:
                    return ax, vals[j]
        return None

    def node(ids, box, depth):
        """Returns ('parcel', idx) / ('empty', 0) / ('kd', idx)."""
        max_depth[0] = max(max_depth[0], depth)
        if not ids:
            return ("empty", 0)
        c = None if small(ids) else cut(ids, box)
        if c is None:
            leaves.append(ids)
            return ("parcel", len(leaves) - 1)
        ax, edge = c
        me = len(kd)
        kd.append(None)
        lo = [n for n in ids if xy[n][ax] <= edge]
        hi = [n for n in ids if xy[n][ax] > edge]
        lbox, hbox = list(box), list(box)
        lbox[2 + ax], hbox[ax] = edge, edge
        lt, li = node(lo, lbox, depth + 1)
        ht, hi_ = node(hi, hbox, depth + 1)
        info = ax
        info |= 4 if lt == "empty" else (8 if lt == "parcel" else 0)
        info |= 64 if ht == "empty" else (128 if ht == "parcel" else 0)
        kd[me] = (edge, li, hi_, info)
        return ("kd", me)

    xs = [xy[n][0] for n in node_ids]
    ys = [xy[n][1] for n in node_ids]
    top = node(list(node_ids), [min(xs), min(ys), max(xs), max(ys)], 0)
    if top[0] != "kd":
        # a single parcel: the format needs at least one kd node
        kd = [(max(xs), 0, 0, 8 | 64)]
    return kd, leaves, max_depth[0]


# ---- parcels ---------------------------------------------------------------

def build_parcel(g, cell_nodes, layer, is_node):
    """Encode one parcel. cell_nodes: global node ids in this cell."""
    cell = set(cell_nodes)
    order = sorted(cell_nodes, key=lambda n: (g.node_xy[n], n))
    # local node indices (long nodes take two)
    local, positions = {}, []
    for n in order:
        local[n] = len(positions)
        positions.append(g.node_xy[n])
        if len(g.node_arms[n]) >= 5:
            positions.append(g.node_xy[n])
    # segments: every segment touching a node of the cell
    segs = sorted({s for n in order for s in g.node_arms[n]})
    has_shapes = layer == 0
    shaped = [s for s in segs if has_shapes and g.seg_shape[s] is not None]
    plain = [s for s in segs if not (has_shapes and g.seg_shape[s] is not None)]
    segs = shaped + plain
    lseg = {s: i for i, s in enumerate(segs)}
    if len(segs) > 4095:
        raise ValueError("parcel has more than 4095 segments")
    # border points: segment ends that are not nodes of this parcel
    exc, exc_idx = [], {}

    def border(n):
        if n not in exc_idx:
            exc_idx[n] = len(exc)
            exc.append(g.node_xy[n])
        return exc_idx[n]

    sch = schema.active()
    seg_recs = []
    for s in segs:
        a, b = g.seg_a[s], g.seg_b[s]
        ln = border(a) if not (a in cell and is_node[a]) else None
        rn = border(b) if not (b in cell and is_node[b]) else None
        seg_recs.append({"seginfo": g.seg_info[s], "restr": g.seg_restr[s],
                         "left_node": ln, "right_node": rn, "length_ft": g.seg_len[s]})
    # node records
    node_recs = []
    for n in order:
        arms = g.node_arms[n]
        hands = 0
        seen_loop = set()
        for k, s in enumerate(arms):
            right = g.seg_b[s] == n and (g.seg_a[s] != n or s in seen_loop)
            if g.seg_a[s] == n == g.seg_b[s]:
                seen_loop.add(s)
            hands |= (1 << k) if right else 0
        info = g.node_info[n]
        flags = info.get("slot_flags") or [0] * len(arms)
        rec = mapwrite.make_node([(lseg[s], fl) for s, fl in zip(arms, flags)], hands,
                                 info.get("rank", layer), info.get("depth", 0))
        if rec["long"]:
            rec["info"] = bytes([hands & 0xFF, (info.get("rank", layer) & 7) | ((info.get("depth", 0) & 31) << 3),
                                 info.get("extra", 0)])
        node_recs.append(rec)
        if rec["long"]:
            node_recs.append({"continuation": True})
    # shapes (mp0 only): deltas from the left end
    shape_list = None
    n_shape_pts = 0
    if has_shapes:
        shape_list = []
        for s in shaped:
            x, y = g.node_xy[g.seg_a[s]]
            d = []
            for px, py in g.seg_shape[s]:
                d.append((px - x, py - y))
                x, y = px, py
            n_shape_pts += len(d)
            shape_list.append(d)
    # names, speeds (mp0 only)
    names = {"abs_addr": [], "short_addr": [], "long_addr": []}
    adas = []
    if layer == 0:
        for s in segs:
            for kind, ni, road, rng in g.seg_names[s]:
                names[kind].append(mapwrite.encode_addr(lseg[s], road, ni, bytes(rng)))
            if g.seg_adas[s]:
                a = dict(g.seg_adas[s])
                a["seg"] = lseg[s]
                adas.append(a)
    if sch.compact and layer == 0:
        # MY10: shape points and road links go into the segment records (no nameinfo);
        # the shape and address lists stay empty but keep their counts
        for i, s in enumerate(segs):
            seg_recs[i]["shape"] = shape_list[i] if i < len(shaped) else []
            seg_recs[i]["names"] = [(kind, road, bytes(rng)) for kind, _ni, road, rng in g.seg_names[s]]
    # turn restrictions: every parcel holding a copy of the start segment
    turns = []
    for t in g.turns_by_seg.get_all(segs) if hasattr(g, "turns_by_seg") else []:
        s = t["seg"]
        turns.append({"seg": lseg[s], "direction": t["direction"], "arms": t["arms"],
                      "hand": 0 if t["via"] == g.seg_a[s] else 1})

    # assemble the container
    IDX = sch.idx
    fields = [0] * sch.n_fields
    trailer = [0] * sch.trailer_len
    fixed = set()
    if layer != 0:
        # factory mg parcels: junction view lists out of order (garbage there is zeroed)
        if sch.compact:
            fields[IDX["junction_view_image_name_size"]] = 256
        else:
            fields[IDX["junction_view_count"]] = 256
        fixed = {IDX["junction_view_list"], IDX["junction_view_image_name_list"]}
    pc = mapwrite.ParcelContainer(fields, trailer, {}, fixed, sch)
    pc.set("pos_list", mapwrite.encode_positions(mapwrite.blocks_from_points(positions)), len(positions))
    pc.set("exc_pos_list", mapwrite.encode_positions(mapwrite.blocks_from_points(exc)), len(exc))
    pc.set("node_list", mapwrite.encode_nodes(node_recs), len(positions))
    pc.set("seg_list", mapwrite.encode_segments(seg_recs), len(segs))
    for kind, recs in names.items():
        pc.set(kind + "_list", b"" if sch.compact else b"".join(recs), len(recs))
    pc.set("adas_list", mapwrite.encode_adas(adas), len(adas))
    if shape_list is not None:
        blob = mapwrite.encode_shapes(shape_list)
        pc.set("shape_list", b"" if sch.compact else blob, len(shape_list))
        pc.fields[IDX["shape_size"]] = len(blob)    # compact: the size MY12 would store
        # trailer stats table: (id, a, b) per stats id, then flags, no_of_segments, ...
        n_stats = len(sch.stats_ids)
        for k, sid in enumerate(sch.stats_ids):
            pc.trailer[2 + 3 * k] = sid
            if sid == 8:
                pc.trailer[3 + 3 * k] = len(shape_list)
                pc.trailer[4 + 3 * k] = n_shape_pts
        pc.trailer[2 + 3 * n_stats + 1] = len(segs) - len(shape_list)
    pc.set("turn_res_list", mapwrite.encode_turn_res(turns), len(turns))
    raw = pc.encode()
    if len(raw) > 0xFFFF:
        raise ValueError(f"parcel too large ({len(raw)} bytes)")
    return raw


class _TurnIndex:
    def __init__(self, turns):
        self.by = {}
        for t in turns:
            self.by.setdefault(t["seg"], []).append(t)

    def get_all(self, segs):
        out = []
        for s in segs:
            out.extend(self.by.get(s, []))
        return out


def tile(g, layer, template_header, limits=None):
    """Graph -> complete map file bytes."""
    is_node = [not info.get("dangling") for info in g.node_info]
    node_ids = [n for n in range(g.n_nodes) if is_node[n]]
    g.turns_by_seg = _TurnIndex(g.turns)
    kd, leaves, depth = build_kd(g, node_ids, limits)
    zipped, sizes = [], []
    for cell in leaves:
        raw = build_parcel(g, cell, layer, is_node)
        zipped.append(mapwrite.compress_parcel(raw))
        sizes.append(len(raw))
    xs = [p[0] for p in g.node_xy]
    ys = [p[1] for p in g.node_xy]
    hdr = {"min_long": min(xs), "min_lat": min(ys), "max_long": max(xs), "max_lat": max(ys),
           "layer": layer, "kd_depth": depth}
    return mapwrite.encode_mapfile(template_header, hdr, kd, zipped, sizes)
