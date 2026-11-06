# 22. Speed limits from government open data (Victoria, WA, NSW, ACT, New Zealand)

OSM has a `maxspeed` tag on about a third of Australian road segments. Some
governments publish their whole speed limit register as open data. This report adds
five of them as optional sources for roads OSM has no limit for: Victoria, Western
Australia (see [Western Australia](#western-australia)), New South Wales (see
[New South Wales](#new-south-wales)), the ACT (see [ACT](#act)) and New Zealand (see
[New Zealand](#new-zealand)).

## Source

Victorian Department of Transport and Planning, **Speed Zones**
([DataVic](https://discover.data.vic.gov.au/dataset/speed-zones),
licence **CC BY 4.0**, a new GeoJSON every month). The September 2026 file:

| | |
|---|---|
| Size | 477 MB GeoJSON, 471,826 features |
| Coverage | every road in Victoria, 432,000 km of zone (both directions counted) |
| Fields used | `speed_limit`, `zone_conditions`, geometry |
| Speeds | 50 (238k features), 100 (138k), 40 (43k), 60 (29k), 80 (18k), others rare |

- Most roads come as **two features with identical geometry**, one per travel
  direction (`direction` = compass point of travel). The geometry's own direction
  is arbitrary: half are digitised against their label. The two directions disagree
  on speed in 309 of ~183k pairs.
- The dataset records **the lowest limit that applies at any time of day**. A school
  zone that's 40 for part of the day is recorded as 40. Features whose
  `zone_conditions` aren't "Normal Operation" (School, Shop, Seasonal, Side Road
  Activated, Other, or anything in `zone_conditions_other`) are therefore dropped:
  9,029 features.

## How it works (`openlion/gov_speed.py`)

1. **prepare**: GeoJSON → compact binary (27 s, 21 MB). Features are read one at a
   time. Direction pairs are merged into one line at the lower of the two speeds,
   since the card stores one limit per segment. Lines are simplified
   (Douglas-Peucker, ~2 m). Result: 278,476 lines, 2.47 M points.
2. **Index**: a grid of ~140 m cells. Each sub-segment goes into every cell within
   16 units of it, so a query reads one cell (median 2 candidates). It takes 23 s
   to build, and Victoria fits in a few hundred MB.
3. **lookup(piece)**: up to 3 samples on a short road piece, or one per 100 m (up
   to 10) on a long one, kept away from the ends where cross streets meet. A sample
   matches the nearest zone line within **12 m** that runs within **30°** of the
   road. The piece gets a limit only when one speed has **≥ 60 %** of all samples
   and **≥ 75 %** of the matched ones.
4. `osm_import.build_graph(..., speed_fill=...)` asks for a limit only where OSM
   has none, and never for service roads or ramps (`*_link`): they run beside bigger
   roads and would pick up those roads' limits. OSM's own tags always win.

Pipeline: `--speed-zones <file>` on `openlion.pipeline` and `openlion.make_card`
(a `.geojson` is prepared into the work folder once; a `.bin` is used directly).
`python -m openlion.gov_speed fetch-vic <folder>` downloads the newest monthly file
(found through the DataVic CKAN API).

## Results: all of Victoria (`tests/gov_speed_check.py`)

OSM graph of the Victoria bbox (595k ways). The bbox also takes in parts of NSW,
the ACT and SA, which have no zones, so the "neither" and "OSM only" counts include
roads outside Victoria. Lookups for ~1 M segments take 43 s.

| | segments | km |
|---|---:|---:|
| OSM and zones agree | 222,020 | 52,458 |
| OSM and zones disagree | 35,952 | 4,754 |
| OSM only | 90,039 | 26,380 |
| **Filled from zones (OSM had nothing)** | **377,153** | **132,257** |
| Neither | 319,946 | 110,049 |

**Agreement where both have a limit: 86.1 %** (street 88 %, highway 89 %, freeway
89 %, arterial 82 %, ramps 67 %, which is why ramps are left out).

Filled km by speed: 100 → 105,736 km (mostly rural and forest roads), 50 → 20,160,
80 → 2,307, 60 → 2,112, 40 → 1,584, others < 200 each.

Disagreements, OSM → zones: 50 → 40 is the largest group (8,225 segments, nearly all
residential streets, e.g. around Rowville and Knox). That fits councils' newer
40 km/h local area zones that OSM hasn't caught up with. Next are 60 → 50 (5,000),
40 → 50 (3,409) and 50 → 60 (2,446). None of these change the card, because OSM
wins where it has a value.

Unmatched freeway and highway segments are mostly outside Victoria. Inside it, the
misses are mainly divided roads where OSM's carriageway sits 12–20 m from the zone
line. The radius was kept at 12 m so parallel service roads don't pick up the main
road's limit.

## Results: Greater Melbourne card

`make_card --bbox 144.5 -38.3 145.6 -37.5 --speed-zones vic_speed.bin` onto the MY12
card: 440,833 segments, **341,391 with a speed limit (77 %)**, of which 151,059 come
from the zones. OSM alone gives 190,332 (43 %). The zone stage adds ~30 s and the
roads stage peaks at 0.8 GB. `card_check`: 0 problems.

## Results: full Australia card, car test

Full AU build onto the MY12 card with the Victorian zones: 3,223,311 segments,
1,440,755 with a speed limit (45 %, up from 34 % with OSM alone), 349,547 of them
from the zones. Build time 21 min. `card_check`: 0 problems.

**Car-tested 2026-11-06 on a 2 GB card: works.** Guidance, address search and
speed warnings all work. The unit now warns about the limit on a street where the
OSM-only maps never did. The same build on a 16 GB card failed in the car: the car
lost track of the car's position and address entry didn't work. The 2 GB card shows
it was the card (faulty, or a card the unit doesn't accept), not the build.

## App: Speed data step

A new wizard step, **Speed data**, comes after Get OSM data. It lists every state and
territory with a checkbox. Only Victoria can be ticked so far; the others say "Coming
in a future version". Ticking Victoria asks the DataVic catalogue (CKAN
`package_show`) for the newest GeoJSON. Data files are only followed to the Victorian
government hosts, over HTTPS. **Download** fetches the file (a stopped download
resumes), runs `python -m openlion.gov_speed prepare`, and keeps only the 21 MB
`zones.bin` in `%LOCALAPPDATA%\OpenLion\speed\vic`. The choice is saved in
`state.json` (`SpeedZones`). The build passes `--speed-zones`, and its saved key
includes the data file, so a new month triggers a rebuild. With only New Zealand on
the card, the step just says there's nothing to choose. Tested in the app: the page,
the catalogue check, and the download and prepare (about 1 minute). Not yet tested:
a full build started from the app.

## Western Australia

Main Roads WA, **Legal Speed Limits** (State and Local Roads), on the
[WA data catalogue](https://catalogue.data.wa.gov.au/dataset/mrwa-legal-speed-limits),
licence **CC BY 4.0**, refreshed weekly from Main Roads' road database (IRIS). It is
served by an ArcGIS map server (`RoadAssets_DataPortal/MapServer/8`), not as one
file. The catalogue's GeoJSON link goes to a web page.

| | |
|---|---|
| Sections | 69,821 (66,054 local roads, 3,767 state roads) |
| Fields used | `SPEED_LIMIT` ("60km/h"), geometry; `CWY` is Single, or Left/Right for each side of a divided road |
| Default rule only | **53,728 sections (77 %)**: "50km/h applies in built up areas or 110km/h outside built up areas" |
| Signposted | 16,093: 50 (6,078), 60 (2,591), 40 (1,800), 70 (1,757), 110 (1,398), 80 (1,355), others fewer |

- The default-rule sections are **left out**. They aren't a signed limit, and the
  data doesn't say which half of the rule applies (the same reason statutory
  defaults were declined on 2026-11-05).
- Part-time zones (school, children's crossing) are a separate layer
  (`MapServer/9`, "Legal Speed Zones", 6,523 sections) and aren't used.
- Main Roads notes the layer "may not be current or accurate" and that the road sign
  is the only enforceable source. The agreement check below is how far it was
  trusted.
- The map server gives no last-changed date, so the catalogue's `metadata_modified`
  date names the version ("Legal Speed Limits 5 November 2026").

`gov_speed.fetch_wa(folder)` asks the server for the signposted sections only
(`SPEED_LIMIT NOT LIKE '%built up%'`) as GeoJSON in EPSG:4326, page by page, and
writes them to one file (16 MB, a few seconds). `prepare()` now reads either state's
fields (`feature_speed()`): 16,093 lines in 2 s, 1.4 MB prepared.
`python -m openlion.gov_speed fetch-wa <folder>` runs it from the command line.

### Results: WA bbox (`tests/gov_speed_check.py`)

OSM graph of 112.9,-35.2 to 129.0,-13.7 (240k ways); lookups 11 s.

| | segments | km |
|---|---:|---:|
| OSM and WA agree | 62,713 | 22,083 |
| OSM and WA disagree | 7,556 | 1,618 |
| OSM only | 52,324 | 28,129 |
| **Filled from WA (OSM had nothing)** | **27,581** | **8,248** |
| Neither | 326,984 | 205,812 |

**Agreement where both have a limit: 89.2 %** (freeway 98 %, highway 92 %, arterial
87 %, street 86 %). Filled km by speed: 110 → 3,923, 50 → 1,407, 70 → 731,
60 → 660, 80 → 469, 90 → 418, 100 → 364, 40 → 265. The check counts ramps, which the
build skips. Largest disagreements, OSM → WA: 50 → 40 (1,178), 70 → 60 (1,060),
80 → 70 (854), 50 → 60 (812).

WA adds far less than Victoria (8,200 km against 132,000 km): three quarters of its
sections only restate the default rule.

**Perth card** (`make_card --bbox 115.6 -32.4 116.2 -31.6` with both the Victorian and
WA files): 227,624 segments, 74,914 with a limit (32.9 %), of which 4,158 come from
WA, against 31.1 % from OSM alone. Most of Perth's suburban streets are default-rule
sections. `card_check`: 0 problems. Passing two `--speed-zones` files works.

### App

Western Australia is a second checkbox on the Speed data step. The app reads the
catalogue date to tell whether there's newer data, then has the engine download it
(`SpeedZoneSource.EngineFetch = "fetch-wa"`). Progress counts road sections, not MB.
The engine's file must land in the state's own folder. Then the file is prepared
like Victoria's (`%LOCALAPPDATA%\OpenLion\speed\wa\zones.bin`). The states still to
come (NSW, Queensland, Tasmania, the ACT) are listed together in one compact box, so the
page fits without scrolling. South Australia and the Northern Territory aren't listed:
they don't provide their speed limits in an open format, and a note on the page says so.
Fixed along the way: ticking a second state while the first was being checked
skipped the second check.

## New South Wales

Transport for NSW, **Speed Zones**, on the
[TfNSW Open Data Hub](https://opendata.transport.nsw.gov.au/data/dataset/speed-zones)
(CKAN; no API key needed for the file downloads), licence **CC BY**. It's one GeoJSON
of lines, `speed_zones.geojson` (373 MB, 447,538 sections). The file keeps the same
name; the version we used was updated 6 November 2026. The catalogue labels it
format "data", not "GeoJSON".

| Type | sections | km | used |
|---|---:|---:|---|
| Default | 427,623 | 139,642 | yes: 50 or 100, the default Transport for NSW applies to that road |
| Ordinary Permanent | 15,403 | 64,366 | yes |
| High Pedestrian, Shared, Local Traffic | 783 | 1,028 | yes (permanent) |
| School, School Bus, Variable, Wet Weather, Toll Plaza | 3,729 | 2,377 | no: part-time or conditional |

- Fields are `sz_type`, `sz_status` (all "Existing"), `sz_direction` (almost all "Both
  Directions") and `sz_speed` ("60 km/h").
- Unlike WA's default-rule sections, NSW's "Default" sections carry one definite value
  for the road.
- `prepare()` reads them with `feature_speed()` (kept types in `NSW_KEEP`): 441,650
  lines, 22 MB, 27 s. 2,073 sections have no usable shape.

### Results: NSW bbox (`tests/gov_speed_check.py`)

OSM graph of 140.99,-37.51 to 153.64,-28.15 (639k ways; the box also takes in the ACT
and parts of Victoria, SA and Queensland). Lookups: 47 s.

| | segments | km |
|---|---:|---:|
| OSM and NSW agree | 260,099 | 85,928 |
| OSM and NSW disagree | 29,413 | 5,967 |
| OSM only | 98,972 | 42,843 |
| **Filled from NSW (OSM had nothing)** | **274,986** | **107,518** |
| Neither | 430,686 | 214,241 |

**Agreement where both have a limit: 89.8 %**, the best of the four (highway 93 %,
freeway 92 %, street 91 %, arterial 89 %). Filled km by speed: 100 → 81,939,
50 → 19,907, 80 → 2,295, 60 → 2,137, 40 → 597. Largest disagreements, OSM → NSW:
60 → 50 (4,397), 40 → 50 (3,372), 50 → 100 (2,823), 50 → 60 (2,147). Roads OSM tags 50
or 60 but NSW has at 100 are about 1.4 % of the compared segments.

**Sydney card** (`make_card --bbox 150.6 -34.2 151.4 -33.6 --speed-zones nsw_speed.bin`):
244,656 segments, 199,809 with a limit (**81.7 %**), of which 93,386 come from NSW,
against 43.5 % from OSM alone. `card_check`: 0 problems.

### App

New South Wales is a checkbox on the Speed data step, downloaded by the app from the
catalogue like Victoria's file. The catalogue reader now recognises a GeoJSON by its
`.geojson` file name as well as its format label. Since the file name never changes,
a source with `DataName` gets the catalogue date in its file name and title
(`speed_zones_2026-11-06.geojson`, "Speed Zones 6 November 2026"), so a newer file is
seen as new. Download and prepare take ~40 s in the app (22 MB kept). With all four
sources on an Australia + New Zealand card, the page still fits without scrolling.

## ACT

The ACT open data portal's "ACT Speed Zones" (`hy95-2hum`) wasn't used: its rows were
last updated on 13 November 2020. The ACT Government's ArcGIS service has a newer one,
**Road Network Speed Zones**: `ACTGOV_Road_Network_2025_Speed_Zones_view`. It's a
"static view of the road network speed zones in 2025", last edited 16 October 2025,
licence **CC BY 4.0** (Australian Capital Territory). It's marked "indicative only".

| | |
|---|---|
| Sections | 45,447, one per direction (`CARRIAGEWAY` ASCENDING / DESCENDING); ~3,700 km of road |
| `SPEED_ZONE` | 50 (34,756), 60 (4,907), 40 (1,887), **SCHOOL ZONE (1,703, left out)**, 80, 100, 70, 20, 90, 10, 15, 25, 110 |
| Roads | Municipal and Territory roads, plus private, university, hospital and defence roads |

Each year is a new service, so `latest_act()` picks the newest year from the service
list. It retries, because the list now and then comes back empty for a moment; the
app's first check hit that once. `fetch_act()` pages the layer without the school
zones: 43,744 sections, about 2.5 min (the server is slow). `prepare()` merges each
road's two directions into 22,936 lines (18 pairs disagree on speed) in 3 s, 0.9 MB.

### Results: ACT bbox

OSM graph of 148.76,-35.92 to 149.40,-35.12 (41k ways); lookups 3 s.

| | segments | km |
|---|---:|---:|
| OSM and ACT agree | 15,700 | 1,361 |
| OSM and ACT disagree | 1,745 | 141 |
| OSM only | 6,852 | 817 |
| **Filled from the ACT (OSM had nothing)** | **23,894** | **2,347** |
| Neither | 16,092 | 3,780 |

**Agreement where both have a limit: 90.0 %** (freeway 94 %, highway 93 %, arterial
90 %, street 88 %). Filled km: 50 → 1,804, 100 → 345, 60 → 128, 40 → 48. Largest
disagreements, OSM → ACT: 60 → 50 (550), 50 → 60 (165), 60 → 80 (148), 80 → 60 (132).

**Canberra card** (`make_card --bbox 148.76 -35.92 149.40 -35.12 --speed-zones
act_speed.bin`): 64,481 segments, 47,158 with a limit (**73.1 %**), of which 22,815
come from the ACT, against 37.8 % from OSM alone. `card_check`: 0 problems.

### App

The Australian Capital Territory is a checkbox on the Speed data step. The engine
downloads it (`fetch-act`). The app reads its version from the service list
(`SpeedZoneSource.YearlyService`: the newest year, "Road Network Speed Zones 2025"),
retrying an empty list twice. Only Queensland and Tasmania are still in the
coming-soon box. With all five sources on an Australia + New Zealand card, the page
still fits without scrolling.

## New Zealand

NZ Transport Agency Waka Kotahi, **National Speed Limit Register** (NSLR), licence
**CC BY 4.0**: the legal register itself. Since 2022 a limit is in force only once
it's in the register. It's an ArcGIS feature service
(`SpeedLimitZoneFull__View/FeatureServer/0`, 71,060 records). Unlike Victoria and
WA, the zones are **polygons**, not lines:

- About 15,000 specific zones, mostly thin corridors around a road (median width
  ~23 m), plus town-wide "Urban Traffic Area" zones.
- 3,465 large **"Default Area"** zones underneath, all 100 km/h. They cover the
  rural default.
- The smallest zone holding a point wins.

What's used:

| | records |
|---|---:|
| All records (the register keeps its history) | 71,060 |
| In force now (`whenIneffective` empty or in the future, `whenEffective` past) and **Permanent** | 18,763 |
| Left out: Variable (school zones), Seasonal, Temporary | |
| Dropped when prepared: slivers left over from edits (median area 0 m², max 197 m²) and 91 with no geometry | 1,308 |
| **Zones used** | **17,455** (3,010 of them background) |

`fetch_nz(folder)` pages the service (500 at a time, GeoJSON, EPSG:4326; about
2.5 min, 181 MB). The version is named from the layer's `editingInfo.dataLastEditDate`
("National Speed Limit Register 6 November 2026"). `prepare()` sees polygons and
writes the area file type (`OLGSPA1`; Douglas-Peucker at ~2 m): 1.6 M points,
13 MB, 34 s.

**AreaIndex** (loaded by `open_index()`, so `load()` and the pipeline take either
type):

- Zones over ~4.5 km go on a coarse grid (~1.1 km cells), the rest on a fine grid
  (~140 m). A zone is stored in the cells its boundary passes through (padded 16
  units), and in the cells wholly inside it.
- In a boundary cell, a point is tested exactly, by ray casting over just the
  zone's edges in that cell's row. Cells wholly inside need no test.
- A point inside only the 100 km/h background, but within **15 m** of a specific
  zone, is left **unanswered**. A road traced a few metres off its 23 m corridor
  must not fall through to 100.
- **Tracks** (`highway=track`) don't take the background: it covers every farm and
  forestry track, most of them private. Without this, 46,000 track segments got
  100 and agreement on tracks was 41 %. A specific zone on a track still counts.

Lookups use the same sampling and vote as the lines (`Index.lookup`; `speed_fill`
now also gets the highway type). Building the index takes 11 s; looking up all of
NZ's ~490k segments takes ~2.7 min.

### Results: all of NZ (`tests/gov_speed_check.py`)

| | segments | km |
|---|---:|---:|
| OSM and register agree | 126,689 | 27,467 |
| OSM and register disagree | 16,696 | 3,265 |
| OSM only | 72,863 | 10,497 |
| **Filled from the register (OSM had nothing)** | **159,498** | **55,097** |
| Neither | 147,909 | 66,548 |

**Agreement where both have a limit: 88.4 %** (highway 94 %, freeway 93 %, arterial
89 %, street 85 %, track 75 %). Filled km by speed: 100 → 42,272, 50 → 6,469,
60 → 2,644, 80 → 2,023, 40 → 715. The two biggest disagreements are opposite
ways round, 30 → 50 (1,876) and 50 → 30 (1,417). That fits recent changes OSM
hasn't caught up with, in both directions (many 30 km/h limits were reversed under
the 2024 speed limit rule). Next come 100 → 80 (1,114) and 50 → 100 (1,023,
mostly rural roads OSM tags 50).

**Wellington card** (`make_card NZ=... --bbox 174.6 -41.4 175.2 -40.9 --speed-zones
nz_speed.bin`): 28,092 segments, 17,326 with a limit (61.7 %), of which 2,232 come
from the register, against 53.7 % from OSM alone. `card_check`: 0 problems.

### App

New Zealand is offered **only when New Zealand is on the card** (New Zealand alone,
or Australia and New Zealand on the Get OSM data step). The Australian states are
only offered when Australia is on it. `SpeedZoneSource.CountryCode` ties each source
to its map country. A source that's ticked but hidden stays remembered, but isn't
used. With New Zealand on the card the heading reads "Which areas?". The
coming-soon box and the SA/NT note only show with Australia. The engine downloads NZ
(`fetch-nz`), like WA. Download plus prepare takes ~3 min in the app and keeps
13 MB. Each source's status now sits on one line, and the credits are one
paragraph. The default window is 1040×900, so all three sources fit without
scrolling.

## Not done

- Queensland and Tasmania (their open data is stale or partial).
- Car tests of cards with WA, NSW, ACT or NZ limits.
