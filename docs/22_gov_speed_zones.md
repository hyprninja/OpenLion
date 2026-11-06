# 22. Speed limits from government open data (Victoria, Western Australia)

OSM has a `maxspeed` tag on about a third of Australian road segments. Some state
governments publish their whole speed zone register as open data. This report adds
two of them, Victoria and Western Australia (see [Western Australia](#western-australia)),
as optional sources for roads OSM has no limit for.

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
come are listed together in one compact box, so the page fits without scrolling.
Fixed along the way: ticking a second state while the first was being checked
skipped the second check.

## Not done

- Other states. NSW is the next candidate; NZ's National Speed Limit Register is
  also current and openly licensed.
- Car test of a card with WA limits.
