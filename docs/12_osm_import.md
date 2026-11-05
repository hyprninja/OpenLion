# Phase 6c: OpenStreetMap import (roads)

Date: 2026-11-04. Code: `openlion/osm_import.py`. Test: `tests/osm_region.py`. Needs `pip install osmium` and an OSM extract (tested with Geofabrik `australia-latest.osm.pbf`, 973 MB, 2026-11-04). OSM data © OpenStreetMap contributors, ODbL.

## What it does

```
.osm.pbf --read()--> routable ways + turn-restriction relations
         --build_graph()--> openlion Graph + new seginfo table
         --layers.derive()--> mg1..mg4 graphs
         --tiler.tile()--> auz.mp0, auz.mg1..mg4
```

1. **Read:** routable `highway=*` ways (excluding parking aisles, driveways, drive-throughs, areas, and private/no access unless motor vehicles are allowed), with node locations. Locations are cached in C++, and osmium filters stop untagged nodes reaching Python. A bounding box keeps ways with any node inside. Reading all of Australia takes about 3.5 minutes.
2. **Split at junctions:** a node shared by two ways, or a way end, becomes a graph node. Points in between become shape points. Pieces are also cut every 500 shape points, and closed pieces are split in the middle (the format has no self-loops).
3. **Orient:** each segment runs from its smaller-(lon, lat) end. The one-way direction (`oneway`, `-1`, implied by motorway and roundabouts) is converted to `drivable_lower`/`drivable_upper` relative to that orientation.
4. **Attributes:** `seginfo` records are built from tags and de-duplicated into a fresh `.si` table (73 records for inner Melbourne):

   | OSM | rank | class |
   |---|---|---|
   | motorway | 4 | 0 freeway |
   | trunk | 3 | 1 |
   | primary | 2 | 1 |
   | secondary | 2 | 2 |
   | tertiary | 1 | 2 |
   | residential, unclassified, living_street, service, road | 0 | 3 |
   | track | 0 | 7 |
   | busway | 0 | 8 |
   | *_link | one below the road it serves | 6 ramp |

   Ranks are calibrated against the factory data over inner Melbourne. Factory km per rank 4/3/2/1 = 19/49/196/68; OSM motorway/trunk/primary+secondary/tertiary = 35/41/210/145. Also: `restclass = 3` for unsealed surfaces and tracks; `toll_road` from `toll=yes`; `urban`/`detailedcity` for residential/service roads and roads ≤ 60 km/h; `dbldig` for one-way motorway/trunk/primary carriageways.
5. **Length** in feet along the polyline (haversine).
6. **Arms** at each node: clockwise by the bearing of the first step away, starting nearest north.
7. **Masks** (`restr_left/right`): at every node with ≥ 2 arms, forbid the U-turn and any exit onto a one-way against its direction. Same pattern as the factory data.
8. **Speed limits** from `maxspeed` (km/h or mph) into the `adas` list.
9. **Turn restrictions:** `type=restriction` relations with a via node. `no_*` bans the to-way's arm; `only_*` bans every other arm except the U-turn back. Duplicates are merged.

## Results: inner Melbourne (144.95–145.06°E, 37.86–37.79°S)

With calibrated ranks and driveways excluded:

| | |
|---|---|
| OSM ways kept | 16,661 |
| Graph | 20,818 nodes, 27,152 segments, 71 seginfo records |
| Speed limits | 15,228 segments (**56%**; factory data has 38% of segments overall) |
| Turn restrictions | 585 (33 relations skipped as ambiguous) |
| mp0 written | 479 KB; read back identical (segments, nodes, turns) |
| mg1 / mg2 / mg3 / mg4 | 13,838 / 8,882 / 1,891 / 183 segments, each read back identical |

The render (`openlion.render` on the new mp0 + .si) closely matches the factory render of the same area: freeways rank 4, Hoddle St / Punt Rd / Eastern Fwy rank 3, the arterial grid rank 2. Before calibration (trunk = 4, primary = 3), too many roads came out as high rank.

## Not yet done

- **Street names** and the address search (`.rd`, `.str`, cities, speller trees) for OSM data: needs suburb polygons and the city model (report 10).
- Signposts (`destination`, `junction:ref`), lanes (`lanes`, `turn:lanes`), POIs, `.fea` background (water, parks, rail).
- ~~Speed defaults where OSM has no `maxspeed`~~: decided 2026-11-05 not to add any. Limits come only from OSM tags.
- Long ways crossing many parcels are fine. Nodes with more than 8 arms are counted but not split yet.
- A full-Australia run (done in report 15).
