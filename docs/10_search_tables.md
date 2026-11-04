# Phase 3h: Address search (country, state, county, city, speller trees)

Date: 2026-11-04. Code: `openlion/search.py`. All tables round-trip byte-identically (`tests/roundtrip.py ... search`). The identity card build now rebuilds 19 tables and still matches the factory card exactly (2,225/2,225 files).

## How "enter destination" works

```
au.cal  catalog list (one per country)  --cityTreeOffset-->  auz.ct  city speller tree
auz.ct  node ranges  -->  auz.cl  city list {cityID, name seqNr, roadTreeOffset}
auz.cl  roadTreeOffset  -->  auz.rt  road speller tree of that city
auz.rt  node ranges  -->  auz.rl  road list {roadID, name seqNr}
roadID  -->  auz.rd road record (name, city, bbox, house numbers)
auz.rd record  <--roadID--  map parcels' abs/short/long_addr  -->  map segments
```

**End-to-end check** (the same steps the unit takes):
1. Typing `MELBOURNE` follows the city tree 3 levels to a 37-entry slice of the city list.
2. The `MELBOURNE` entry's road tree, given `SWANSTON STREET`, leads to the road record for Swanston Street, numbers 1–501, at 144.962°E, 37.817°S.
3. That record links to **55 map segments** in the CBD parcels.

## Tables

| Table | Records | Layout |
|---|---|---|
| `auz.ctr` country | 2 (AU, NZ) | 80 B: id, trans, name[35], trans, phonetic[35], ISO3, ISO2, speedUnit, drivingSide |
| `auz.stt` state | 25 | 152 B: stateID, zoneID, county range, cover, name, phonetic, countryID, `alpha_counties[26]` |
| `auz.cny` county (LGA) | 634 | 106 B: countyID, stateID, zoneID, city range (u32), cover, name, phonetic, timeinfoID |
| `auz.cty` city | 18,849 | 38 B + nameNum × u32 `.str` offsets. **cityID = record byte offset − 0x5E**, which is what `.rd` road records store |
| `au.cal` catalog list | 2 | 48 B: catalogID, type, trans, name[36], cityTreeOffset (AU 0, NZ 101168), postalcodeTreeOffset (−1) |
| `au.cat` catalog | 2 | 19 B: catalogID, streetFirst, bounding box |
| `auz.cl` city list | 25,244 (AU 0–22,990, NZ after) | 10 B: cityID, name seqNr, roadTreeOffset |
| `auz.ct` city trees | 2 trees, 5,650 nodes | see below |
| `auz.rl` road list | 1,508,444 | 5 B: roadID, name seqNr |
| `auz.rt` road trees | 18,849 trees (one per city), 418,066 nodes | see below |
| `auz.ti` time info | 43 | 23 B: id, timezone (1200 + 100 × UTC hours, e.g. 2150 = UTC+9.5), DST rule (`yMf`/`yMl` = yearly, month, first/last weekday) |
| `au.aff` affixes | 6 | 44 B: abbr[3], name[41] (N/NORTH, NE, E, S, W) |
| `auz.typ` road types | 1 (empty) | 83 B |
| `auz.pol/pot`, `au.pca/pcl/pct/prd` | 0 | postcode and phoneme tables are empty in AUNZ |

Names in the second name slot (trans byte 5) are phonetic transcriptions for the voice, e.g. AUSTRALIA → `c|*"stre|li|j@`.

### Speller trees (`.ct`, `.rt`)

```
tree header 12 B: u32 listID_start, u32 listID_end, u32 nodeCount
node        19 B: char key[5], u32 childIndex, u16 childCount, u32 dataIndex, u32 dataCount
```

- Node 0 is `*`, and node 1 a structural root keyed `2` (city trees, 79 road trees) or `1` (all other road trees; meaning unknown). Below that, keys are single characters. An empty key (`''`, always first among siblings) holds the names that end exactly at that prefix.
- `dataIndex/dataCount` give a slice of the list (relative to `listID_start`). Validated: **every node of every tree** covers exactly the names that start with its prefix.
- Node array: each node's children form one block, and blocks are laid out depth-first. Leaves store childIndex 0. Trees are packed back to back in city order.
- **The factory trees are sparse.** Not every next character gets a node: under `A`, there are nodes for B, D, E… but none for C, though 13 names start "AC". The rule behind the choice isn't found; it doesn't follow count, position or alphabet. Since the list is sorted, the unit evidently scans the list slice where a node is missing.

### Sort order

All lists (both city lists, all 18,849 road lists) are sorted by the character order stored in `au.abc`:

```
" &(),-./;ABCDEFGHIJKLMNOPQRSTUVWXYZ[]'0123456789"
```

So apostrophes sort after letters (`DYSART` before `D'AGUILAR`), and postcode-first names (`3051, NORTH MELBOURNE`) come last. `search.collate_key()` implements it.

## Writer

`search.py` reads and writes all of the above. `build_tree()` makes a valid tree from a sorted name list with a full-trie strategy, a superset of the factory structure: 11,242 nodes at leaf size 8 and 45,317 at leaf size 1, versus 5,324 in the factory AU city tree. `check_tree()` validates any tree.

**Open (car test):** does the unit accept a fuller tree than the factory one? A minimal tree (root only) is also valid by the same logic. Both can go on the first test card. The meaning of the root `1`/`2` digit is also unknown; copy the factory value (city tree `2`, road trees `1`).

## Notes for the converter

- City entries: OSM `place=suburb/town/village/locality` plus their `addr:postcode`. Add the factory-style variants: `NAME;PREFIX` inversions (e.g. `MELBOURNE;NORTH`) and `POSTCODE, NAME`.
- Counties ← LGAs (`boundary=administrative`, `admin_level=6`); states ← `admin_level=4`.
- Road lists per city ← OSM streets grouped by the suburb polygon they fall in. Use the same name variants as the factory data, including `, SUBURB`-qualified names in the metro catch-all city.
- Time info, affixes and road types can be copied from the factory card unchanged.
