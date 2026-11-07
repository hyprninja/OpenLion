# 27 School zones, level crossings and New Zealand's LINZ data

Four open datasets fill gaps the earlier reports left: school zone and level crossing warnings (empty road-sense categories since report 23), and New Zealand house numbers and suburbs (OSM NZ has few of the first and no boundaries for the second, report 18). Every source here is CC BY 4.0, downloaded by the engine at build time into the app's `govdata` folder (`make_card --gov-data <dir>`), with the last good copy used when a download fails, like the speed cameras.

## How the factory cards store school zones and level crossings

They're POIs in the **road sense** parent category, beside "camera". Category IDs differ per card, so `osm_poi.build` files them by name, as for cameras.

| Category | MY12 | MY15 | MY17 | MY10 | `Poi_AlertAttributes` |
|---|---|---|---|---|---|
| school zone | 6,687 | 7,586 | 7,569 | 7,555 | `SpeedLimit` 40, `Direction` NULL, `TimeStamp` |
| school point | 24,586 | 24,820 | 25,048 | 51,714 | `SpeedLimit` 40, `Direction` = travel heading, `TimeStamp` |
| railway | 2,775 | 2,816 | 6,138 | 5,635 | `SpeedLimit` NULL, `Direction` = travel heading, `TimeStamp` |

`CameraType`, `FixtureStatus` and `SeverityRating` are NULL in all three. POI names are "SchoolZone", "SchoolPoint" and "Railway" (upper case on MY15/MY17, none on MY10).

Where they sit (MY12, Canberra): a **school zone** POI marks the zone, near the school; its **school points** are 40–200 m away, each with a heading pointing into the zone along a road: one per way into it. **Railway** POIs are on the crossing with the heading of the traffic approaching it. MY10 has a pair per crossing at the same spot (90 and 270); MY12 mostly one, and a pair (180° apart) at about a fifth of them.

OpenLion writes the same rows: one school zone POI per zone, one school point per road entry and direction, and one railway POI per direction a road crosses the line (one on a one-way road).

## Sources

| What | Where | Source | Notes |
|---|---|---|---|
| School zones | NSW | Transport for NSW "School Zones data" (`schoolzones.zip`, 3.7 MB) | 3,302 zone polygons drawn around the roads, speed (40, or 30 at 7), times; NSW's OSM school zones are dropped |
| School zones | elsewhere | OSM | ways with a school-hours `maxspeed:conditional` ("40 @ (Mo-Fr 08:00-09:30,14:30-16:00; SH off)": 10,700 ways in Australia), or `hazard=school_zone` (1,900) |
| Level crossings | everywhere | OSM `railway=level_crossing` | 14,700 nodes in Australia; kept where the node is on a working railway (rail, light rail, narrow gauge, preserved, ...) and on a mapped road |
| Level crossings | NSW | Transport for NSW "NSW Level Crossings on Public Roads" (ALCAM) | 1,150 after dropping "NO ROAD" / "NO RAIL" location errors; added where OSM has no crossing within 100 m and a working OSM railway passes within ~150 m |
| Level crossings | WA | Main Roads WA "Rail Crossings" (ArcGIS layer 15) | 1,170 public crossings not marked out of use; added the same way |
| NZ addresses | NZ | LINZ "NZ Addresses" (ArcGIS Online, owner `administrator_LINZ`) | 2.4 million points, current ones only; road names from "NZ Addresses: Roads" |
| NZ suburbs | NZ | LINZ "NZ Suburbs and Localities" | 6,563 polygons; suburbs, localities, islands and conservation land kept, lakes and bays left out |

**Closed lines.** Main Roads WA's list still has crossings on the wheatbelt branch lines closed since 2014: of the 413 WA crossings with no OSM crossing on a working railway within 100 m, 237 were next to an OSM crossing on a line tagged `railway=disused` or `abandoned`, and the other 176 had no OSM crossing at all (track lifted). So the OSM read also traces every working main line (not yards, sidings or spurs) into a ~100 m grid (`rail_cells`), and an official crossing is only added within a cell of it. That drops 332 of WA's 1,170 and 16 of NSW's 1,150.

Level crossings use OSM first because an OSM crossing node is exactly where the road meets the rails, and the road it's on gives the headings directly. The government points are a few metres off and have to be snapped. Checked and left out: Queensland's level crossing dataset is a list of collisions, not crossings; Victoria's "school zones" dataset is school enrolment areas, not road zones.

LINZ publishes on its Data Service (data.linz.govt.nz, needs a free account and key) and, with the same data and licence, as public ArcGIS Online feature services that answer plain queries. OpenLion uses the second: `gov_nz.fetch` pages through them by OBJECTID (10,000 rows per request, four at a time), and re-downloads a copy older than 30 days. The suburb polygons are simplified to about 10 m on the server.

## Placing school zones

