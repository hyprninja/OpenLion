# Phase 0–2: Inventory and format identification

Date: 2026-11-04. Source: a raw image of a VE Series II MY12 factory nav card and a file-level copy of its `navigation/` folder (neither is distributed here).

## Headline

- The map data is a **Siemens VDO / Continental database, format version 5.0.5, built from Navteq data** (not a Whereis-native format). Whereis is the retail brand only.
- **Nothing is encrypted.** Every table is either plain binary with a `SIEMENS` text header, or standard **zlib** inside a simple block container that is now fully decoded.
- Coordinates are **signed int32, degrees × 10^5** (little-endian in most tables; the `.fea` header is big-endian).
- Street names are **plain uppercase ASCII** in `auz.str`.
- No checksum or signature file has turned up yet. Phase 4 (integrity) is still open, and the in-car tamper test is still the deciding experiment.
  **Settled 2026-11-05:** the unit accepted a card with all map tables rebuilt from OSM, so there is no blocking check (see [16](16_test_card.md)).

## Phase 0: Safety

| Item | Result |
|---|---|
| Raw image | 4 GB card, 3,963,617,280 bytes |
| File copy | 2,225 files, 382,190,142 bytes. Per-file SHA-256 in [`reference/manifest_sha256.tsv`](reference/manifest_sha256.tsv), useful for checking that another card holds the same CD_7606 release |
| Copy vs image | All 2,225 files identical (`openlion/fat32_list.py` lists and hashes files straight from a raw image) |
| Partition | MBR, one partition, type `0x0B` (FAT32 CHS), starts LBA 8192, fills the card. 4 MB gap before it is all zeros. No hidden areas. |
| Filesystem | **FAT32**, 32 KB clusters, label `NO NAME`. The exFAT claim is wrong. |

**Imaging tip:** Windows writes a `System Volume Information` folder to any card it mounts writable, and many USB readers ignore the SD lock switch. Image the original card on a reader that honours the lock, or disable indexing for removable drives first. The extra folder appears harmless to the head unit, but the original card is best left untouched.

## Layout

```
navigation/
  cdrom.txt                 volume CD_7597 = CD_7606 (AUNZ) + CD_7335 (AG)
  db_aunz/                  Australia + NZ, Navteq 2010Q3, built 16.6.2011, ver 505.99.03f.84
    cdrom.toc, files.cfg
    db/        33 tables, 183 MB   <- the road map
    dbal/      VERSIONS.CFG + 2 x dbal_dll.dll (left untouched by decision)
    edb/POI/   aunz_sql.db3 (SQLite POI db, 59 MB), mappoi.bmp
    images/    742 day + 742 night PNG (POI/brand icons)
    telemat/   TMC traffic location tables
  db_ag/                    Arabian Gulf, Navteq 2009Q4, CD_7335 (unused in AU)
  voices/                   en_GB, en_US, fr_FR WAV prompts
```

The volume codes match community reports: **7606 = MY12 AUNZ**. `zone.cfg` also carries `08407606`, so MY16's "8418" is probably the same field.

## The table set (`files.cfg`)

`files.cfg` is a plain-text map from file ID to extension and compression type. It is effectively the format's table of contents:

| Type | Meaning | Files |
|---|---|---|
| 0 | not compressed | abc, aff, cal, cat, mod, vi, pc*/prd (all `SIEMENS` header) |
| 1 | FLAT (`zip\x01` container) | rd road, rl road list, rt road tree, str strings, cty city, cl city list, ct city tree, cny county, stt state, ctr country, typ road types, si seginfo, ti time info, tmc, pol/pot postcodes |
| 2 | MAP (tiled zlib) | mp0, mg1–mg4 (map geometry, 5 zoom layers) |
| 3 | FEATURE | fea (55 MB) |

Comments in the file say several tables are VW/BMW/Opel only. This is a shared Continental platform, which fits the RNS-510 lead.

### Common header (0x54 bytes, uncompressed tables and decompressed FLAT tables)

```
0x00  "SIEMENS"            0x10  "(C) SIEMENS AG"
0x20  "2.0.0"              0x30  "AU5059903F.84"   (db build id)
0x40  "5.0.5"              0x54  table-specific header starts
```

### FLAT container (cracked, `openlion/flat.py`)

```
0x00 "zip\x01" | u32 block_size=0xC00 | u32 uncompressed_size | u32 max_compressed_block
0x10 u32 offsets[n+1]   n = ceil(size / 3072), last offset = file size
each block = one zlib stream (78 DA) of 3072 bytes (last block shorter)
```

All 16 FLAT tables decompress and match the size checks. Run `openlion/flat.py` locally to unpack them. For example, `auz.rd` holds 22.6 MB of road records and `auz.str` holds 10.2 MB of strings.

### MAP / FEATURE files

Header magic `(*$SIEMENS&^)`, then the build id, then a **bounding box**. A tile directory follows, then back-to-back zlib tiles (tile 1 at 45,841 + 6,995 bytes = tile 2 at 52,836). The tile index format is the next thing to decode.

## Evidence for the coordinate encoding

- `mp0` / `au.cat` bbox: lon 96.81–178.54, lat −46.91 to −9.38 (Cocos Is. → NZ East Cape, Stewart Is. → Torres Strait).
- `.fea` header bbox (big-endian): 90.0 to 180.0, −48.1 to −7.3.
- `auz.rd`: **2,789** int32 (lon, lat) pairs fall inside a 0.1° box around the Melbourne CBD (e.g. 144.98585, −37.89927). Offsets are unaligned, so road records are packed, not padded.

## Strings

`auz.str` entries are `0x04 <TEXT> 0x00`, sorted, for example `EASTLINK`, `CHARLES SWANSTON WAY`, `0810, DARWIN`. Counts: SWANSTON 21, HODDLE 47, FLINDERS 444, MELBOURNE 302. Road records presumably point into this table by offset (to confirm).

## Other

- **POI database** `aunz_sql.db3` is standard SQLite, which is the easiest piece to regenerate from OSM.
- `dbal/` holds `dbal_dll.dll` (two versions) and `VERSIONS.CFG`. These are hashed only and deliberately not analysed (Route B is out of scope).

## Next steps (Phase 3)

1. Decode the `.rd` record layout: anchor on a known road (e.g. EastLink), find its string offset in `.str` and its coordinates in `.rd`.
2. Decode the tile directory in `mp0` / `mg1-4` and the `.fea` structure.
3. Write a round-trip test: re-pack the decompressed FLAT tables and compare byte for byte. zlib level and settings must match (current blocks start `78 DA`, i.e. level 9).
4. **Tamper test (in car):** on a spare full-size SD (FAT32), copy `navigation/` and change one letter of a minor street name in `auz.str`, then re-compress that block. Does the unit show it, reject the card, or ignore it? This decides whether a hidden checksum exists.
5. Get MY16 (CD_8418) for diffing, if possible.
