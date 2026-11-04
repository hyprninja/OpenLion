# Phase 3b: Schema grammar and map tiles (`mp0`, `mg1`–`mg4`)

Date: 2026-11-04. Parser: `tools/tiles.py`. Render check: `tools/render.py` (renders of factory data are not distributed).

## Result

The road network (nodes, segments, lengths, topology) decodes cleanly from all five map layers. Every structural check passes in **all 11,282 parcels**:

| Check | mp0 | mg1 | mg2 | mg3 | mg4 |
|---|---|---|---|---|---|
| parcels (all zlib OK) | 6,819 | 2,128 | 1,479 | 595 | 261 |
| position list framing | ✓ all | ✓ all | ✓ all | ✓ all | ✓ all |
| border-position framing | ✓ all | ✓ all | ✓ all | ✓ all | ✓ all |
| #positions = #nodes | ✓ all | ✓ all | ✓ all | ✓ all | ✓ all |
| node list framing | ✓ all | ✓ all | ✓ all | ✓ all | ✓ all |
| segment list framing | ✓ all | ✓ all | ✓ all | ✓ all | ✓ all |

mp0 holds 1.99 M nodes and 2.55 M segments. In mp0, 2.28 M segments are referenced by exactly two nodes. Most of the rest are referenced once, because they cross a parcel border.

Geometry check (Melbourne CBD parcel): for all 381 internal segments, straight-line node distance ÷ stored length = 0.302–0.306 (10th–90th percentile). **Lengths are in feet** (0.3048 m). The rendered CBD is recognisable street by street.

## Schema (`au.mod`) grammar, as far as decoded

Entry: `u8 type, name\0, u16 varId (FFFF = none), u32 minSize, u32 maxSize, …`, then:

- **Containers** (`0B` table, `08` struct, `0A` list): `u8 childCount, u8 0`. Type `08` with children adds `u8 isHeader`. Children follow.
- **Bitfield containers** (size-1 or size-2 ints holding bits): `u8 childCount` only.
- **Bitfield leaves** (min = max = 0): `u8 bitWidth, u8 bitOffset` (LSB-first).
- **Plain leaves**: `u8 0, u8 0`.
- **Char arrays** (`00`): `u16 minLen, u16 maxLen, u8 flag`.
- **List trailer** (11 bytes, after a repeated element's last child): `00 00 <u16 countVar> 00…`. Example: `road_list` × var 0x19 (`rec_cnt`), `pcl_dir` × var 0x34 (`kd_cnt`).
- **Condition** (optional fields): `03 00 FFFF <u16 var> <u8 op> <u8 value>`. op `00` means `==`, `02` means `<`, `03` means `>=`.
- **Table end**: file extension string, e.g. `rd`, `mp0`; the map table lists `mp0`, `mg1`… `mg4`.

That's enough to read layouts by hand reliably. It isn't yet a fully automatic interpreter: some trailer variants (`01 00 04 00…` on node lists) aren't understood.

## Map file layout

```
0x00   common header (0x5E)              rec_cnt = parcel count,
                                         byte_cnt = size of parcel data
0x5E   mapHeader (32)  i32 min_long, min_lat, max_long, max_lat
                       u16 layer (0..4), u32 pcl_cnt, i32 kd_cnt,
                       u16 kd_depth, u16 max_tmcp_chain_len, u16 max_tmcp_location_size
0x7E   pcl_dir         kd_cnt x {i32 edge, u32 lo_index, u32 hi_index, u8 kd_info}
                       kd_info bits: 0 cut axis (0=lon,1=lat), 2 lo_empty,
                       3 lo_is_parcel, 6 hi_empty, 7 hi_is_parcel
       pcl_hndl_list   pcl_cnt x {i32 file_offset, u16 raw_size, u16 zip_size}
       parcel_data     pcl_cnt zlib streams, contiguous
```

The kd-tree lookup works: walking it for (144.963, −37.813) lands on the CBD parcel.

## Parcel layout (decompressed)

**`pcl_offs`, 318 bytes = 159 × u16.** These are `(byte_offset, count)` pairs for: pos, alt, exc_pos, exc_alt, node, seg, attr, abs_addr, short_addr, long_addr, adas, adas_speed, adas_oot. Then `shape (offset, count, size)`, turn_res, then about 35 more lists (lanes, slopes, TMC, junction views, traffic signs…). They're mostly empty in this AU database. After those come `adas2`, 23 × 3-u16 `adas_shape_counts`, `flags` (shape/height/slope/radius-present bits), `no_of_segments`, `no_of_shape_points`, `node_radius_total_cnt` and `no_of_segs_with_node_radius`.

**Positions** (`pos_list`, also `exc_pos_list` for border nodes): one or more blocks of `u16 n, i32 lon, i32 lat, n × (i16 dlon, i16 dlat)`. Deltas are **cumulative**, and points are sorted by longitude. Position *i* is node *i*. Verified exactly: every road bounding box in `auz.rd` inside the CBD parcel matches decoded node coordinates exactly (97/97).

**Nodes**, fixed 10 bytes:

```
u8 nodeinfo0     count:4 (number of segments), sHands:4
count < 5:  u8 nodeinfo1 (sRank:3, sDepth:5), 4 x u16 slot
count >= 5: u8 lHands, u8 nodeinfo2 (lRank:3, lDepth:5), u8 nodeinfo3, 3 x u16 slot
slot: seg_idx = low 12 bits, top 4 bits = flags (meaning TBD)
```

Only the first `count` slots are used on short nodes. **Open:** how long nodes (5–15 arms, ~0.2% of nodes) list arms beyond 3.

**Segments**, variable length (5–13 bytes):

```
u8 seginfo0   bit0 marker_left, bit1 marker_right, bit2 turn_restr,
              bits3-4 ext_count, bits5-7 seginfoID
ext_count x u8 seginfoIDext       (with seginfoID: index into auz.si, TBD)
if turn_restr:   u8 restr_left, u8 restr_right
if marker_left:  u16 left_node
if marker_right: u16 right_node
i32 length (feet)
```

## Still to decode (next)

1. **Shape points** (`shape_list`): needed for curved roads. Currently curves render as straight lines.
2. **`attr` / `seginfoID` → `auz.si`**: road class, speed, one-way, toll. This is where routing attributes live.
3. **Name links**: how segments tie to `auz.rd` roads and street names. Likely via `abs_addr` / `short_addr` / `long_addr` and the schema's `roadID` / `nameinfo` fields.
4. **Cross-parcel joins**: how border nodes (`exc_pos`) link to the neighbouring parcel.
5. Long-node arm lists; slot flag bits.
6. How `mg1`–`mg4` (generalised zoom layers) relate to `mp0`: shared IDs or independent.
7. `auz.fea` (55 MB, "feature" table): area polygons, water, POI symbols.
