# Phase 8: Points of interest and Canberra

Date: 2026-11-05. Modules: `openlion/osm_poi.py` (new) and `openlion/osm_names.py` (municipalities). Both run inside `python -m openlion.pipeline`.

## POI database format (`edb/POI/aunz_sql.db3`)

The POI database is plain SQLite 3 (page size 8192, UTF-8, schema format 4, journal mode DELETE), written by Navteq's `svdconv 1.076_P4`. The unit doesn't run free SQL. It calls 28 named statements stored in the database itself (`PredefinedStatement`): 20 compiled scripts in a `Par3` blob (`GetPoisAroundPoint`, `GetPoisInRectangle`, `GetPoiAttributes`, …) plus 8 built-ins. So the schema, table names and column order must stay exactly as they are.

| Table | Rows (factory) | Content |
|---|---|---|
| `Poi_BaseAttributes` | 239,807 | `Poi_ID`, `PoiPartition_ID` (category), `Coordinate`, `Name_String_ID`, `Name`, `PhoneNumber` (`61396704506`) |
| `Poi_AddressAttributes` | 238,624 | string IDs for house number, street, village, city, region (state), country, ISO country and phone (`+(61)-3-96704506`) |
| `Poi_AlertAttributes` | 238,624 | `SpeedLimit`, `Direction` (degrees), `TimeStamp`, `CameraType`, `FixtureStatus`, `SeverityRating`; all NULL except road-sense alerts |
| `Poi_Relation` | 242,464 | type 0: 72,723 POIs → 1,183 "Link Point" POIs (category 82) |
| `Poi_VSegAttributes` | 0 | POI → road segment links: empty, so POIs aren't tied to the road network |
| `String_BaseAttributes` | 263,361 | IDs 1–421 are category, icon and language names; the rest are POI strings |
| `Category_*`, `PoiPartition_*`, `Icon_BaseAttributes` | 83 / 83 / 274 | 70 leaf categories under 11 groups (shopping, auto, services, food, …) plus road-sense, link and entry points. Partition ID = category ID; partitions carry the icon, map zoom level and priority |
| `Language_BaseAttributes`, `String_TranslationString_Relation`, `DatabaseAttribute` | 2 / 182 / 5 | `en`/`us`; database id `SVD_20110616082437_906` (the same id as `cdrom.toc`), schema versions core 1.01, poi 1.03 |

**Coordinate** is a 64-bit Morton code: bit 2k is bit k of x, bit 2k+1 is bit k of y, where

    x = (lon + 180°) · 2³² / 360°,   y = (lat + 180°) · 2³² / 360°

That's about 1 cm resolution. An index on `(Coordinate, PoiPartition_ID)` serves the rectangle searches. For example, a factory café decodes to 144.96012°E, 37.81379°S, which is Queen St, Melbourne.

**Strings** are uppercase ASCII, up to 69 characters. Names, streets, cities and states are shared through `String_BaseAttributes`.

## POI builder

`osm_poi.build()` copies the factory database and keeps the schema, statements, categories, partitions, icons, languages, translations, `DatabaseAttribute` and strings 1–421 exactly. It deletes every factory POI row and POI string, then inserts the OSM POIs, sorted by Morton code, and runs `VACUUM`. `compare_schema()` proves the kept parts are unchanged.

OSM tags map onto 59 of the 70 leaf categories. Not mapped: business facility, specialty store, automobile club, ski resort, Holden dealership and the six road-sense categories (`RULES` in `osm_poi.py`). Examples: `amenity=fuel` → petrol station, `restaurant`/`fast_food` → restaurant, `pub`/`bar`/`nightclub` → nightclub, `place=*` → city centre, `railway=station` → train station. Car parks are included only when named, multi-storey or underground. Private toilets, private airfields and unnamed parks are left out. Unnamed petrol stations take their brand; unnamed ATMs, toilets, car parks and rest areas get a generic label.

