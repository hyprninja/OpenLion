# Phase 3f: Area/line features (`auz.fea`)

Date: 2026-11-04. Code: `tools/features.py`, `tools/render_fea.py`. Render check: `tools/render_fea.py` (renders of factory data are not distributed).

## Summary

`auz.fea` holds the map background: water, parks, landuse, rail, boundaries, suburb labels, plus a simplified road network for zoomed-out views. It's **big-endian**, unlike every other table. It is fully decoded structurally: **all 192,282 non-empty parcels across 5 levels decode and frame exactly**, and renders of Melbourne and Victoria are correct.

## File layout

```
FeatureFileHeader (100 B)   5 x char[16] stamps ("(*$SIEMENS&^)", "(C)1996 Siemens", "DB", "10.02", "10.00"),
                            u32 dbID, u32 fileID, u32 fileCnt (=5), 8 B zip statistic
MapHeader x 5 (84 B each)   i32 min_long, min_lat, max_long, max_lat
                            i32 parcel_w, parcel_h, parcel_cnt_x, parcel_cnt_y, norm_x, norm_y,
                            i32 feaType, u32 scaleCnt, u32 scales[8] (first scaleCnt valid),
                            u32 offset (absolute) of the level's parcel grid
Parcel grid                 parcel_cnt_x * parcel_cnt_y x {u32 offset, u32 byteCnt, u32 byteCntZip}
                            row-major from the south-west: index = iy * cnt_x + ix
                            byteCntZip == byteCnt -> stored raw, else zlib
```

| Level | Scales | Parcel (deg) | Grid | Non-empty | Coverage |
|---|---|---|---|---|---|
| 0 | 50–500 | 0.16 × 0.08 | 563 × 511 | 120,099 | AU/NZ region |
| 1 | 1k–5k | 0.2 × 0.15 | 450 × 273 | 54,976 | AU/NZ region |
| 2 | 10k–20k | 1.0 × 0.75 | 90 × 55 | 2,536 | AU/NZ region |
| 3 | 50k–100k | 4 × 2 | 180 × 85 | 12,349 | world |
| 4 | 200k–500k | 8 × 6 | 90 × 29 | 2,322 | world |

## Parcel contents

```
u32 feaCnt
feature:
  u8 category   1 line, 2 polygon, 4 point, 8 road, 16 label
  u8 type       (table below)
  u8 flags, u8 scaleFlag (bit per scale of the level, 0x0F = all four)
  u8 lenAtt, u8 nameCnt?, u8 ?, u8 lenNames
  u32 feaPointCnt
  attribute[lenAtt]   line (6):  priority, dimension, labelPoint(i16,i16; 0x8000 = none)
                      poly (10): priority, dimension, labelPoint, centrePoint
                      point (2): priority, dimension
                      road (3):  segRank, segClass, segLandMark
                      label (4): label_type (rank:2, name_type:3), segClass, segLandMark, countryID
  names[lenNames]     each {u8 trans (0x04), u8 len, text}
  points              feaPointCnt x (i16 dx, i16 dy), big-endian
```

**Coordinates:**
- First point is relative to the **parcel centre**. The rest are cumulative, **with 16-bit wrap-around**: steps over 32,767 wrap, which is used for full-parcel edges.
- Real offset = value × `parcel_w / norm_x` (x) and × `parcel_h / norm_y` (y), in degrees × 10⁵. That's 1:1 on level 0 and 0.4 / 0.3 on level 1. Verified on six lakes shared between levels 0 and 1: origin = centre exactly, scale 0.400 / 0.300.
- A **(0,0) delta separates parts**; the next delta is a pen-up move. Lines use it for pieces clipped at the parcel edge, polygons for multi-part areas. With this rule, 96% of large line jumps are explained; the rest are long straight runs.
- Polygons are clipped to the parcel, so edges run along the parcel boundary.

## Feature types (from names and known landmarks)

| cat:type | Meaning | Evidence |
|---|---|---|
| 2:125 | ocean / open sea | named TASMAN SEA, PACIFIC OCEAN; full-parcel squares offshore |
| 2:80 | bay | PORT PHILLIP BAY, WESTERN PORT BAY |
| 2:75 | lake / reservoir | LAKE EYRE NORTH, ROSSLYNNE RESERVOIR, Albert Park Lake |
| 2:90 | river (area) | MURRAY, YARRA, MARIBYRNONG |
| 1:90 | river / creek (line) | |
| 2:110 | island / land mass | HERRING ISLAND, SWAN ISLAND; continents at levels 3–4 |
| 2:10 / 2:12 / 2:14 | national park / state park / reserve, state forest | DANDENONG RANGES NP, ARTHURS SEAT SP |
| 2:55 | park | YARRA PARK (MCG), ROYAL PARK, the Zoo, BATMAN PARK |
| 2:204 | golf course | ALBERT PARK GOLF COURSE |
| 2:210 | sports ground / racecourse / stadium | Flemington, Caulfield, Docklands |
| 2:211 | cemetery | Melbourne General |
| 2:201 | hospital | Royal Melbourne, Alfred |
| 2:206 | shopping centre | Highpoint, Chadstone |
| 2:208 | university / education | University of Melbourne |
| 2:150 | airport | MELBOURNE INTERNATIONAL AIRPORT |
| 2:214 | beach (probable) | Torquay, Anglesea, Rye coast |
| 2:35 | built-up area + suburb/locality labels | RICHMOND, FOOTSCRAY (2-point named entries are labels) |
| 1:200 | railway | Melbourne network, Flinders St yards |
| 1:182 | state / country boundary | Murray River border, line to Cape Howe |
| 1:95 / 2:95 | channel / canal (probable) | Wimmera irrigation channels |
| 1:202 | site lines (car parks?) | tiny lines inside Highpoint |
| 1:155, 2:207 | unknown | |
| 8:190 | overview road network | names `1`, `SH 1`, `A1`; levels 1–4 |
| 16:195 | route shield labels | `B110`, `A1` |

## Notes for the converter

- This file is the easiest part to regenerate from OSM: `natural=water/coastline`, `landuse`, `leisure=park/golf_course/pitch`, `amenity=hospital/university`, `aeroport`, `railway=rail`, `boundary=administrative`, `place=suburb` labels. The ocean comes from OSM coastline (land/water polygons).
- The overview roads (8:190) must be generated per level from OSM `motorway/trunk/primary` with simplification, and route refs from `ref`.
- Draw priority and dimension in the attribute decide drawing order; copy the factory values per type.
- `trans` = 0x04 as in `.str`.
- Navteq-derived content is not shipped. An OSM build rewrites this file entirely.