**Official polygons (NSW).** Each zone's entries are where a mapped road crosses its boundary going in: every road segment near the polygon is intersected with its edges, and walking along the segment, each crossing that takes the road from outside to inside is an entry for that direction (one-way roads give only their own direction). A segment can enter and leave in one go, so this doesn't rely on a vertex inside the zone. The zone's own POI goes on the road nearest the polygon's centre.

**OSM ways (other states, NZ).** School zone ways that share a node make one zone. Its entries are the nodes where any other mapped road meets it: a side street joining it, or the road carrying on beyond the zone. Each gives the heading of traffic arriving along that road. The zone's POI goes at the middle of its longest way.

Entries closer than 25 m with the same heading are merged.

## New Zealand addresses and suburbs

**Addresses** go through the same path as G-NAF (report 25): one point per road and number (units at one number become their mean), merged after OSM's, so a LINZ number is dropped where OSM has the same number on the same street nearby. Address ranges ("20–24") keep both ends.

**Suburbs** become level-9 boundary areas, like Australia's OSM suburbs, so a street is in the LINZ suburb it lies in rather than the nearest place point. LINZ gives each suburb its town or city (`major_name`: Te Rapa → Hamilton), and the city records list the suburb under it, factory style ("TE RAPA, HAMILTON"). Where the town is also a locality of its own (Paraparaumu), its suburbs join that city record; otherwise a city record is made over its suburbs, centred on them. Only a City or Town parent counts: LINZ also names "Major Localities" (Marlborough Sounds, Egmont National Park) as parents of rural localities, and the factory lists those on their own. OSM place points and areas stay as the fallback for anything LINZ doesn't cover.

**Gaps.** LINZ suburbs leave out rivers, so a bridge over the Waikato fell outside every suburb and went to OSM's old "Hamilton" place point, which made a second HAMILTON city ahead of the LINZ one in search (Victoria Street not found). `Localities.find` now tries points 150 m, 400 m and 1 km away in eight directions before falling back to places, for countries with LINZ suburbs only (Australian builds are unchanged).

**OSM already has New Zealand's addresses.** NZ mappers imported LINZ addresses into OSM, so of LINZ's 1.81 million numbered points that matched OSM streets, 1,797,981 were already in OSM and 15,426 were added (Hamilton: 151 of 55,162). LINZ addresses are kept because they cost little and keep the numbers current between OSM imports; the suburbs are the real gain.

## Results

Full Australia + New Zealand build onto the MY12 card (with G-NAF and the speed cameras), 2026-11-08, OSM extracts of 2026-11-03: `card_check` 0 problems, every capital-city lookup found, 663 MB, 27 minutes on the 44-thread PC.

| | School zones | Entry warnings | Level crossings | Source |
|---|---|---|---|---|
| NSW | 3,292 | 17,063 | 1,454 | TfNSW zones; OSM + 56 from the TfNSW list |
| VIC | 241 | 1,247 | 1,865 | OSM |
| QLD | 496 | 2,145 | 3,078 | OSM |
| SA | 250 | 1,238 | 688 | OSM |
| WA | 66 | 357 | 1,274 | OSM; OSM + 84 from the Main Roads list |
| TAS | 3 | 6 | 207 | OSM |
| NT | 16 | 87 | 74 | OSM |
| ACT | 20 | 107 | 0 | OSM |
| NZ | 48 | 269 | 1,555 | OSM |
| **Total** | **4,432** | **22,519** | **10,195** (16,524 warnings, one per direction) | |

The factory MY12 card had 6,687 zones, 24,586 entries and 2,775 crossing warnings. Crossings are now far more complete; school zones match the factory only in NSW (3,292 vs ~2,400 in the factory's NSW), because OSM's school zones outside NSW are patchy (Victoria 241 against the factory's ~1,700). A Newcastle test area had 234 zones and 1,123 entries against the factory's 143 and 640, the same ratio of about 4.7 entries per zone.

New Zealand: 4,295 LINZ suburbs and localities on the card, 2,263 of them listed under a town or city (Auckland 180, Dunedin 82, Hamilton 57, Lower Hutt 37, ...). 4,791 OSM level crossings were left out because they aren't on a road the card has (paths, cycleways, service roads); 112 NSW and 28 WA list crossings had no such road within 40 m.

## Checks

`tests/card_check.py` now expects "school zone" rows with a speed and no heading, "school point" rows with a speed, "railway" rows with no speed, all three with a time stamp and no camera type; "blackspot" and "check speed" stay empty. `tests/test_alerts.py` covers the tag rules, the list parsers, crossing headings on two-way and one-way roads, OSM-first crossing merging, OSM and polygon zone entries (including a road passing straight through a zone), and the LINZ readers.

## Not done

- **Not car-tested.** The rows match the factory ones, but how the unit warns (distance, whether it needs `Poi_Relation` link points as about a third of factory school points have) is unconfirmed.
- Victoria, Queensland, SA, WA, Tasmania, the NT and the ACT have no open school zone list; their zones come from OSM, which is patchy outside the cities.
- Blackspots and "check speed" (mobile camera sites) stay empty.
