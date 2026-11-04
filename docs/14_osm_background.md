# Phase 6e: Background layer (`auz.fea`) from OSM

Date: 2026-11-04. Code: `openlion/osm_fea.py`; `feawrite.encode_fea` now computes the header's Zip_Statistic. Test: `tests/osm_fea_region.py`. OSM data © OpenStreetMap contributors, ODbL.

## Result

For greater inner Melbourne (144.86–145.08°E, 37.90–37.76°S), the converter writes a complete `auz.fea` from OSM. It uses the factory grid (5 levels, same parcel sizes and scales) and the factory per-type style bytes. Every parcel reads back with the reader. The level-0 render shows Port Phillip Bay from the coastline, the Yarra and Maribyrnong, the docks, Albert Park Lake, Royal Park / Yarra Bend, racecourses, cemeteries, hospitals, universities, shopping centres and the rail network. Level 1 adds the simplified overview roads with route-number labels.

Level-0 content for the test box: 5,002 parks, 652 lakes, 89 river areas, 527 sports grounds, 187 reserves, 129 shopping centres, 75 hospitals, 56 universities, 50 golf courses, 25 cemeteries, 42 beaches, 2 airports, 1,293 rail lines, 667 rivers / creeks, 105 suburb labels, plus ocean and land.

## Header Zip_Statistic = buffer sizes

The 8-byte "Zip_Statistic" in the feature-file header is **(largest stored parcel, largest raw parcel)** over the whole file. Factory: (57,420, 116,000), exactly the file's maxima. The unit presumably sizes its buffers from these, so:
- `feawrite.encode_fea()` now **computes** it from the data written. The factory round trip is still byte-identical.
- `osm_fea` keeps every parcel **≤ 110,000 bytes raw and ≤ 57,000 stored**, below the factory maxima. OSM is much denser than the 2010 data (unbudgeted CBD parcels reached 208 KB), so the smallest non-essential features are dropped until a parcel fits: 2,357 of them in the test box, nearly all tiny pocket parks. Ocean, land and bays are never dropped.

## OSM → feature types

| OSM | cat:type |
|---|---|
| `natural=water`, `landuse=reservoir` | 2:75 lake |
| `waterway=riverbank`, `water=river/canal` | 2:90 river area |
| `boundary=national_park` / protect_class 2 | 2:10 (2:12 if named STATE PARK) |
| `leisure=nature_reserve`, other `boundary=protected_area` | 2:14 |
| `leisure=park/garden`, `landuse=recreation_ground` | 2:55 park |
| `leisure=golf_course` | 2:204 |
| `leisure=stadium/sports_centre/track`, `landuse=racecourse` | 2:210 |
| `landuse=cemetery`, `amenity=grave_yard` | 2:211 |
| `amenity=hospital` | 2:201 |
| `amenity=university/college` | 2:208 |
| `shop=mall` | 2:206 |
| `aeroway=aerodrome` | 2:150 |
| `natural=beach` | 2:214 |
| `place=island/islet` | 2:110 |
| `natural=bay` | 2:80 |
| `waterway=river/stream` | 1:90 |
| `waterway=canal/drain` | 1:95 |
| `railway=rail` (not yard / siding / spur) | 1:200 |
| `place=city/town/suburb/village` points | 2:35 label (2-point feature, like the factory) |
| roads rank ≥ 2 (level 1), ≥ 3 (level 2), 4 (levels 3–4) | 8:190, `ref` → 16:195 route label |

**Ocean and land** are drawn the way the factory does it: an ocean square (2:125, flags 1) on every parcel that isn't entirely land, then the land mass (2:110, flags 3) clipped to the parcel on top. Land comes from joining all `natural=coastline` ways into closed rings (16,530 ways for Australia). Parcels entirely on land get neither.

Geometry: polygons are clipped to the parcel with Sutherland–Hodgman, lines with Liang–Barsky (pieces joined with the (0,0) separator). Douglas–Peucker tolerance per level is 1 / 3 / 15 / 60 / 150 units of 10⁻⁵°. Small features are dropped at coarser levels. Consecutive duplicate points are removed, since a zero step would read as a separator.

## Not yet done

- Only parcels inside the chosen bounding box are filled; the rest of the grid stays empty. A full-Australia run is next.
- Inner rings (holes) of multipolygons are ignored. Lakes inside parks are still drawn, because lakes come later in the draw order.
- State borders (1:182) and built-up-area polygons (2:35 polygons) aren't generated.
- OSM ways aren't merged, so long rivers and rail lines are many small features.
- Levels 3–4 cover the world in the factory file; from an Australia extract they only get Australian content.
- Whether the unit's land colour (type 110) matches its background colour needs a car test.
