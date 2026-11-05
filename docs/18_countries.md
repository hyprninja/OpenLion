# Phase 9: Choose your country (Australia, New Zealand or both)

Date: 2026-11-05. Modules: `openlion/countries.py` (new), `openlion/osm_names.py` (places, catalogs), `openlion/osm_import.py`, `openlion/osm_poi.py`, `openlion/pipeline.py`, `openlion/make_card.py`; the app's Get OSM data step.

## What the card already supports

The factory card is "Australia + New Zealand" (`cdrom.txt`), and the map format keeps the two apart:

| Table | Per country |
|---|---|
| `auz.ctr` | one row per country: name, ISO codes, speed unit, driving side (AU and NZ = 1, left; the Gulf states in `db_ag` = 0, right) |
| `auz.stt` | states carry a `countryID` (offset 98): 0–8 Australia, 9–24 the 16 NZ regions |
| `auz.cny` | NZ district / city councils use time zone 9 |
| `au.cal`, `au.cat` | one search catalog per country, each with its own city tree offset and bounding box |
| `auz.ct`, `auz.cl` | the city list is Australia's entries (22,991), then New Zealand's (2,253), each sorted, each with its own tree |
| `auz.cty`, `auz.rd` | `cataloglistIdFirst` / `countryID` = 0 or 1 (factory: 548,381 AU roads, 76,375 NZ) |

The factory extent (`AU_BBOX`) and the background map grid already cover New Zealand, so New Zealand needs no new extent. OpenLion 1.0 built Australia only and left the NZ catalog empty: New Zealand had no streets on a 1.0 card.

## OSM differences

Australia has suburb and locality boundaries (`admin_level=9`) and postcode areas. New Zealand has regions (`admin_level=4`, 17 including the Chatham Islands) and district / city councils (6, 67), but almost no suburb boundaries: suburbs, towns and localities are `place=*` points (2,902 localities, 1,461 hamlets, 673 suburbs, 451 quarters, 431 villages, 186 towns, 20 cities in the 2026-11-03 extract; about 200 suburbs are areas, mostly in Christchurch).

So for New Zealand (`countries.NZ`, `localities="places"`):

- **Cities** are place areas where they exist, otherwise the nearest place point (`osm_names.read_places`, `PlaceIndex`). Distance is divided by a weight (city 3, town 2, suburb / village 1.3, others 1) so a town's streets reach further than a hamlet's. Neighbourhoods, farms, islands and isolated dwellings aren't localities.
- **Suburbs under towns**: a `suburb` or `quarter` is listed under the nearest city or town of the same council within 40 km, like the factory: "TE RAPA, HAMILTON", "HAMILTON EAST, HAMILTON". The town's own city record becomes the municipality, and its street list covers every suburb's streets. This is the Canberra model from report 17, generalised.
- **States** are mapped onto the factory list by name; `MANAWATU-WHANGANUI` is aliased to the factory's `MANAWATU WANGANUI`. NZ has no postcode areas in OSM, so cities have no postcode variants.
- **POIs** get NEW ZEALAND as the country and `+(64)-9-3790000` phone numbers, the factory format.
- **Speed limits**: still OSM `maxspeed` only. Values like `40kmh` are now read (156 NZ ways).

Each road segment carries its extract's country (`g.seg_country`, from the private tag `openlion:country`), so a road record, its city and its catalog always agree.

## Pipeline

`python -m openlion.pipeline AU=australia.osm.pbf <db> <out> --extract NZ=new-zealand.osm.pbf` (a bare path is Australia, as in 1.0). Every OSM read is done per extract and cached as `<name>_<CODE>.pickle`; the results are merged (ways in both extracts kept once). Reads are now clipped to the map extent, which drops the Chatham Islands (west of 180°) and Heard Island. A country left out gets a root-only city tree, so either catalog can be empty.

`make_card` takes the same `--extract` options; its cache key covers every extract, and its stage times are scaled by the total extract size.

`card_check` runs CBD lookups for each catalog that has cities: Australia's nine and New Zealand's six (Auckland, Wellington, Christchurch, Hamilton, Dunedin, "PONSONBY, AUCKLAND").

## Results

RESULTS

## App

The Get OSM data step asks which maps go on the card: Australia (~1 GB), New Zealand (~400 MB) or both. Each country has its own row, download and "Use a file I already have…". Australia's extract stays in `%LOCALAPPDATA%\OpenLion\osm` (where 1.0 kept it), New Zealand's goes to `osm\nz`. The choice is saved in `state.json` (`Countries`). A build is keyed on the extracts' file names joined with `+`, so an existing 1.0 Australia build still counts as up to date.

## Not done / to test in the car

- A New Zealand card hasn't been in the car yet. Things to check: address entry offers New Zealand, "SUBURB, TOWN" search works, routing and guidance in NZ (demo mode, since the car is in Australia).
- A one-country card leaves the other catalog empty. The 1.0 Australia-only card worked, but selecting New Zealand in address entry on such a card hasn't been tried.
- On a New Zealand-only card the background map shows Australia as sea (the background grid covers the whole factory extent, and nothing marks Australia as land).
- Factory traffic (TMC) data is kept as on 1.0.
