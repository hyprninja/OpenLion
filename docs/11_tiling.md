# Phase 6b: Tiling, kd-tree and layer derivation

Date: 2026-11-04. Code: `openlion/graph.py` (layout-independent graph + extractor), `openlion/tiler.py` (kd-tree + parcels), `openlion/layers.py` (mg1–mg4 from mp0). Tests: `tests/retile.py`.

## Result

The converter can now take a road graph in any layout and write a complete map file: kd-tree, parcels, nodes, segments, border points, shapes, names, speed limits and turn restrictions.

**Re-tile test:** extract the graph from a factory map file, tile it again with the new builder, read the new file back, and compare segments, junction arm lists and turn restrictions as layout-independent multisets:

| Layer | Graph (nodes / segments / turns) | Factory parcels | Re-tiled parcels, kd depth | Identical after re-tiling |
|---|---|---|---|---|
| mg4 | 78,715 / 84,867 / 1,927 | 261 | 200, 8 | segments, nodes, turns: yes |
| mg3 | 189,262 / 207,196 / 6,699 | 595 | 512, 9 | yes |
| mg2 | 501,112 / 553,970 / 13,183 | 1,479 | 1,052, 11 | yes |
| mg1 | 750,533 / 839,843 / 14,745 | 2,128 | 2,048, 11 | yes |
| mp0 | 1,987,294 / 2,420,542 / 14,921 (with shapes, names, speeds) | 6,819 | 8,642, 16 | yes |

Every re-tiled parcel passes the reader's framing checks and has sorted positions. mp0 gets more parcels than the factory because the tiler caps parcels at 32 KB raw, below the factory's 38.9 KB maximum. Run time on mp0: about 1 minute to extract, 1 minute to tile.

**Layer derivation:** `layers.derive()` applied to the mp0 graph reproduces the factory overview layers.

| Layer | Segments | Nodes (incl. rank) | Turn restrictions |
|---|---|---|---|
| mg4 | 84,867 identical | 81,063 identical | 1,911 of 1,927 (16 factory-only) |
| mg3 | 207,196 identical | 196,021 identical | 6,646 of 6,699 (53 factory-only) |
| mg2 | 553,970 identical | 518,578 identical | 13,152 of 13,183 (31 factory-only) |
| mg1 | 839,843 identical | 777,761 identical | 14,731 of 14,745 (14 factory-only) |

No derived restriction is wrong. A handful (0.1–0.8%) exist only in the factory overview layers and have no mp0 counterpart; their origin is open.

Two extraction details found on the way:
- Parallel tile-edge segments can share endpoint coordinates *and* length (grade separations, e.g. one pair in Sydney). Copies must be paired one-to-one, with ties broken on attributes.
- In mg layers a stub crossing a tile edge has no partner copy: its far end isn't a node of the layer.

## Factory rules found (and followed by the tiler)

### kd-tree (`pcl_dir`)
- Nodes are stored in **pre-order**; parcels are numbered in **leaf order** (each parcel exactly once).
- `kd_info` uses only bit 0 (cut axis: 0 = longitude, 1 = latitude), bits 2/3 (low side empty / is a parcel) and bits 6/7 (high side empty / is a parcel). The factory has 133 empty leaves in mp0.
- A point exactly on a cut belongs to the **low** side (`value <= edge`). 727 of 727 such nodes in mp0.
- `kd_depth` = the deepest level, counting the root as 1.
- The cut axis is chosen per node (it alternates 78% of the time). The factory's split threshold isn't a single count or size. The tiler uses longer-side median cuts and splits until a parcel has ≤ 450 node records and ≤ 650 segments, inside the factory envelope (largest factory parcel: 38.9 KB raw, 854 segments, 588 nodes; format limits: 64 KB raw / 64 KB zipped, 4,095 segments).

### Inside a parcel
- **Positions are sorted by (lon, lat)** (100%). Node *i* = position *i*; a long node takes two indices with the position repeated.
- **Arms are clockwise by bearing, starting from the one nearest north** (181,198 of 181,198 junctions checked).
- **Orientation is global:** every segment runs from its smaller-(lon, lat) end (left) to the other (right), including both tile-edge copies. Re-tiling never flips a segment, so U-turn masks, one-way bits, shapes and turn `hand` carry over unchanged.
- Shaped segments come first (shape *k* = segment *k*). There's no other required segment order.
- **Tile-edge segments** are written in both parcels, and **both copies carry shape, names and speed limit** (128,124 of 128,124 pairs have names in both).
- **Turn restrictions** are stored in every parcel holding a copy of the start segment (the 1,035 tile-edge restrictions appear twice).
- **mg layers:** a stub's far end that isn't a node of the layer is **always a border point**, even inside the same parcel.
- mg parcels carry fixed placeholders `junction_view_list = 0`, `junction_view_count = 256`, `junction_view_image_name_list = 0`.
- mp0 trailer stats: `(8, shape count, shape points)` in the factory id order, and `no_of_segments = segments − shapes`.

### Layer derivation
`layers.derive(mp0_graph, k, si)`: segments with rank ≥ k, plus every segment attached to their nodes. Node rank is the highest such layer. Compared against the factory mg graphs: see below.

## Not yet carried by the tiler

Lists the factory fills but the tiler doesn't write yet: altitudes (`alt`, `exc_alt`), signposts (`attr`), lanes, conditional access/speeds (`drivable_*`, `adas_speed`, `adas_oot`), junction views. The readers for most of these exist (reports 06, 04). `nodeinfo.depth` and long-node `nodeinfo3` are copied from the source graph; OSM-built graphs will write 0.
