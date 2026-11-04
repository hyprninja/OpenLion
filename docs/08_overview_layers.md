# Phase 3g: Overview layers (`mg1`–`mg4`) and their relation to `mp0`

Date: 2026-11-04. Render check: `openlion/render.py` on `auz.mg2` vs `auz.mp0` (not distributed).

## Result: a strict routing hierarchy, fully derivable from `mp0`

The four overview files are **not** drawn maps. The zoomed-out picture comes from `auz.fea` levels 1–4 (report 07). They are **routing layers** for long-distance route search, and each one is a pure subset of `mp0`:

> **Layer k = every mp0 segment with `seginfo.rank >= k`, plus every mp0 segment attached to a node of those segments ("stubs").**

The rule was checked on the whole database. It reproduces every layer exactly, with node identity (not coordinate) as the key:

| Layer | Segments | Predicted by rule | Mismatches |
|---|---|---|---|
| mg1 | 839,843 | 839,841 + 2 | 1,115 predicted stubs absent; all at grade separations (2,142 coordinates shared by two different mp0 nodes) |
| mg2 | 553,970 | 553,968 + 2 | 1,553, same cause |
| mg3 | 207,196 | 207,196 | 2,155, same cause |
| mg4 | 84,867 | 84,867 | 1,657, same cause |

(Counts are unique segments after de-duplicating tile-edge copies.)

## What is shared with mp0

- **Segments:** every overview segment is an exact copy of an mp0 segment: same endpoints, same `length_ft`, same `seginfo` index (100%). No chains are merged.
- **Nodes:** every overview node, border points included, sits exactly on an mp0 node. Each layer's nodes ⊂ the layer below (100%).
- **Arms:** every overview node lists **all** of its mp0 arms, **in the same order** (100%). That's what the stubs are for: arm numbers, the `restr_left/right` masks and turn restrictions keep their meaning in every layer.
- **Turn restrictions:** repeated per layer. About 91% match an mp0 manoeuvre by geometry. The rest are probably mp0 restrictions that start on a tile-edge segment and weren't comparable (not verified).

## What overview layers leave out

No shapes, names/addresses, speed limits (`adas`), signposts, lanes or junction views. Present: positions, border positions, altitude lists, nodes, segments, turn restrictions, a few `drivable_info/oot` (conditional access) entries.

`junction_view_count` reads 256 in every overview parcel, with a nonsense list offset. It's probably an unused field; copy as-is when writing.

## Node fields explained by the hierarchy

- `nodeinfo.rank` (3 bits) = **the highest layer the node appears in** (99.9%+ in all layers). The router uses it to step up to a higher layer.
- `nodeinfo.depth` (5 bits) acts like independent flags. Bit 0 is set mostly on 3–4-way junctions and bit 1 on degree-2 nodes; bits 2–4 are common. **Meaning not decoded.** The writer should copy factory patterns or test in the car.

## Tiling

Each layer is tiled independently: its own kd-tree and parcels, coarser as the layer thins out (median parcel width 0.076° in mp0 to 0.287° in mg4). As in mp0, segments crossing a parcel edge are stored in both parcels.

## Consequences for the converter

1. Build **mp0** from OSM: geometry, attributes, names, speeds, turns, lanes, signs.
2. Assign each segment a **rank 0–4** from OSM `highway=*` (e.g. motorway/trunk → 4, primary → 3, secondary → 2, tertiary → 1, rest → 0). This one field drives all four overview layers.
3. **Derive mg1–mg4 mechanically** with the rule above, keyed by node identity. Copy segment and node records, keep arm order, copy turn restrictions, drop names/shapes/speeds/lanes/signs. Set `nodeinfo.rank` to the highest layer containing the node.
4. Re-tile each layer with its own kd-tree. Parcel size is free; match the factory's typical sizes.
5. Open: `nodeinfo.depth`, and the probable `turn_res.direction` meaning. Both are candidates for a small car test.
