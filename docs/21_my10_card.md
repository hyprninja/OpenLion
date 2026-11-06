# Phase 12: The MY10 card (compact map parcels)

Date: 2026-11-06. Modules: `openlion/schema.py`, `mapwrite.py`, `mapfile.py`, `tiler.py`, `graph.py`, `compat.py`, `build.py`; tests `tests/test_compact.py`, `tests/retile.py`, `tests/roundtrip.py`. App: card name and volume (`CardNames.cs`, `NavigationCard.cs`).

An error report from a VE Series II owner (hyprninja/OpenLion#19) came from the oldest iQ card so far. 14 of 19 tables round-tripped. The five map files (`auz.mp0`, `auz.mg1`–`mg4`) failed in every parcel with "unknown parcel layout (pcl_offs size 228)". A full copy of the card showed why.

## The card

| | MY10 card | MY12 card |
|---|---|---|
| Volume | CD_7172 (db_aunz), CD_7173 (db_ag) | CD_7597 / CD_7606 |
| Data | Navteq AUNZ 2009Q4 | Navteq AUNZ 2010Q3 |
| `cdrom.txt` | none (only `db_*/cdrom.toc`, like MY15/MY17) | yes |
| `au.mod` datamodel | Arriba 5.2.1 (same text) | Arriba 5.2.1 |
| `auz.rd`, `auz.str`, `auz.fea`, search tables | same format | |
| `pcl_offs` (parcel header) | **228 bytes** (114 u16) | 318 bytes (159 u16) |
| mp0 | 6,322 parcels, 53.3 MB | 6,819 parcels, 82.7 MB |

`au.mod` names the same datamodel string, so it can't tell the cards apart. Its field lists can, though, and the parcel header size identifies the layout by itself (`schema.for_parcel()`). `schema.use_card()` probes the first `auz.mp0` parcel, as it already does for MY15, and activates `V521_COMPACT`.

## What changed (from a diff of the two `au.mod` files, confirmed on every parcel)

**Parcel header, 159 → 114 u16.** The six `abs/short/long_addr_list_2` fields are gone (77 named fields instead of 83). The two traffic-sign lists after `traffic_sign_cnt` are called `no_overtaking` and `protected_overtaking` (empty in AU). The trailer is 37 u16 instead of 76, because its stats table (`adas_shape_counts`) has 10 triples instead of 23. In mp0 the ids are 47, 43, 45, 41, 46, 42, 44, 24, 8, 16, and id 8 again holds (shaped segments, shape points). It's followed by `flags` = 0 and `no_of_segments` = segments without a shape. mg trailers are all zero.

The remaining lists keep the MY12 encoding: altitudes, attributes/signposts, speed limits (`adas`, `adas_speed`), turn restrictions and lanes.

**Varints.** Three types are new in the datamodel: `1e`, an unsigned varint (7 bits per byte, low bits first, high bit = more bytes, at most 5 bytes), and `0d`, a signed coordinate pair packed into one varint:

```
first byte   bit7 more bytes, bit6 dlon < 0, bit5 dlat < 0, bits0-4 payload
then         7-bit continuation bytes (bit7 = more)
payload      magnitudes bit-interleaved, lowest first: dlat in the even bits, dlon in the odd
```

Example: `ae 01` is (+7, −2). Checked against MY12: the first deltas of the first MY10 parcel, (7, −2), (265, 15), (317, 18), (451, 32), are the same real-world nodes MY12 stores as i16 pairs.

**Positions** (`pos_list`, `exc_pos_list`). The same blocks (`u16 n, i32 lon, i32 lat`) are followed by n `0d` pairs instead of n × (i16, i16). A new block still starts where a delta leaves the i16 range (largest factory delta: 32,767).

**Nodes.** `nodeinfo0`, then `nodeinfo1` (short node) or `lHands, nodeinfo2, nodeinfo3` (long node), then `count` varint slots (`seg_idx` in the low 12 bits, flags above), with no unused slots. A long node still takes two node indices.

**Segments.** `length` is a `1e` varint instead of an i32. In mp0 (the schema conditions it on `layer == 0`) every segment then carries `layer_0_data`:

```
varint seg_shape_cnt, then that many 0d shape points (cumulative, as the MY12 shape list)
u16 addr_counter, big-endian: long:5 (bits 0-4), short:5 (bits 5-9), abs:6 (bits 10-15)
if any link: varint base_roadID (always the smallest roadID of the segment)
  per abs link:   varint delta_roadID
  per short link: varint delta_roadID, 4-byte range (addrinfo, delta, u16 base)
  per long link:  varint delta_roadID, 6-byte range (addrinfo, msb_base, u16 lsw_base, u16 delta)
```

roadID = base + delta is the same relative byte offset into `auz.rd` as MY12's `roadID` (all 3,850,092 factory links land on a road record). There is no `nameinfo` byte, and OpenLion writes 0 there on MY12 anyway.

So mp0's `shape_list` and `abs/short/long_addr_list` are empty (their offsets point at the next list), but their counts are still filled in: `shape_cnt` = shaped segments (1,310,331 in mp0), `abs_addr_cnt` (1,513,192), `short_addr_cnt` (2,321,365), `long_addr_cnt` (15,535). `shape_size` is the size MY12's shape list would have, 2 × (shape_cnt + 1) + 4 × points (2 for a parcel without shapes). Shaped segments still come first. mg layers have no `layer_0_data` and `shape_size` = 0, which is how a parcel tells the two apart (`ParcelContainer.layer0()`).

**Junction-view fields in mg parcels.** As on MY12, each mg layer repeats one meaningless value pair in the junction-view slots (MY10: `junction_view_list`/`_count`, then `junction_view_image_name_list` = 0, `_size` = 256). OSM builds write 0 in place of the junk and 256 in `junction_view_image_name_size`, mirroring what they already do for MY12.

## Result

`python -m openlion.compat <MY10 card>/navigation`: **all 19 tables are re-created byte for byte**, including all 10,341 map parcels. MY12, MY15 and MY17 still pass all 19.

`python tests/retile.py <MY10 db>`: each factory layer is decoded into a road graph, tiled again with OpenLion's compact writer and read back. All five layers come back as the same graph (segments with their shapes and road links, nodes, turn restrictions), and every new parcel frames cleanly. mp0: 1,930,837 nodes, 2,353,296 segments, 13,315 turn restrictions; 53.8 MB in 8,218 parcels (factory 53.3 MB in 6,322: OpenLion's parcel limits are tighter).

