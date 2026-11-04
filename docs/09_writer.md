# Phase 6a: Writers and byte-identical round trip

Date: 2026-11-04. Code: `openlion/` (`common`, `roads`, `mapwrite`, `feawrite`, `build`), test: `tests/roundtrip.py`.

## Result

Every map table the converter will replace can now be **written** as well as read. The proof is a round trip: parse the factory file, re-encode it from the parsed objects, re-compress, re-assemble, and compare. All come back byte-identical:

| File | Checked | Result |
|---|---|---|
| All 16 FLAT (`zip\x01`) tables | block container | identical |
| `auz.rd` road / address index | 624,756 records | identical |
| `auz.str` street names | 505,727 strings | identical |
| `auz.si` segment attributes | 2,631 records | identical |
| `auz.mp0` | 6,819 parcels; positions, border positions, nodes, segments, shapes, speed limits, turn restrictions each re-encoded; container, zlib, kd/handles, header | identical |
| `auz.mg1`–`mg4` | 4,463 parcels, same checks | identical |
| `auz.fea` | 192,282 parcels, 5 levels, grids, sentinels, headers | identical |

**Identity card build:** `python -m openlion.build <navigation> <out/navigation> --identity` rebuilds all 9 replaceable tables through the writers and copies everything else. The output folder is identical to the factory card: 2,225 of 2,225 files.

## Encoding rules pinned down while writing

- **Compression:** every compressed parcel (map files and `.fea`) and every FLAT block is plain zlib level 9, default memLevel/strategy.
- **Map parcel:**
  - The 318-byte `pcl_offs` is followed by the lists, contiguous in field order.
  - Trailer u16[0] (`adas2_list`) = parcel end.
  - The mg layers carry `junction_view_list = 0` / `junction_view_count = 256` / `junction_view_image_name_list = 0` as fixed placeholder values.
- **Trailer statistics (mp0):** a 23-entry `(id, a, b)` table in fixed id order. Only id 8 is filled: `(8, shape_cnt, total shape points)`. Then `flags`, and `no_of_segments` = seg_cnt − shape_cnt. mg layers: all zero.
- **Shape list:** the offset table always has `shape_cnt + 1` entries in mp0, so an empty shape list is the 2-byte table `[2]`. mg layers have no shape table at all.
- **Unused bytes:** factory files leave stale values in unused node slots and unused turn-restriction arm fields. The codecs keep them for exact round trips; new data writes 0.
- **`.fea`:** each level's grid has `cnt_x * cnt_y + 1` entries; the last is `{end of this level's data, 0, 0}`. Parcels follow in grid order with no gaps. A parcel is stored raw when zlib doesn't shrink it (`byteCntZip == byteCnt`).
- **Map file:** header `rec_cnt` = parcel count, `byte_cnt` = size of the parcel-data area.

## Card builder policy

`openlion/build.py` will only replace these files:

```
db_aunz/db/auz.rd  auz.str  auz.si  auz.mp0  auz.mg1  auz.mg2  auz.mg3  auz.mg4  auz.fea
```

Everything else (`cdrom.txt`, `cdrom.toc`, `files.cfg`, `zone.cfg`, `au.*`, `VERSIONS.CFG`, `dbal_dll.dll`, voices, images, TMC tables, `db_ag`) is copied unchanged, keeps its timestamps, and is hash-verified after copying. Any other replacement is refused.

## What the OSM converter still needs (in order)

1. ~~Search tables~~: done in [report 10](10_search_tables.md). They're now in `REPLACEABLE`, and the identity build covers 19 tables.
2. **POI database** `edb/POI/aunz_sql.db3` (standard SQLite): rebuild from OSM amenities.
3. **TMC traffic tables** (`auz.tmc`, `telemat/`) reference factory road IDs; they'll need emptying or remapping.
4. ~~Tiling~~: done in [report 11](11_tiling.md), including deriving the mg layers.
5. **Graph build from OSM:** nodes, arms, `hands`, U-turn/one-way masks, turn restrictions, ranks → mg layers (report 08), names → `.rd`/`.str`, speeds, lanes, signposts.
6. **Open bits** best settled by a car test: `nodeinfo.depth`, `turn_res.direction`, `signLevel`.
7. **The tamper test**, which decides whether any hidden check exists before investing in 1–5.
