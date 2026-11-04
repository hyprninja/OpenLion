# Phase 3d: Turn restrictions, long nodes, cross-parcel joins, one-way direction

Date: 2026-11-04. Code: `openlion/mapfile.py` (`Parcel.nodes`, `seg_ends`, `turn_restrictions`). All figures are for the full `auz.mp0` (6,819 parcels) unless noted.

## Summary

| Item | Result |
|---|---|
| Long nodes (5–8 arms) | **Decoded**: 2 records (20 bytes); node framing exact in all 6,819 parcels |
| Topology | **Complete**: 2,292,416 segments have 2 node ends; 256,252 have 1 node end + 1 border end. Nothing else exists. |
| Cross-parcel joins | **Decoded**: all 256,252 border segments pair across parcels (128,124 pairs + 1 group of 4 parallel duplicates) |
| `turn_res` list | **Decoded**: 14,080 in-parcel restrictions valid (0 bad arms); 1,035 starting at a junction in the next parcel all valid at the neighbour's node |
| Per-segment `restr_left/right` | **Decoded**: mask of forbidden exits at that end |
| One-way direction | **Decoded**: `drivable_lower` = lower-index → higher-index node |

## Nodes (final)

```
u8 nodeinfo0   count:4 (arms), sHands:4
count < 5  snode  1 record (10 B): u8 nodeinfo1 (sRank:3, sDepth:5), 4 x u16 slot
count >= 5 lnode  2 records (20 B): u8 lHands, u8 nodeinfo2 (lRank:3, lDepth:5),
                                    u8 nodeinfo3, 8 x u16 slot
slot: seg index (low 12 bits) + 4 flag bits (all 0 so far)
```

- A long node takes **two node indices**; position *i+1* repeats position *i*. That keeps "node *i* = position *i*" true.
- The first `count` slots are the arms. **Arm order is the order used by turn restrictions.**
- `hands` bit *k* (4-bit short, 8-bit long) = 1 when the node is arm *k*'s **higher-index ("right") end**. Checked on 1.14 M arms: 99.98% agree.

## Segment ends: left/right convention

- **Left** = the segment's lower-index node, **right** = higher-index. Positions are sorted by longitude inside a parcel, so left is usually the western end.
- Shapes run left → right.
- `marker_left` / `marker_right`: that end is a border point (`exc_pos_list` index in `left_node` / `right_node`).

## One-way direction

`seginfo.drivable_lower` = drivable **left → right** (lower → higher index); `drivable_upper` = right → left. This follows from the forbidden-exit masks: continuing onto a one-way the wrong way is masked as forbidden.

CBD readout, to sanity-check from local knowledge (2010 data): Little Lonsdale St is stored as eastbound; Little Bourke St, Little Collins St and Flinders Lane as westbound.

## Per-segment `restr_left` / `restr_right`

Present when `seginfo0.turn_restr` is set (most urban segments). Each is a bitmask over the arm list of the node at that end: **bit k = forbidden to leave via arm k** when arriving along this segment. Evidence:

- the value fits within 2^count in 99.94% of cases
- at two-way through-nodes the set bit is the segment's own arm, i.e. no U-turn
- at nodes where the onward road is one-way against you, the bit is the onward arm

## `turn_res_list` (explicit manoeuvre bans)

```
u32: seg:12 | direction:1 | count:3 | arm_no_0..arm_no_4 (3 bits each, bits 16..30) | hand:1 (bit 31)
```

1. Start on `seg`. `hand` picks the end: 0 = left, 1 = right. That end's node is the first junction, and it may lie in the neighbouring parcel (follow the join).
2. Take `arm_no_q` at each successive junction, `count` steps.
3. The resulting path is prohibited.

Distribution: 1 step 15,706; 2 steps 347; 3 steps 96; 4–5 steps 8. **Open:** what `direction` means. It splits exactly 50/50 and doesn't choose the node. Candidates: prohibited vs mandatory ("only") manoeuvre, or a travel-direction flag for time-dependent rules.

## Cross-parcel joins

A segment that crosses a parcel edge is **stored in both parcels**, each copy with one real node and one border point. The two copies have the same `length_ft`, mirrored endpoints (A's node = B's border point and vice versa) and opposite left/right markers. **Join key: (node coordinate, border coordinate, length).** A converter must write both copies.

## Lists present in this database (mp0 totals)

positions/nodes 1,990,508 · segments 2,548,668 · border points 256,252 · shapes 1,350,084 · abs/short/long addr 1.62 M / 2.39 M / 6.6 k · adas (speed) 958,253 (**only 38% of segments have a speed limit**) · adas_speed 27,056 · adas_oot 81,746 · turn_res 16,159 · attr (signposts) 8,834 · lanes 13,349 in 281 parcels · junction views 790 · altitudes 10,326.

## Still open

- `turn_res.direction`; slot flag bits; `nodeinfo` rank/depth meaning (probably routing hierarchy levels).
- `attr_list` (signposts / exit numbers), lanes, `adas_speed` / `adas_oot` (conditional speeds).
- `class` / `restclass` value tables.
- Generalised layers (`mg1-4`) ↔ `mp0` correspondence; `auz.fea`.

The routable graph (geometry, topology, attributes, names, speeds, turn bans, joins) is now fully readable from `mp0`. That's enough to start on the writer and a round-trip test.
