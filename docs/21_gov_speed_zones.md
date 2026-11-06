# 21. Speed limits from government open data (Victoria)

OSM has a `maxspeed` tag on about a third of Australian road segments. Some state
governments publish their whole speed zone register as open data. This report adds
the first one, Victoria, as an optional source for roads OSM has no limit for.

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

## Not done

- Other states (NSW is the next candidate; WA and NZ are also current, statewide
  and openly licensed).
- Attribution: the card and README must credit "Speed zone data © State of
  Victoria (Department of Transport and Planning), CC BY 4.0" whenever this source
  is used.