**OSM build for MY10 (ACT, `make_card --bbox 148.75 -35.95 149.45 -35.10`, checked against the card's own manifest):** the layout is detected as 5.2.1-compact, and 17 tables plus the POI database are written. The card check reports **0 problems**: 731 files, none missing or extra, 713 identical to factory, 18 replaced, no protected file changed. It finds 9,802 road records and 179 mp0 parcels (all framing cleanly, all 69,050 name links on a road record), 26,896 fea features and 4,747 POIs, and Northbourne Avenue and Lonsdale Street both look up.

## The POI database, and a bug it uncovered

The MY10 POI database (`edb/poi/aunz_sql.db3`, lower-case folder) differs in three ways:

- `Poi_BaseAttributes` has only `Poi_ID, PoiPartition_ID, Coordinate, Name_String_ID`, with no `Name` or `PhoneNumber` column (the phone number is still in `Poi_AddressAttributes`). The builder now writes as many columns as the table has.
- Category, icon and language strings take String_IDs 1..537 instead of 1..421 (5 languages instead of 2). The builder now reads the boundary from the database (`osm_poi.factory_strings_max`) and checks that it's contiguous.
- **Category IDs are numbered differently.** On MY10, ID 1 is "place of worship"; on MY12 it is "home improvement store".

MY15 and MY17 number them differently from MY12 too. OpenLion's OSM tag rules used MY12's IDs, so **MY15 and MY17 builds from 1.1.0 and 1.2.0 filed OSM POIs under the wrong categories.** In the earlier MY15 ACT build, the 675 "petrol stations" were restaurants, and 265 POIs (department, office-supply, book, clothing and sporting-goods stores, and campgrounds) sat in the road-sense alert categories: railway 127, school zone 57, blackspot 31, camera 22, school point 14, check speed 14. The rules now keep MY12 numbering as canonical IDs with their names (`osm_poi.CATEGORY_NAMES`), and each POI is written under the card's own ID for that name (partition ID = category ID on every card). `tests/card_check.py` now counts POIs in road-sense categories, looked up by name, as problems. The rebuilt MY15 ACT card has 0, and so does MY10.

The lower-case `edb/poi` folder also exposed that `build.build()` matched replacement paths case-sensitively, so the new POI database was silently not written on MY10. Paths now match case-insensitively (keeping the card's spelling), and a replacement with no factory file to replace is an error. card_check matches the POI path the same way.

`tests/test_compact.py` covers the codecs with synthetic data (pair and varint round trips, nodes, mp0 and mg segments, a parcel read back through `mapfile.Parcel`).

The app now shows the card as **MY10 (CD_7172)**. On cards with more than one database (MY10 also carries `db_ag`, CD_7173) the volume comes from `db_aunz`, the database OpenLion rewrites.

Not yet tested in a car.
