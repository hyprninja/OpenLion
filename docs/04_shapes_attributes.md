# Phase 3c: Shapes, segment attributes, name links, speed limits

Date: 2026-11-04. Code: `openlion/mapfile.py` (`Parcel.shapes`, `name_links`, `speed_limits`, `seginfo_*`), `openlion/render.py`. Render check: `openlion/render.py` (not distributed).

## Summary

| Item | Status | Evidence |
|---|---|---|
| Shape points (curves) | **Decoded** | mp0 10% sample: 116,452 shaped segments, median length error 0.20%, 99.6% within 2% |
| Border segments | **Decoded** | 25,539 border segments, 99.96% within 2% |
| Segment attributes (`.si`) | **Decoded** | toll bit lands on Sydney, CityLink/EastLink, Brisbane, NZ toll roads; one-way lanes correct in CBD |
| Name links | **Decoded** | 888/888 CBD links resolve to `auz.rd` records with correct laneway names; every mp0 segment has a link |
| Speed limits | **Decoded** | list framing exact in all 6,819 mp0 parcels; values 5–130 km/h, 50 dominant |
| mg1–mg4 shapes | **None exist** | overview layers carry curves as extra nodes |

## Shapes

```
shape_list (offset, shape_cnt, shape_size from pcl_offs)
  (shape_cnt + 1) x u16  offsets relative to list start; last = shape_size
  per shape: n x (i16 dlon, i16 dlat), cumulative, degrees x 1e5
```

- **Shape k belongs to segment k.** Shaped segments are numbered first (0…shape_cnt−1). `no_of_segments` in `pcl_offs` counts the unshaped rest (CBD: 375 + 56 = 431).
- Points start from the segment's **lower-indexed node**. For a border segment they start from its in-parcel node.
- `mg1`–`mg4` have `shape_cnt = 0` everywhere. Their geometry is already simplified into nodes, and stored lengths stay true, so straight-line checks are looser (74–90% within 2%).

## Border (exception) nodes

`seginfo0.marker_left` / `marker_right` say one end of the segment is outside the parcel. `left_node` / `right_node` then index **`exc_pos_list`** (same block format as positions). Matching to the neighbouring parcel's own node is not yet done. It's probably by coordinate.

## Segment attribute table (`auz.si`)

Index into the table: `seginfoID (3 bits) | ext1 << 3 | ext2 << 11`. That's 2,631 u32 records (the max index seen, 2,597, is in range). Fields (LSB-first):

| Bits | Field | Observed meaning |
|---|---|---|
| 0–2 | rank | 0 local … 4 freeway (Monash/CityLink = 4, Eastern Fwy / Hoddle St = 3, arterials = 2) |
| 3–6 | class | 2 major street (Bourke St), 3 ordinary street (most), 7 tracks / unnamed, 10 unnamed links… (needs a full table) |
| 7 | divided | set on divided parts of Bourke St |
| 8 / 9 | drivable_lower / upper | 3 = two-way; 1 or 2 = one-way, one value per direction (Little Bourke/Little Collins/Flinders Lane = 2, Little Lonsdale = 1) |
| 10 | toll_vignette | |
| 11 | toll_road | 3,531 segments: Sydney 1,723, Melbourne 983, Brisbane/Gold Coast 751, Auckland 35, Tauranga 22 |
| 12–13 | tollbooth_direction | |
| 14 | hwy_complex | |
| 19–21 | restclass | |
| 22–24 | landmark | 2 = roundabout (77% of segments on Canberra roundabouts, 0% elsewhere); 5 seen on ~6% of other roads, meaning unknown |
| 25 | dbldig | |
| 26 | detailedcity | |
| 27 | urban | CBD 1, rural Victoria 0 |
| 28 / 29 | bifurcation left / right | |
| 30 | rnc | always 0 in AUNZ |
| 31 | truck_digitized | |

**To confirm:** which of 1 and 2 means "forward" relative to the segment's lower→higher node direction. A one-way street with known direction will settle it.

## Name links (segment → street)

```
abs_addr   7 bytes: u16 seg, u8 nameinfo, u32 roadID
short_addr 11 bytes: + u8 addrinfo, u8 delta, u16 base        (house-number range)
long_addr  13 bytes: + u8 addrinfo, u8 msb_base, u16 lsw_base, u16 delta
```

`roadID` is a **byte offset into `auz.rd`, relative to its 0x5E header**, the same convention as string offsets. That road record gives the street name(s), city and house-number range. List framing is exact (CBD: 154 × 7 and 734 × 11 land on the next list offsets). `_2` variants of these lists exist in the schema but are empty in this database.

## Speed limits (`adas_list`)

```
u16 seg, u8 content (bit1 speed_restricted, bits2-3 no_of_lanes_present),
[u8 speed km/h], [u8 no_of_lanes], u8 no_of_speed_limits
```

Distribution over mp0: 50 km/h 710k, 60 120k, 80 39k, 100 36k, 70 33k, 110 8k, 40 6k, 130 114 (NT open road), plus a few 5–35. `adas_speed_list` / `adas_oot_list` (time-dependent limits) are empty in the parcels checked.

## Still open

1. Turn restrictions: `seg.restr_left/right` values and the `turn_res_list` (u32 bitfield: seg_idx 12, direction 1, count 3, arm_no_0…4 × 3 bits, hand 1). The `seginfo0.turn_restr` flag is set on 309 of 431 CBD segments, so it's probably a "has arm info" flag rather than a hard restriction.
2. Long nodes (≥5 arms): how arms beyond the 3 stored slots are found.
3. `class` and `restclass` value tables; the one-way direction convention.
4. Matching border nodes to neighbour parcels.
5. `attr_list` (signposts / exit numbers), empty in the parcels checked so far.
6. `auz.fea` (areas, water, map symbols) and the generalised layers' link to mp0.

With 1–4 done, the routable road graph is fully understood. That's enough to start the converter's writer side.
