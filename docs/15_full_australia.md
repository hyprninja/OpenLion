# Phase 6f: Full-Australia build

Date: 2026-11-04. Driver: `python -m openlion.pipeline <pbf> <factory db> <out> [--bbox …] [--lookup CITY STREET …]`. Input: Geofabrik `australia-latest.osm.pbf` (973 MB, 2026-11-04). OSM data © OpenStreetMap contributors, ODbL.

## Result

One run produces all 17 replaceable tables for the whole of Australia. Every `mp0` parcel passes the reader's framing checks, and a street lookup in every capital's CBD resolves through the new search tables, following the unit's path:

| Lookup | Road record |
|---|---|
| SYDNEY / GEORGE STREET | 151.207°E, 33.873°S |
| MELBOURNE / SWANSTON STREET | 144.963°E, 37.819°S |
| BRISBANE CITY / QUEEN STREET | 153.027°E, 27.468°S |
| PERTH (WESTERN AUSTRALIA) / HAY STREET | 115.848°E, 31.957°S |
| ADELAIDE / KING WILLIAM STREET | 138.599°E, 34.936°S |
| HOBART / ELIZABETH STREET | 147.319°E, 42.884°S |
| DARWIN CITY / MITCHELL STREET | 130.835°E, 12.466°S |

Duplicate suburb names get the state appended, exactly as in the factory data, which also has `PERTH (WESTERN AUSTRALIA)` / `PERTH (TASMANIA)` and `RICHMOND (VICTORIA)`. So you type PERTH and choose from the list.

| | OSM build (Australia) | Factory 2010 (AU + NZ) |
|---|---|---|
| OSM ways read | 1,816,856 (4 min) | |
| Road segments / nodes | 3,223,312 / 2,539,193 | 2.55 M / 1.99 M |
| Turn restrictions | 48,090 (1,518 relations skipped as ambiguous) | 16,159 |
| Segments with a speed limit | 1,091,208 (34%) | 38% |
| Suburbs / LGAs / road records | 15,546 / 601 / 608,240 | 18,849 cities (AU + NZ) |
| mg1 / mg2 / mg3 / mg4 segments | 1,287,309 / 772,819 / 277,334 / 114,632 | 839,843 / 553,970 / 207,196 / 84,867 |
| `auz.mp0` | 105.7 MB, 9,768 parcels, framing clean | 82.7 MB |
| `auz.mg1`–`mg4` | 28.6 MB | 21.3 MB |
| `auz.fea` | 233.1 MB | 55.6 MB |
| All 17 replaced tables | ~385 MB | 183 MB |
| Run time | ~37 min with cached OSM reads (~50 min from scratch), 64 GB RAM machine | |

No junction in Australia has more than 8 roads, so the split below never triggered. 9,622 segments (0.3%) lie outside every OSM suburb and go to the catch-all city.

## Changes needed for national scale

- **Junctions with more than 8 roads** (the format's limit) are split into two nodes at the same spot, joined by a 1 ft two-way connector. OSM turn restrictions that end up on the other half are skipped and counted.
- **Road ranks re-calibrated nationally.** Inner Melbourne alone made rank 4 motorway-only (21k segments in mg4 against the factory's 86k), but the factory's rank 4 (~40k km) includes the rural highways. `road_rank()` now uses the speed limit as the urban/rural test:

  | OSM | Rank |
  |---|---|
  | motorway | 4 |
  | trunk ≥ 80 km/h or untagged | 4 |
  | urban trunk | 3 |
  | primary ≥ 80 km/h | 3 |
  | urban primary | 2 |
  | secondary | 2 |
  | tertiary | 1 |

  OSM km: rank 4 ≈ 47k, rank 3 ≈ 32k, rank 2 ≈ 110k; factory (AU + NZ): 40k / 41k / 142k.
- **Background built per tile:** features are bucketed by tile per level, and the coastline is clipped recursively over tile ranges, so the mainland outline is processed about log(tiles) times. Sea / land / coast status comes out of the clipping.
- **Suburb assignment** is cached on a ~50 m grid (2.4 M point-in-polygon lookups for 3.2 M segments).

## Open

- **`auz.fea` is about 4× the factory size** (~233 MB vs 55.6 MB), and about 1.78 M small features were dropped to stay within the factory parcel limits. The overview levels need real generalisation: merging ways into long lines, merging adjacent small areas, and lower detail at levels 1–2. The card has room (the whole `navigation/` folder is about 580 MB), but load speed on the unit is unknown.
- Speed limits cover 34% of segments nationally (OSM tagging is thin outside the cities); the factory data has 38%. No defaults are added (decided 2026-11-05): 34% of 3.2 M segments already means more roads with a limit than the factory's 38% of 2.55 M.
- New Zealand is not included (AU extract only), so the NZ catalog is empty.
