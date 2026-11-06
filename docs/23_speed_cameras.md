# 23 Speed cameras

The car warns about fixed speed and red-light cameras from the POI database (`edb/POI/aunz_sql.db3`). Up to 1.2.0, OpenLion left those categories empty. This report covers how the factory cards store cameras, where OpenLion now gets them, and how it works out each camera's direction.

## How the factory cards store cameras

Cameras are ordinary POIs in the category named **camera**, under the parent category **road sense**. The category's ID differs per card: 55 on MY12, 65 on MY15 and MY17, 59 on MY10. The builder finds it by name (`osm_poi.camera_partition`).

| Table | Camera rows |
|---|---|
| `Poi_BaseAttributes` | `Name` = "Camera" (mixed case, unlike other POIs), Morton-coded `Coordinate` as for every POI |
| `Poi_AlertAttributes` | `SpeedLimit` (km/h or NULL), `Direction`, `TimeStamp` (`28-JAN-08`: the data date), `CameraType`, `FixtureStatus` = 1, `SeverityRating` NULL |
| `Poi_AddressAttributes` | street, city, state and country strings as for other POIs |
| `Poi_Relation` | about 40% of factory cameras link to "Link Point" POIs (type 0). Not rebuilt; the rest work without them |

There is **one POI per enforced direction**. A camera that checks both directions is two rows at nearly the same spot.

**Direction** is the travel heading of the traffic being checked, in degrees clockwise from north. The Hume Freeway point-to-point pair near Broadford shows this: the camera with heading 200 sits on the east carriageway and the one with heading 20 on the west. That's the southbound and northbound carriageways, driving on the left.

**CameraType** (meanings inferred from where the cameras are and their limits):

| Value | Seen on | Read as | OpenLion writes it for |
|---|---|---|---|
| 1 | freeways and highways at 100–110 km/h, point-to-point sites on MY12/MY15 | fixed speed | fixed speed, point-to-point |
| 2 | intersections, mostly 60 km/h | red light + speed | intersection cameras (VIC, ACT) |
| 0 | intersections | red light only | OSM `enforcement=traffic_signals` |
| 3, 4 | MY10 only (4 = Hume point-to-point) | | not used |

The factory cards had 1,037 (MY12), 1,337 (MY15) and 1,039 (MY10) camera POIs. The newest data is from January 2014.

## Sources

Governments that publish camera locations openly come first: a plain download, with coordinates, kept current. Every other state gets OpenStreetMap's cameras. The build downloads the official lists each time (`openlion/gov_cameras.py`) into the app's `cameras` folder. If a download fails, it uses the last good copy, and with no copy that state falls back to OSM.

| State | Source used | Why |
|---|---|---|
| VIC | vic.gov.au "Find a road safety camera" (Cameras Save Lives, CC BY 4.0): 347 fixed digital cameras | Current (annual test certificates to Oct 2026), coordinates; the camera ID ends in its direction (H138N = northbound) |
| ACT | data.act.gov.au "Traffic speed camera locations" (Access Canberra, CC BY 4.0): 28 current cameras | Official API, coordinates; decommissioned cameras are dated and left out |
| NSW | OSM | The live camera map blocks plain downloads; the open-data copy dates from 2021–2023 |
| QLD | OSM | Published as road and suburb names only, no coordinates |
| SA | OSM | Names only, and the police page blocks plain downloads |
| WA, TAS, NT | OSM | No list published |
| NZ | OSM | NZTA's list is behind a CAPTCHA |

speedcams.world was checked too. Its Australia file is OSM repackaged (planet of 21 September 2026), without OSM's direction tags, so reading our own extract is better.

**OSM read** (`cameras.read_osm`, cache `cams1_<country>.pickle`, 14 s for Australia): `highway=speed_camera` nodes with `direction` / `camera:direction` and `maxspeed`, plus `type=enforcement` relations (`maxspeed`, `traffic_signals`, `average_speed`). A relation gives the device's kind and, from its `from` and `to` members, a direction. Australia: 1,223 cameras.

## Direction and speed limit: snapping onto the road

In the roads stage, while the road network is still loaded, each camera is snapped onto the nearest drivable road (`cameras.place`; 8 s for Australia). Official points may be up to 60 m from the road (kerb or junction centre), OSM points up to 35 m. Ramps are avoided unless nothing else is near. A road whose name matches the official record's street is preferred.

- **Known direction** (VIC ID suffix or "Northbound" in the text, ACT "SOUTHBOUND", OSM `direction`, an enforcement relation): the carriageway going within 60° of it wins, and the POI takes that carriageway's exact heading.
- **OSM camera node on a way**: that way. `direction=forward/backward` is along it.
- **No known direction**: on a one-way carriageway (divided roads, tunnels), its direction. On a two-way road, one POI each way.
- **Speed limit**: the road's OSM `maxspeed`, else the camera's own `maxspeed`, else NULL.
- Cameras within 30 m with headings within 30° are kept once; official ones win.

Euroa (the camera that started this): Victoria lists H138N and H138S on the Hume Freeway at Creightons Siding Road. They land on the two carriageways at 47° and 229° (the Hume runs north-east there), at 110 km/h.

## Full-Australia result (OSM extract of 4 November 2026)

| State | Source | Camera POIs |
|---|---|---|
| NSW | OSM | 495 |
| VIC | official | 419 (347 cameras; intersections warn both ways) |
| QLD | OSM | 100 |
| SA | OSM | 182 |
| WA | OSM | 111 |
| TAS | OSM | 11 |
| NT | OSM | 25 |
| ACT | official | 28 |

The official lists replaced 324 OSM cameras in Victoria and 35 in the ACT. 3 cameras had no road within reach and keep a NULL direction; 36 have no speed limit.

## In the app

The build step shows a **Speed cameras** box:

- **Before the build**: which states get official lists ("downloaded fresh when the build starts") and which don't: *"You can't have up-to-date speed cameras for New South Wales, Queensland, ... because they don't publish their camera locations in an open format. You'll get OpenStreetMap's best guess instead: crowd-sourced, and only as current as the last mapper who drove past."*
- **During the build**: the same, once the downloads are done, including any list that failed to download (with the date of the copy used instead).
- **After the build**: the same plus the number of camera warnings on the card. It's saved with the build record.

`make_card --cameras <dir>` drives this. It emits `{"event": "cameras", "final": false|true, "states": [...]}` once before the build and once after it. `--no-camera-download` uses the lists already in the folder. The app's list of official states (`SpeedCameras.OfficialStates`) mirrors `gov_cameras.SOURCES`.

## Not done

- **Not car-tested.** The format matches the factory rows, but whether the unit needs `Poi_Relation` link points, and how it treats CameraType 0 vs 2, is unconfirmed.
- QLD and SA intersection cameras could be placed from their street names (where the two named roads cross in OSM). Their freeway and highway sites can't be pinned that way.
- Mobile camera sites (factory "check speed" category), school zones, blackspots and railway crossings stay empty.
