# OpenLion

Open tools and format documentation for the **Holden iQ factory satellite navigation** (VE Commodore Series II, MY11–MY16), with the goal of building **up-to-date map cards from OpenStreetMap**.

Holden stopped issuing map updates in 2016. The iQ reads its maps from a full-size SD card, and the map data turns out to be an unencrypted Siemens VDO / Continental database ("Arriba" data model, format 5.0.5) built from Navteq data. This repo documents that format and provides readers for it. The aim is an OSM → native converter that writes a new `navigation/` map set while leaving the card's metadata, version files and libraries unchanged.

## Status

| Part | State | Doc |
|---|---|---|
| Card layout, FAT32, file inventory, zlib block container | Decoded | [01](docs/01_inventory_report.md) |
| Schema file `au.mod`, road/address index `.rd`, strings `.str` | Decoded, round-trip byte-identical | [02](docs/02_road_table_format.md) |
| Map tiles `mp0`: kd-tree, parcels, nodes, segments, lengths | Decoded | [03](docs/03_map_tiles_format.md) |
| Curve shapes, segment attributes `.si`, name links, speed limits | Decoded | [04](docs/04_shapes_attributes.md) |
| Turn restrictions, long nodes, cross-tile joins, one-way direction | Decoded | [05](docs/05_turns_nodes_joins.md) |
| Signposts, exit numbers, lanes, junction views | Decoded | [06](docs/06_signposts_lanes.md) |
| Background features `.fea` (water, parks, landuse, rail, overview roads) | Decoded | [07](docs/07_features_fea.md) |
| Routing layers `mg1`–`mg4` | Fully derivable from `mp0` | [08](docs/08_overview_layers.md) |
| Writers for every replaceable map table; identity card build is byte-identical (2,225/2,225 files) | Done | [09](docs/09_writer.md) |
| Address search: countries, states, counties, cities, speller trees, city/road lists | Decoded, round-trip byte-identical | [10](docs/10_search_tables.md) |
| Tiling + kd-tree build; mg1–mg4 derived from mp0 (re-tile test: all layers identical) | Done | [11](docs/11_tiling.md) |
| OSM import: roads, attributes, speed limits, turn restrictions → mp0 + mg1–mg4 (tested on inner Melbourne) | Working | [12](docs/12_osm_import.md) |
| OSM street names + address search: suburbs, LGAs, postcodes, road records, speller trees (tested on inner Melbourne) | Working | [13](docs/13_osm_names_search.md) |
| OSM background layer (`.fea`): water, parks, landuse, rail, coast/ocean, labels, overview roads (tested on Melbourne) | Working | [14](docs/14_osm_background.md) |
| Full-Australia build: all 17 tables in one run (`python -m openlion.pipeline`), lookups work in every capital | Done | [15](docs/15_full_australia.md) |
| Test card assembly: complete `navigation/` folder with OSM maps, every other file byte-identical; read-back check passes | Done | [16](docs/16_test_card.md) |
| POIs, `.fea` size reduction, NZ, Canberra as a city name | Not started | |
| In-car test: unit accepts the OSM card, shows post-2010 streets, address search works, routes 200 km in demo mode | Passed (2026-11-05) | [16](docs/16_test_card.md) |

Every structural claim in the docs was checked against the full factory dataset (for example, all 11,282 map parcels and 192,282 feature parcels decode and frame exactly).

## Repository layout

```
docs/        format reports, in the order they were worked out
docs/reference/manifest_sha256.tsv   file list + SHA-256 of the MY12 (CD_7606) card
openlion/    pure-Python (3.9+, standard library only) readers, writers and renderers
tests/       round-trip, re-tiling, OSM region and card checks against your own card copy
app/         Windows app (.NET 10, WPF): wizard that copies a factory card, builds maps and writes a blank card
```

The Windows app is in progress: the wizard, drive detection and `CardGuard` (the checks that refuse to write to a factory card) are done; copy, OSM download, build and card writing come next. Build and test with `dotnet test app/OpenLion.slnx`.

| Module | Purpose |
|---|---|
| `flat.py` | Unpack and re-pack `zip\x01` FLAT tables |
| `common.py` | Shared 0x5E table header |
| `roads.py` | Road / address index (`.rd`) and street names (`.str`): read + write |
| `mapfile.py` | Map tiles (`mp0`, `mg1`–`mg4`) reader: nodes, segments, shapes, attributes, names, speeds, turn bans, signposts, lanes |
| `mapwrite.py` | Map tile writer: parcel container, list codecs, file assembly |
| `features.py` / `feawrite.py` | Background feature file (`.fea`) reader / writer |
| `search.py` | Address-search tables and speller trees: read, write, build, validate |
| `graph.py` | Layout-independent road graph + extractor from any map file |
| `tiler.py` | Graph → map file: kd-tree, parcels, border copies, all lists |
| `layers.py` | Derive routing layers mg1–mg4 from the mp0 graph |
| `osm_import.py` | OpenStreetMap (.pbf, via `osmium`) → road graph + seginfo table |
| `osm_names.py` | OSM boundaries + names → road records, cities and all search tables |
| `osm_fea.py` | OSM areas/lines/coastline → background feature file |
| `pipeline.py` | One command: OSM extract → every replaceable table (cached stages, checks, sample lookups) |
| `build.py` | Writes a complete `navigation/` folder (`--identity` or `--tables <dir>`), replacing only map tables and hash-verifying everything else |
| `render.py`, `render_fea.py` | Draw decoded roads / features to PNG, no third-party libraries |
| `fat32_list.py` | Read-only file lister + hasher for a raw SD image |

Typical use, from the repo root, given your own copy of a card's `navigation/` folder:

```bash
python -m openlion.flat navigation/db_aunz/db/auz.si auz.si.raw
python -m openlion.render navigation/db_aunz/db/auz.mp0 auz.si.raw out.png 144.95 -37.86 145.06 -37.79
python tests/roundtrip.py navigation/db_aunz/db
python -m openlion.build navigation out/navigation --identity
python tests/retile.py navigation/db_aunz/db
python -m openlion.pipeline australia-latest.osm.pbf navigation/db_aunz/db osm_out
python -m openlion.build navigation card/navigation --tables osm_out/db
python tests/card_check.py card/navigation
```

## Legal and data policy

- **No factory map data is in this repo**: no card contents, no decoded tables, no renders of factory data, no junction-view artwork. The Whereis/Navteq/Holden map data and GM/Continental software are proprietary. Work from your own card.
- Format documentation and tools are original work.
- Maps built from OpenStreetMap are © OpenStreetMap contributors and must be distributed under the **ODbL**, with attribution.

## Safety

Treat your original card as read-only. Image it, ideally with the lock switch on and a reader that honours it, and experiment only on copies written to a separate full-size SD card.
