# Phase 6d: Street names and address search from OSM

Date: 2026-11-04. Code: `openlion/osm_names.py`. Test: `tests/osm_names_region.py`. OSM data © OpenStreetMap contributors, ODbL.

## Result

For inner Melbourne, the converter now writes every address-search table from OSM: `.str`, `.rd`, `.cty`, `.cny`, `.cl`, `.ct`, `.rl`, `.rt`, `au.cal`, `au.cat`. It also links every map segment to its road record. A lookup through the new tables, following the unit's path (city tree → city list → road tree → road list → road record → segments), finds:

| Typed city / street | Road record | Segments linked |
|---|---|---|
| MELBOURNE / SWANSTON STREET | 144.963°E, 37.819°S, +0.005 × 0.012° | 36 |
| RICHMOND / SWAN STREET | 144.989°E, 37.827°S | 47 |
| FITZROY / BRUNSWICK STREET | 144.977°E, 37.808°S | 63 |
| SOUTH YARRA / CHAPEL STREET | 144.994°E, 37.847°S | 56 |
| NORTH MELBOURNE / ERROL STREET | 144.950°E, 37.802°S | 24 |
| `3121, RICHMOND` / SWAN STREET (postcode form) | same record as RICHMOND | 47 |
| `MELBOURNE;NORTH` / ERROL STREET (rotated form) | same record as NORTH MELBOURNE | 24 |

Validation: every road tree and the city tree pass `check_tree`, the city list is sorted by the factory collation, all tables parse with the readers, and mp0 with name links reads back identically.

Inner Melbourne totals: 40 suburbs, 5 LGAs, 33 postcode areas, 3,541 road records (21,711 named and 5,441 unnamed segments), 3,133 strings, 93 city-list and 3,915 road-list entries.

## OSM model

| Factory | OSM |
|---|---|
| state | `boundary=administrative`, `admin_level=4`, mapped onto the factory state list by name (factory state IDs and `auz.stt` kept) |
| county | `admin_level=6` (LGA). Time zone copied from a factory county of the same state |
| city | **`admin_level=9`** (suburb / locality). Australia has 15,648; `admin_level=10` is almost empty |
| postcode | `boundary=postal_code` areas (2,645 in Australia), matched by the suburb's centroid |
| road record | one per (normalised name, suburb), plus one nameless record per suburb for unnamed segments (`roadInfo` 0, as in the factory data) |

A segment's suburb is the one containing its midpoint (middle shape point, else the middle of its end nodes). Segments outside every suburb go to a catch-all city.

## Name conventions copied from the factory data

- **Text:** uppercase, accents stripped, characters limited to the `au.abc` set, max 35 characters.
- **Rotations:** for a multi-word name, one variant per split of the leading words, keeping the street type (and any direction suffix) at the end. `WARNER GLEN MILL ROAD` → `GLEN MILL ROAD;WARNER`, `MILL ROAD;WARNER GLEN`. Factory: a 2-word name with a street type (`SWANSTON STREET`) isn't rotated; 27,126 three-word names are.
- **City names:** base, rotations (`NORTH MELBOURNE` → `MELBOURNE;NORTH`), and `POSTCODE, NAME`. Duplicate names get the state appended (`ABBOTSFORD (VICTORIA)` style).
- **`mainSeqNr`** = index of the group's main name (0 for base and rotations).
- **`nameinfo`** = 0 on segment links (factory: 0 in 82% of links).
- **City fields:** `cityIdMunicipality` = itself (as for 15,637 of 18,849 factory cities); `cataloglistIdFirst` = catalog (0 = AU); `cover` = suburb bounding box; centre = polygon centroid.
- **`au.cal`:** AU city tree at offset 0, NZ at the offset of its (currently empty) tree. **`au.cat`:** AU bounding box from the data.

## Not yet done / differences from the factory data

- **Phonetic names** (`trans` 5 slots, used by the voice) are left empty. The voice may then not announce street names; this needs a car test.
- No **house-number ranges** (`short_addr`/`long_addr`); only `abs_addr` links. OSM `addr:*` could fill them later. *Done in report 24.*
- One link per segment. The factory sometimes has several (e.g. street name + route name); OSM `ref` could add those.
- The factory's metro catch-all cities (MELBOURNE listing `STREET, SUBURB` names from across the metro) aren't reproduced. Each suburb lists only its own streets.
- **New Zealand catalog is empty** (AU extract only).
- Speller trees use the full-trie builder (report 10); the factory trees are sparser.