Addresses come from the OSM `addr:*` tags. The city and state come from the OSM suburb and state boundaries, the country is AUSTRALIA, and phone numbers are converted to both factory formats.

Result for Australia, from the 2026-11-04 extract: 292,497 matching OSM objects. 19,172 were skipped for having no name, 3,341 node-and-area duplicates were removed, and 37 lay outside the map (Heard and Macquarie Islands). That leaves **269,947 POIs** (45.7 MB). The factory file is 59 MB for AU + NZ. `compare_schema()` finds no differences in the kept parts, and SQLite's integrity check passes. Read 3 min, build 15 min.

| Category | Factory 2010 (AU + NZ) | OSM (AU) |
|---|---|---|
| petrol station | 7,003 | 7,559 |
| restaurant | 27,967 | 28,479 |
| coffee shop | 7,768 | 14,518 |
| nightclub (pubs, bars, clubs) | 3,702 | 6,868 |
| hotel | 6,055 | 5,175 |
| grocery store | 3,910 | 6,220 |
| convenience store | 1,609 | 4,789 |
| bank / ATM | 8,553 / 10,366 | 2,729 / 2,054 |
| school | 8,988 | 10,306 |
| hospital | 389 | 1,180 |
| pharmacy / medical service | 3,693 / 6,044 | 3,540 / 6,172 |
| train station / airport | 1,189 / 687 | 1,565 / 2,026 |
| park / tourist attraction | 15,459 / 1,088 | 42,575 / 7,745 |
| public toilet / campground / rest area | 483 / 51 / 336 | 17,741 / 8,729 / 2,961 |
| city centre (localities) | 20,746 | 27,841 |
| car park | 2,787 | 6,502 |

Banks and ATMs are thinner in OSM than in the Navteq data. Most other categories are equal or fuller.

Not rebuilt:
- **Road-sense alerts** (categories 54–59: school zone, speed camera, blackspot, railway crossing, check speed, school point). Their direction, camera-type and status fields aren't decoded well enough to warn correctly, so they're left empty.
- **Link and entry points** (82, 83) and **POI relations**. The factory data leaves 70% of its POIs without relations.
- **Holden dealerships** (51), since the brand is gone.

## Canberra

In the factory data, Canberra is a municipality. One city, CANBERRA, has its own street list covering all its suburbs (6,694 entries). Each of its 123 suburbs is a city named `SUBURB, CANBERRA` (`BRADDON, CANBERRA`, `PLAINS;ISABELLA, CANBERRA`, `2905, CANBERRA`) whose `cityIdMunicipality` points at CANBERRA. The factory uses the same structure for 2,030 places, mostly in New Zealand (AUCKLAND has 246 suburbs).

`osm_names.MUNICIPALITIES` rebuilds this for Canberra: every OSM suburb whose centre lies in the ACT becomes `SUBURB, CANBERRA`, plus one CANBERRA city whose street list holds every ACT street. Typing CANBERRA gives the whole city, and typing BRADDON finds `BRADDON, CANBERRA`.

## Test card v2

`python -m openlion.pipeline` now writes the POI database too, and `build.py --poi` puts it on the card. The national run takes 56 minutes. Its memory use is lower now: the raw OSM ways are dropped once the graph and the overview-road lines exist, because the previous run died silently with about 26 GB of memory free.

The card is `testcard_v2/navigation/` (571 MB). It replaces 18 files: the 17 map tables and the POI database. The other 2,207 files are byte-identical to the factory card. `tests/card_check.py <card> <factory navigation/>` passes with 0 problems:
- every tile frames cleanly
- all name links resolve
- the `.fea` parses
- lookups work in all 8 capitals, including CANBERRA / NORTHBOURNE AVENUE and BRADDON, CANBERRA / LONSDALE STREET
- the POI database passes the integrity check, with no dangling strings or categories, and every POI lies within 10 km of the road map
- the factory POI structure is unchanged

Still to test in the car: POI search (nearby, by category, by name), POI icons on the map, and CANBERRA in address entry.
