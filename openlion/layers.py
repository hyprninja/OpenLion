"""Derive the routing layers mg1..mg4 from the mp0 graph (report 08).

Layer k = every segment with seginfo rank >= k ("core"), plus every segment attached
to a node of a core segment ("stubs"). Nodes of core segments are nodes of the layer
and keep all their arms in the same order; the far end of a stub that is not a core
node becomes a dangling end (written as a border point). Node rank = the highest
layer in which the node is a core node. Shapes, names and speeds are dropped.
"""
from .graph import Graph


def node_ranks(g, si_records):
    """Highest layer (0..4) in which each node is an endpoint of a core segment."""
    rank = [0] * g.n_nodes
    for s in range(g.n_segs):
        r = si_records[g.seg_info[s]] & 7
        for n in (g.seg_a[s], g.seg_b[s]):
            if r > rank[n]:
                rank[n] = r
    return rank


def derive(g, k, si_records, turn_filter="all_segments_in_layer"):
    """Return the Graph for layer k (1..4)."""
    rank = node_ranks(g, si_records)
    core_node = [r >= k for r in rank]
    keep = [False] * g.n_segs
    for s in range(g.n_segs):
        if (si_records[g.seg_info[s]] & 7) >= k or core_node[g.seg_a[s]] or core_node[g.seg_b[s]]:
            keep[s] = True
    out = Graph()
    nmap = {}

    def node(n):
        if n not in nmap:
            info = dict(g.node_info[n]) if core_node[n] else {"dangling": True}
            if core_node[n]:
                info["rank"] = rank[n]
            nmap[n] = out.add_node(g.node_xy[n], info)
        return nmap[n]

    smap = {}
    for s in range(g.n_segs):
        if keep[s]:
            smap[s] = out.add_segment(node(g.seg_a[s]), node(g.seg_b[s]), g.seg_len[s], g.seg_info[s],
                                      g.seg_restr[s], None, [], None)
    for n, m in nmap.items():
        if core_node[n]:
            out.node_arms[m] = [smap[s] for s in g.node_arms[n]]
        else:
            out.node_arms[m] = [smap[s] for s in g.node_arms[n] if s in smap]
    for t in g.turns:
        if t["seg"] in smap and core_node[t["via"]]:
            out.turns.append({"seg": smap[t["seg"]], "via": nmap[t["via"]], "arms": t["arms"],
                              "direction": t["direction"]})
    return out
