# Phase 7: Test card assembly

Date: 2026-11-05. Tools: `python -m openlion.build <factory navigation/> <new navigation/> --tables <pipeline out/db>`, then `python tests/card_check.py <new navigation/>`.

## Result

The OSM tables from the full-Australia run (report 15) went into a complete `navigation/` folder, ready to copy to a spare SD card:

| | |
|---|---|
| Files | 2,225 (same list as the factory card, nothing missing or extra) |
| Copied unchanged, hash-verified | 2,208, all matching `docs/reference/manifest_sha256.tsv` |
| Replaced map tables | 17: `auz.rd .str .si .mp0 .mg1–4 .fea .cny .cty .cl .ct .rl .rt`, `au.cal`, `au.cat` |
| Protected files changed | none (`cdrom.txt`, `cdrom.toc`, `files.cfg`, `zone.cfg`, `au.mod`, DLLs, voices, images, TMC, POI, `db_ag`) |
| Header stamps | all 17 new tables keep the factory's 0x5E-byte header up to the record counts |
| Size | 584 MB (factory 382 MB), so a 1 GB card is enough; the factory card is 4 GB |
| Factory timestamps | kept on every file, the replaced ones included |

`auz.stt` (states) and `auz.ctr` (countries) are replaceable but stay factory: the OSM build reuses them as they are.

## Read-back check

`tests/card_check.py` reads the assembled folder back through the decoders:

| Check | Result |
|---|---|
| `mp0`: 9,768 parcels; `mg1`–`mg4`: 2,899 / 2,047 / 517 / 256 | every parcel frames cleanly; each kd-tree reaches every parcel exactly once |
| Name links in `mp0` | 3,419,486, every one lands on one of the 608,240 road records |
| `.fea` | 5 levels, 3.49 M features, 0 parse errors, largest raw parcel 110,000 B (factory buffer 116,000) |
| Street lookups through the search tables | resolve in all 8 capitals |

## Cross-table references left as factory

Before assembly, every untouched table was checked for links into the road network that the new data would break:

- **POI database** (`edb/POI/aunz_sql.db3`, SQLite): POIs carry a coordinate and address strings only. The segment link table `Poi_VSegAttributes` is empty. So the 2010 POIs still show, at their coordinates, and routing to one uses the nearest new road.
- **Traffic (TMC)**: `auz.tmc` and `telemat/` hold location tables. The factory map tiles have no TMC point lists (`tmcp_size` is 0 in all 6,819 parcels, and the header maxima are 0), so nothing in the tiles points at them. Traffic messages will probably not attach to roads, the same as before.
- **Phonetics**: `cdrom.toc` says `Phoneme: NO`, and the phoneme tables are empty in the factory data too. Empty phonetics in the OSM build therefore matches the factory card.
- `cdrom.toc`, `cdrom.txt`, `files.cfg` and `zone.cfg` contain no file sizes or checksums.

## Known differences on the new card

- **Canberra**: the factory data has one city, CANBERRA, covering the ACT. OSM has the ACT suburbs (CITY, BRADDON, …), and the old name finds only CANBERRA AIRPORT. Larger place names such as GOLD COAST don't exist as cities in either data set.
- **POIs** are still the 2010 factory set.
- **No house numbers**, so address search goes to the street.
- **No New Zealand**: the NZ catalog is empty.
- **No signposts, lanes or junction-view images** in the OSM build.
- **Speed limits** come from OSM only: 34% of segments. Roads without an OSM limit show none.
- **`.fea` is 4× factory size** (233 MB vs 55.6 MB). Every parcel fits the factory buffers, but load speed on the unit is unknown.

## In-car result (2026-11-05)

The assembled card went into the car (VE Series II MY12, iQ) on a spare SD card:

- **Accepted without complaint.** No error or warning, so there is no hidden checksum or signature over the map tables. Fully rebuilt tables with new sizes and contents load fine as long as the metadata files stay unchanged.
- **New data is used.** A local street that didn't exist in the 2010 data now shows on the map.
- **Routing works.** A simulated drive (the unit's demo mode) to a town about 200 km away planned and ran correctly, which uses the long-distance routing layers (`mg1`–`mg4`) as well as `mp0`.
- **Address search works** with the new OSM suburbs, streets and speller trees.
- **Zoomed-out map seemed fine**, so the 4× larger `.fea` doesn't noticeably slow drawing in first use.

Still to check on real drives: the speed-limit display and spoken turn guidance.

## In-car test plan

Use a **spare** full-size SD card. The original card stays read-only and is the way back.

1. **Format** the spare card FAT32 with the factory volume label, for example on Windows: `format X: /FS:FAT32 /V:CD_7606 /Q`, with X being the spare card.
2. **Control run (recommended first):** copy the factory `navigation/` copy to the card root and confirm the unit accepts a copied card. This separates "the unit rejects any non-original card" from "the unit rejects the new data".
3. **Test card:** reformat, then copy the assembled `navigation/` folder to the card root (`robocopy <new navigation> X:\navigation /E /COPY:DAT /DCOPY:T`).
4. In the car, check, in this order:
   1. Card accepted, and the map version shown is the same as with the original card.
   2. Map draws at every zoom level: roads, water, parks and coastline.
   3. Address entry: for example MELBOURNE → SWANSTON STREET, and BRADDON → NORTHBOURNE AVENUE.
   4. Route calculation, both short and long (e.g. Sydney → Melbourne, which uses mg3/mg4).
   5. Turn-by-turn guidance and the speed-limit display on a road with an OSM limit.
   6. Panning and zooming speed.
5. If the unit rejects the card or misbehaves, put the original card back. Note what was on screen, and whether the control card worked.
