# Phase 10: The MY17 card (datamodel 5.3)

Date: 2026-11-05. Modules: `openlion/schema.py` (new), plus `roads.py`, `mapfile.py`, `mapwrite.py`, `tiler.py`, `features.py`, `feawrite.py`, `compat.py`, `pipeline.py`, `build.py` and `osm_names.py`.

The first error report from another user came from a newer card. The app's compatibility check refused it, as it should: 12 of 19 tables round-tripped, and rd, mp0, mg1–mg4 and fea did not. A full copy of that card showed why.

## The card

| | 2011 card (this project so far) | MY17 card |
|---|---|---|
| Volume | CD_7606 | CD_8418 |
| Data | Navteq AUNZ 2010Q3 | Navteq AUNZ 2016Q1 |
| Customer version | GMH 2011 | MY17 |
| DBAL | V8.4_V9.1 | V8.7 V9.4 |
| `au.mod` datamodel | Arriba 5.2.1 | **Arriba 5.3** |
| Table version stamps | 5.0.5 | 5.0.5 (same) |
| mp0 parcels | 6,819 | 9,743 |

All tables still carry the stamp `5.0.5`, so the stamps can't tell the two formats apart. The datamodel string in `au.mod` (the schema file described in report 02) can. `schema.detect()` reads it, and `schema.use_card()` activates the matching layout for everything that follows.

The MY17 card also lacks `auz.pol` and `auz.pot`, and its `files.cfg` lists 36 file IDs instead of 41. Neither file is one OpenLion writes. The POI database has the same SQLite schema, with more rows.

## What changed (from a diff of the two `au.mod` files)

**Road record (`auz.rd`), 29 → 26 bytes.** `u16 maxNumber, u16 minNumber` became `u8 hasNumbers` (0 or 1 in the factory data; 513,125 of 693,805 roads have 1). Everything else is unchanged. The 29-byte reader ran 5 bytes past the end of the table, which is what the report showed.

**Map header (mp0, mg1–mg4).** `u16 max_tmcp_chain_len, u16 max_tmcp_location_size` became `u32 maxPclId`, which is `pcl_cnt − 1` in every factory layer. They are the same 4 bytes, so old readers didn't notice.

**Parcel header (`pcl_offs`), 159 → 163 u16.** Six fields are new after `adas_oot_cnt`: `truck_list`, `truck_cnt`, `truck_speed_list`, `truck_speed_cnt`, `truck_oot_list` and `truck_oot_cnt`. `tmcp_list`/`tmcp_size` are gone. The 76-u16 trailer (`adas2_list` … `no_of_segs_with_node_radius`) is unchanged. Lists still start straight after the header, so the first value (`pos_list`) is 318 or 326. That identifies a parcel's layout on its own (`schema.for_parcel()`). The old decoder read the parcel-end marker from the wrong slot, so every parcel failed with "adas2_list is not the parcel end".

**Speed limits (`adas`).** The content byte gains bit 6 `speed_restricted_rl` and bit 7 `speed_restricted_lr`, each followed (after `no_of_speed_limits`) by an optional u8 speed for that direction. `adas_speed` gains bit 3 `is_direction` with an optional u8 direction. Both bits are always 0 in 5.2.1, so the extended codec reads both versions. The truck lists (`truck`, `truck_speed`, `truck_oot`) are kept as opaque blobs; OSM builds leave them empty.

**Also in the diff, not used by OpenLion:** `lane_use` was renamed `lane_use_2`, the TMC parcel list is gone, and the affix, ptype and separate postcode tables (`aff`, `ptp`, `pti`, `pmm`, `pmc`, `pmp`) are no longer described. New map-file names `mpa` and `mpb` are listed, but there are no such files on the card.

## Two things the datamodel doesn't say

**fea names length is a u16.** The schema is identical in both versions, but some MY17 ocean features carry their name in 30+ languages, more than 255 bytes. Byte 6 of the feature header, which report 07 left as unknown and which was always 0 on the 2011 card, is the high byte of a big-endian u16 `lenNames` (bytes 6–7). Reading it that way makes both cards' fea round-trip exactly.

**MY17 mp0 `byte_cnt` is the file size.** Every 2011 map file, and the MY17 mg layers, store the size of the parcel data in the common header's `byte_cnt`. The MY17 mp0 stores the whole file size (107,709,639). The writer copies this for 5.3 mp0. With only one sample we can't tell whether the unit reads this field.

## Result

`python openlion/compat.py "<MY17 card>/Navigation"`: all 19 tables are re-created byte for byte (rd, str, si, mp0, mg1–mg4, fea, ctr, stt, cny, cty, cl, ct, rl, rt, cal, cat). The 2011 card still passes all 19.

`python -m openlion.build "<MY17 card>/Navigation" <out> --identity`: 683 files copied unchanged and 19 tables rebuilt, all 19 byte-identical. `tests/card_check.py` on that folder: every parcel in every layer frames cleanly, the kd-tree reaches each parcel once, all 5,127,895 name links land on a road record and all 758,773 fea features parse. The CBD lookups find George St (Sydney), Swanston St, Hay St, King William St, Elizabeth St (Hobart) and Lonsdale St (Braddon) in the factory 2016 data. The other three miss only because the test uses OSM-style city names.

**OSM build for MY17 (ACT, `make_card --bbox 148.75 -35.95 149.45 -35.10`):** the datamodel is detected as 5.3, and 17 tables plus the POI database are written in the 5.3 layout. The card check reports **0 problems**: 9,802 road records (26-byte), 180 mp0 parcels (326-byte headers), all name links on a road record, 29,397 fea features, 4,747 POIs with the factory structure unchanged, and Northbourne Avenue and Lonsdale Street found. That's 2 minutes with cached OSM reads, 17 minutes from cold.

The same build against the 2011 card (5.2.1) also passes with 0 problems.

**New Zealand (report 18) on MY17:** a full NZ build (`make_card NZ=new-zealand-261103.osm.pbf`) onto the MY17 copy passes the card check with 0 problems: 98,454 road records, 2,027 mp0 parcels, 901,837 fea features and 64,250 POIs, and the lookups find Queen St (Auckland), Lambton Quay, Colombo St, Victoria St (Hamilton), George St (Dunedin) and Ponsonby Rd. MY17 lists the same 25 states and regions in a different order, with different time-zone IDs, and has 71 counties instead of 634. The NZ code matches regions by name and takes each region's time zone from the card's own counties, so every OSM region found its factory region with no extra changes.

## Not done

- `hasNumbers` is written as 0 for OSM roads, matching the 0 house numbers the 5.2.1 writer stores.
- Directional (rl/lr) speed limits and truck limits are read and kept, but not filled from OSM yet. OSM `maxspeed:forward/backward` and `maxspeed:hgv` would map onto them.
