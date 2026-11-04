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
| OSM → native writer | Not started | |
| In-car tamper test (checksums / signatures?) | Not done | |

Every structural claim in the docs was checked against the full factory dataset (for example, all 11,282 map parcels and 192,282 feature parcels decode and frame exactly).

## Repository layout

```
docs/        format reports, in the order they were worked out
docs/reference/manifest_sha256.tsv   file list + SHA-256 of the MY12 (CD_7606) card
tools/       pure-Python (3.9+, standard library only) readers and renderers
```

| Tool | Purpose |
|---|---|
| `unzip_flat.py` | Unpack and re-pack `zip\x01` FLAT tables (re-pack is byte-identical) |
| `roads.py` | Road / address index (`.rd`) and street names (`.str`) |
| `tiles.py` | Map tiles (`mp0`, `mg1`–`mg4`): nodes, segments, shapes, attributes, names, speeds, turn bans, signposts, lanes |
| `features.py` | Background feature file (`.fea`), big-endian, 5 zoom levels |
| `render.py`, `render_fea.py` | Draw decoded roads / features to PNG, no third-party libraries |
| `fat32_list.py` | Read-only file lister + hasher for a raw SD image |

Typical use, given your own copy of a card's `navigation/db_aunz/db`:

```bash
python -I tools/unzip_flat.py navigation/db_aunz/db/auz.rd auz.rd.raw
python -I tools/render.py navigation/db_aunz/db/auz.mp0 auz.si.raw out.png 144.95 -37.86 145.06 -37.79
```

## Legal and data policy

- **No factory map data is in this repo**: no card contents, no decoded tables, no renders of factory data, no junction-view artwork. The Whereis/Navteq/Holden map data and GM/Continental software are proprietary. Work from your own card.
- Format documentation and tools are original work.
- Maps built from OpenStreetMap are © OpenStreetMap contributors and must be distributed under the **ODbL**, with attribution.

## Safety

Treat your original card as read-only. Image it, ideally with the lock switch on and a reader that honours it, and experiment only on copies written to a separate full-size SD card.
