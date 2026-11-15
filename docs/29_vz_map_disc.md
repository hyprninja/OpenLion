# Phase 13: The VY/VZ map disc (CARiN database)

Date: 2026-11-16. Modules: `openlion/carin.py` (container, ISO), `carin_tile.py`, `carin_net.py`, `carin_write.py`, `carin_search.py`, `carin_iso.py`, `carin_build.py`; tests `tests/test_carin.py`, `tests/test_carin_build.py`. Source: an image (`NAV_DB.iso`) of the map disc from a VY/VZ Calais, the two models before the VE (not distributed here).

## Headline

- The VY/VZ system is a **VDO Dayton PC5400**, and its disc is a **Siemens VDO CARiN** database. This format family is separate from the Continental/Navteq tables on the VE cards (reports [01](01_inventory_report.md)–[21](21_my10_card.md)). Nothing on the iQ cards carries over except the general approach.
- Map data: **Sensis** (the Whereis supplier), built 2004-01-22, database "C-IQ 47041 (COMMERCIAL)", CD-ID 11175.
- The disc is a **96 MB CD**, not a DVD: plain ISO 9660, no Joliet or Rock Ridge.
- Almost all of it is one file, `carindb` (95.4 MB): a big-endian chain of **31,213 units**. Each unit is zlib-compressed, stored, or packed with a second codec.
- **The map data is not encrypted.** All 8,239 zlib units decompress, and recompressing them at level 9 reproduces them byte for byte. Street names, place names and POIs are plain lowercase ASCII.
- Coordinates: signed int32, **1 unit = 1.8×10⁻⁷°**. `lat = y × 1.8e-7`, `lon = x × 1.8e-7 − 30`.
- The disc also carries Siemens VDO's **C-IQ licensing layer**, which unlocks a disc for one unit. That layer is the `DAYTON` file, the encrypted files under `TPD/`, and tables 0x1F–0x28 at the end of `carindb`. OpenLion leaves all of it alone and copies it byte for byte. **The user supplies their own disc, already activated for their car, and OpenLion only refreshes the map tables in it.**
- **Status:** the format is decoded far enough to rebuild the road map, search and indexes from OSM (`openlion/carin_build.py`, below). Every check that can run without a car passes. Whether the PC5400 accepts a changed disc is still unknown: the [test set](#testing-in-a-car) is how to find out.

## The disc

| Item | Result |
|---|---|
| Image | 96,481,280 bytes (47,110 sectors). The volume claims 46,960 sectors; the rest is the 150 empty run-out sectors a CD rip picks up |
| Volume | ID `NAV_DB`, system `MAPCD_11175_EHV` (EHV: Eindhoven, CARiN's home), publisher `Siemens VDO Trading B.V.`, preparer `Digital Maps Program`, created 2004-01-19 |
| Mastering | The primary volume descriptor is written twice (sectors 16 and 17, identical). Names carry no `;1`, `carindb` is lower case, and root records have 10 bytes of system-use padding. A rebuild should patch the factory image rather than master a new one |

| LBA | Size | File | What it is |
|---|---|---|---|
| 22 | 42 | `BIBLIOGR` | Text: `CD-ID 11175`, `DB-CA-REL 27`, `SW-CA-REL 10` (needs software release 10 or later) |
| 23 | 42 | `DAYTON` | C-IQ licensing. Left alone |
| 24 | 719 | `ABSTRACT` | Text: build tool V2.0, 2004-01-23, source `…/CIQ/Sensis/australia/au_pa_nod_47041_20040122fcaz9.cpr`, "C-IQ 47041 (COMMERCIAL)" |
| 25 | 873 | `COPYRIGH` | Copyright notice |
| 26 | 689,664 | `CARINET` | TMC traffic-event texts in several languages (`carinet16s512ca.20020513`). Same unit container, tables 0x64–0x66. Not map data |
| 363 | 95,392,768 | `carindb` | **The road map** |
| 46943… | ~12 KB | `TPD/…` | C-IQ "third party data" access control (`acCIQ_au`): encrypted `.PSC/.LSC/.CPR` plus two images for the activation screen. Left alone |

The `ABSTRACT` notes "CARINSYS File: Not available": there's no unit software on the disc.

## `carindb`: the unit chain

The file is a whole number of **512-byte pages**. It is cut into units laid end to end. Every unit starts with an 8-byte header:

| Off | Type | Field |
|---|---|---|
| 0 | u32 | **address**: `(owner offset >> 8) << 8 \| page count`. For almost every unit the owner offset is the unit's own offset in the file |
| 4 | u16 | table type, 0x00–0x28 |
| 6 | u16 | codec: `0x00nn` stored; `0x0Fnn` zlib; `0x0Enn` second codec. For zlib and 0x0E, `nn` is the unpacked size in pages, header included |

- **Zlib units** (`78 DA`, level 9) unpack to exactly `nn × 512 − 8` bytes, and their padding is all zeros.
- **Stored units** are their own logical form.
- **0x0E units** keep their first few hundred bytes plain (section table, chain pointers, first records), and then switch to a dense packed stream. This codec isn't decoded yet. It appears only in tables that also have zlib units (see the table below). So the head unit picks the codec per unit, and **a writer can use zlib everywhere and never needs to produce 0x0E**.
- **Table 5** units are extension pages. Their address field points at the unit they extend (owner ≠ self), and they hold no header-relative data of their own.
- The volume header at offset 0 says one page but runs on into a second page that has no header. `units()` folds headerless pages into the unit before them. This only happens once.

The same address format (`offset>>8 | pages`) is how units point at each other: next/prev links in each unit, index tables (table 8, table 0x1F), and so on. **So a unit can't change size until every pointer is relocated.** `carin.py` patches units in place for now: a unit's contents can change, but it must still fit its slot.

Inside a unit, offsets are relative to the unit's first header byte. Most units start (after the 8-byte header) with a small section table of `(u16 offset, u16 count)` pairs, then a next/prev pair of unit addresses. Records follow.

### Tables

| Table | Units | On disc | stored / zlib / 0x0E | Role (tentative except where noted) |
|---|---|---|---|---|
| 0x00 | 6,454 | 32.7 MB | 502 / 2 / 5,950 | **Map tiles, detail level**: geometry plus labels (road and region names, plain text). Quadtree: tile sides 0x18000 · 2ⁿ units |
| 0x01–0x03 | 3,144 | 6.6 MB | 1,686 / 1,458 / 0 | Map tiles, overview levels (same tile header) |
| 0x04 | 4,150 | 4.8 MB | 360 / 3,790 / 0 | Road network for routing |
| 0x05 | 4 | 9 KB | 4 / 0 / 0 | Extension pages |
| 0x06 | 683 | 0.9 MB | 453 / 230 / 0 | Per-tile network data (has a tile bbox) |
| 0x07 | 1 | | 0 / 1 / 0 | Coverage root (bbox of the whole database) |
| 0x08 | 23 | 0.4 MB | 23 / 0 / 0 | Unit address index |
| 0x09 | 4,893 | 2.5 MB | 4,703 / 190 / 0 | Small per-tile records |
| 0x0A, 0x0B, 0x0D | 3 | | | Database and table directories |
| 0x0C | 27 | 0.3 MB | 0 / 27 / 0 | **Place names** for search, sorted ("baan baa", "new south wales") |
| 0x0E | 2,555 | 30.4 MB | 1 / 1,812 / 742 | **Street index** for search, sorted `street, suburb` ("zeehan street, hobart") |
| 0x0F, 0x11 | 63 | 0.6 MB | 0 / 63 / 0 | Search and POI indexes |
| 0x10 | 573 | 6.2 MB | 0 / 573 / 0 | **Points of interest** (decoded below) |
| 0x12 | 1 | | | Volume header: record-size list per type |
| 0x13 | 1 | | 0 / 1 / 0 | Database metadata ("47041", labels) |
| 0x14–0x16, 0x1C–0x1E | 8,628 | 9.9 MB | | Background areas: sea, land, water, with names ("coral sea", "indian ocean") at several scales |
| **0x1F–0x28** | 10 | 5 KB | | **C-IQ licensing** ("product activated", "no supplier activated", in several languages). Never touched |

`python -m openlion.carin info NAV_DB.iso` prints this table from any disc.

### Map tile header (table 0x00–0x03)

After the unit header comes a section table: 14 `(offset, count)` pairs starting at 0x08. The **bounding box** is at 0x44: four int32 values, `x0 y0 x1 y1`. For example, one tile on the Muirs Highway (WA) has bbox `0x30998800 0xF47A4000 0x30B18800 0xF4924000`. Stored tiles also have plain strings at the end: the country, region ("the great southern wine region"), road names and road numbers ("s102"), and the state.

### POI record (table 0x10)

The section table points at a list of 40-byte records:

| Off | Type | Field |
|---|---|---|
| 0 | i32 | x |
| 4 | i32 | y |
| 8 | u16 ×2 | flags / category (not yet decoded) |
| 12 | u16 ×5 | unit-relative string offsets: name or brand, street, suburb, city or state, country |

These records gave the coordinate calibration. Mount Street, Perth CBD (Best Western) is at `(0x304BC800, 0xF56B4300)`, which converts to 115.85°E, 31.95°S. Dealer records in Albany, Broome and Kununurra land on the streets they name.

## Deeper decode (rebuild work)

### Database layers and the spatial index

| Unit | Table | Meaning |
|---|---|---|
| Root | 0x07 | 12 entries of 24 bytes: `u32 address of the layer's first 0x08 unit`, world bbox (`0x2A518800 0xEE924000 0x42518800 0x06924000`), `u16 level`, `u16 scale`. Then a 32-bit value and the licensing root (0x1F); both are copied as found |
| Layer grid | 0x08 | `(0x10, n)` section of u32 addresses of 0x09 units, one per grid cell, x-major (`index = cx * rows + cy`), then `u32 cell size >> 16`. Detail roads: 256 x 256 cells of 0x180000 over 11 units. POIs: 128 x 128 of 0x300000. Level 1: 32 x 32 of 0xC00000. Background: 1 x 1 |
| Cell | 0x09 | `(0x14, m)` u16 sub-grid of list offsets, `(off, k)` u32 tile addresses, `u32 sub-cell size`. Sub-grids run from 1 x 1 to 32 x 32. Every empty cell of a layer points to one shared empty 0x09 unit |

| Root entry | Layer | Tiles |
|---|---|---|
| 0 | detail roads, level 6 | 0x00 (+ 0x04 house numbers) |
| 1 | POIs | 0x06 (POI tiles, records in 0x10) |
| 2, 3, 4 | road levels 1, 2, 3 | 0x01, 0x02, 0x03 |
| 5, 6, 7, 9, 10, 11 | background at six scales | 0x14, 0x15, 0x16, 0x1C, 0x1D, 0x1E |

Levels nest: a level-1 tile lists its level-2 children (s8), level 2 lists level 3, level 3 lists detail tiles. Each tile header holds its parent's address. The same road keeps its 32-bit road ID at every level it appears on.

### Road tiles (0x00-0x03), `openlion/carin_tile.py`

All 3,648 stored and zlib road tiles round-trip byte for byte through `carin_tile.parse()` / `write()`. The layout is in the module docstring. Coordinates inside a tile are u16, one step = 64 database units (about 1.3 m), measured from the bbox minimum. A detail tile of 0x180000 units spans 0..0x6000, a level 1-3 tile of 0x300000 spans 0..0xC000.

Header after the bbox: `0x54 u32` the tile's house-number unit (0x04, detail only), `0x58 u32` parent tile, `0x5C u32` tile size (overview levels; 0 on detail), then three u32 (`0x0020C49B` on overview levels, per-tile values on detail), `0x6C u32` (`0x0167000A` on detail), `0x70 u32` (`0x00010000` / `0x01010000`).

| Section | Record | Fields |
|---|---|---|
| s0 / s1 / s2 | 10 B | name hierarchy: country; place + state; street. `u16 str, u16 str2, u16 parent record, u16 first name entry (s14), u16 flags`. Record 0 is blank, the last is a fence |
| s3 | 4 B x 8 | first road and first s5 node of each of the 7 road groups, plus a fence. Roads are sorted by group = `(flags >> 8) & 7` |
| s4 | 26 B (overview) / 32 B (detail) + fence | `u16 node A, u16 node B, u16 first shape point, u16 next road at A, u16 next road at B` (circular lists round each node), `u16 class`, `u32 road ID`, `u16 flags`, `3 x u16 name pointers`, `u16`. Detail adds `u16 street record (s2), u16 attributes, u16 name pointer`. The fence record carries the end of the shape list |
| s5 | 8 B | node in the tile: `u16 x, u16 y, u16 first road, u16 flags` (bits 14/15: the node also exists on coarser levels) |
| s6 | 16 B | node on the tile edge: as s5, then `u32 neighbour tile, u16 offset of the matching node there, u16 0` |
| s7 | 6 B | shape point `u16 x, u16 y, u16 0` |
| s8 | 4 B | child tile addresses (levels 1-3) |
| s9 | 8 B | neighbour tiles: `u32 address, u16 x, u16 y` of a point on the shared edge |
| s10 | 8 B | `u32 own address, u16 road offset, u16 end (0/1)`, about 13% of roads on levels 1-3. Meaning not settled (probably turn handling); the writer leaves it empty |
| s14 | 4 B | name entries `u16 string offset, u16 flags` (0x200 place, 0x208/0x20A street name, 0x10/0x11 route number), then NUL-terminated, deduplicated strings |

Road class (+10): the high byte looks like a typical speed in m/s with bit 7 marking urban roads (`0x16` Muirs Highway, `0x86` suburban street), the low byte a category nibble with bits 4-5 for direction.

### House numbers (0x04)

One unit per detail tile, one 10-byte record per road in tile order: `u16 left from, u16 right from, u16 left to, u16 right to` (0x7FFF = none), `u16 scheme` (2 = odd/even sides, 1 = none or mixed). Not used for routing.

### Search

- 0x0D: letter trie over place names: `u32 unit address, u8 letter, u8 flag, u16 offset, u16 count`.
- 0x0C places: s0 8 B `(name, flags, name2, record)`, s1 20 B `(u32 street-list unit, u16 offset, u16 count, u16 POI-link offset, u16 POI count, u32 tile, u16 road offset, u16 0)`, s3 12 B links to city-centre POIs (0x11).
- 0x0F: per place, a letter index into the streets: `u32 0x0E unit, u8 letter, u8 flag, u16 offset, u16 count, u16 0`.
- 0x0E streets: s0 8 B `(name "street, suburb", flags, name2, s1 record)`, s1 4 B `(u16 first s2 entry, u16 count/flags)`, s2 24 B `(i32 x, i32 y, 4 x u16 house-number range, u32 tile, u16 road offset, u16 road count)`.

### POIs

0x06 POI tiles (30-byte records: `u32 0x10 unit, u16 offset, u16 x, u16 y, category ...`), 0x10 POI records (40 B: `i32 x, i32 y`, 2 x u16, 5 x u16 name offsets: name, street, suburb, city or state, country, ...) and 0x11 POI search index. POIs point at their detail tile (about 140,000 references), so they can't survive a road rebuild unchanged.

## What `carin.py` does

| Command | |
|---|---|
| `info NAV_DB.iso` | Files on the disc, plus a unit summary per table |
| `roundtrip NAV_DB.iso` | Re-encodes every zlib unit in place and checks the result is identical. On the factory disc, all 8,239 units are identical |
| `labels NAV_DB.iso "<name>"` | Finds which units hold a name, in all tables except licensing |
| `testdisc NAV_DB.iso OUT.iso "<old>" "<new>"` | Renames a label (same length) in the map tiles only. The search tables are sorted, so they stay untouched. Writes a new image that differs from the original only in the sectors it changed |

The licensing tables and files are never decoded or rewritten.

## The rebuild (`openlion/carin_build.py`)

```
python -m openlion.carin_build NAV_DB.iso OUT.iso --roads roads2_AU.pickle --boundaries boundaries3_AU.pickle
```

It takes the OSM caches an ordinary OpenLion build leaves in its work folder. All of Australia takes about 3 minutes and 9 GB of memory.

| Step | Module | What it does |
|---|---|---|
| Network | `carin_net.py` | Drivable OSM ways (no tracks, service roads or busways) inside the database's world box (97.8-170.3°E, 52.6°S-19.8°N; the Cocos Islands fall outside it), split at junctions: 2.18 M roads. Coordinates are snapped to the 64-unit tile grid and nudged off tile edges. Class code, group and urban/rural come from the calibration above. Urban means `maxspeed` 60 or less, or 40+ junctions per 0xC000 cell where there's no telling speed |
| Levels | `carin_build.py` | Detail: everything. Level 3: groups 0-2 (OSM rank 2+); level 2: groups 0-1; level 1: group 0. On levels 1-3, chains of same-class roads through plain pass-through nodes are merged and their shapes simplified. Result: 8.7 k / 36 k / 153 k roads (factory: 35 k / 67 k / 124 k) |
| Tiles | `carin_net.py`, `carin_write.py` | Quadtree inside each grid cell, split by estimated bytes. Roads are clipped at tile edges into pieces joined by paired edge nodes. Roads are sorted by group, then by street. Names: country, suburb + state (OSM admin levels 9 and 4), street name and route numbers. Detail-tile header fields that aren't understood are copied from the factory tile at the same place |
| House numbers | `carin_build.py` | None yet: detail tiles carry house-number address 0, as 2,297 factory tiles do (`WITH_HOUSE_NUMBERS` writes all-empty 0x04 units instead) |
| Grids, root | `carin_build.py` | New 0x08 / 0x09 grids for the four road layers and an empty POI layer. The root's layer pointers are patched |
| Search | `carin_search.py` | Place trie, places (suburbs, shown with their state), a street trie per place, street records pointing at runs of road records, plus the `' *'` all-Australia list of `street, suburb` |
| Layout | `carin_build.assemble` | Every unit is laid out with its final address, zlib-compressed, sized and re-laid until stable. The new units fill from 0x1200 up to the background block and carry on after the licensing block |
| Disc | `carin_iso.py` | The new `carindb`, with `TPD/` moved after it and the directory records, both path tables and both volume descriptors updated |

Kept byte for byte: every file except `carindb`. Inside `carindb`: the volume header, metadata (0x13), the country trie (0x0B), the background block from 0x5168200 (coast, land and water at six scales, with their grids) and the C-IQ licensing block.

Not carried over:
- **POIs.** The factory POIs point at the old road tiles; the POI layer is empty.
- **House numbers.** OSM has too few to be worth it yet; G-NAF could fill 0x04 later.
- **Turn restrictions.** Where the disc keeps them isn't known; s10 is written empty.
- **Postcode aliases** in place search.

Offline checks (`tests/test_carin_build.py`, plus a full-database validator run on every build): every tile re-parses and re-serialises, every road/node pointer and circular list holds, both copies of each edge node point back at each other at the same position, parents, children and house-number units match, every tile is found through its grid, and every street in search leads to road records carrying that street's name.

## Testing in a car

Nobody on the project has a VY/VZ, so the test discs go out in one batch. Each one answers a different question:

| Disc | Contents | If it works | If it fails |
|---|---|---|---|
| 1 control | the owner's own image, unchanged | the unit reads CD-Rs | the unit won't take CD-Rs at all: nothing else can be tested this way |
| 2 label test | factory data, "muirs highway" renamed "openlion test" in two map tiles (2 sectors) | changed map data is accepted | the disc is checked against its contents: stop here |
| 3 rebuild | OSM roads and search, Nov 2026 | done | if 1 and 2 work, the rebuild has a format mistake: note exactly what the unit did and when |

Muirs Highway runs between Manjimup and Mount Barker in WA. The label shows when the map is scrolled there, but disc 2 has done its job just by working normally.
