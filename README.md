<p align="center">
  <img src="app/src/OpenLion/Assets/logo.png" alt="OpenLion logo" width="160">
</p>

<h1 align="center">OpenLion</h1>

<p align="center"><b>New maps for your Holden iQ satellite navigation, made from OpenStreetMap.</b></p>

<p align="center">
  <a href="https://github.com/hyprninja/OpenLion/releases/latest"><b>Download OpenLion for Windows or Mac</b></a>
  · <a href="#how-it-works">How it works</a>
  · <a href="#questions">Questions</a>
  · <a href="#technical-documentation">Technical documentation</a>
</p>

Holden stopped updating the iQ's maps in 2016. Whole suburbs and new roads built since then simply aren't there. OpenLion is a free app for Windows and Mac that makes a new map card for your car from today's [OpenStreetMap](https://www.openstreetmap.org) data. It copies your factory card, builds fresh maps for Australia, New Zealand or both, and writes them to a spare SD card. Your factory card isn't changed.

| Before: factory card | After: OpenLion card |
|---|---|
| ![The factory map shows an empty block next to Worland Road, with no streets](docs/images/before.jpg) | ![The OpenLion map shows Cruse Street and the new subdivision's streets along Worland Road](docs/images/after.jpg) |

Same spot, same car. The factory map stops at Worland Road. The OpenLion card shows Cruse Street and every street in the new estate.

## What you get

- **Every road in OpenStreetMap**, including streets built in the last ten years, with turn restrictions, one-way streets, roundabouts and speed limits.
- **Turn-by-turn guidance and voice instructions**, just like the factory maps. The new card uses the same map format, so the car reads it like its own.
- **Extra speed limits (optional)** from the government's official data, for roads OpenStreetMap has no limit on. OpenStreetMap's limit is kept wherever it has one.

  | Where | Source | Roads gained |
  |---|---|---|
  | Victoria | Speed zones, Department of Transport and Planning | about 132,000 km |
  | New South Wales | Speed zones, Transport for NSW | about 107,000 km |
  | Queensland | 2019–20 survey of speed signs, TMR | about 8,000 km |
  | Western Australia | Signposted limits, Main Roads WA | about 8,000 km |
  | ACT | Road network speed zones, ACT Government | about 2,300 km |
  | Tasmania | State roads only, Department of State Growth | about 1,200 km |
  | New Zealand | National Speed Limit Register, NZTA | about 55,000 km |

  South Australia and the Northern Territory don't publish their speed limits in an open format, so they're not offered.
- **Address search** by suburb, street and house number, with suburbs, councils and postcodes from OpenStreetMap.
  - **Australia:** house numbers from OpenStreetMap and, if you choose, from G-NAF, the government's national address file, which fills in most streets OpenStreetMap doesn't cover.
  - **New Zealand:** house numbers and suburbs also come from LINZ, the government's official lists, so suburbs sit under their town or city ("TE RAPA, HAMILTON") just like the factory maps.
- **Points of interest**: about 270,000 fuel stations, shops, cafés, hospitals and more.
- **Speed camera warnings** for fixed, red-light and point-to-point cameras. They come fresh from the official lists where a government publishes them openly (Victoria and the ACT) and from OpenStreetMap everywhere else. The app tells you which is which when it builds.
- **School zone and level crossing warnings**, like the factory maps. NSW school zones come from Transport for NSW and other states' from OpenStreetMap. Level crossings come from OpenStreetMap everywhere, plus any it's missing from the official NSW and WA lists.
- **A new voice (optional).** **Elodie** is a warm, relaxed Australian woman's voice, named as a nod to DankPods' EFM Toledo. She says common instructions in longer phrases ("Turn left" · "300 meters ahead"), so they sound much smoother than the factory voice's word-by-word joins. **Jimbo** is an 11/10 bogan who still gets you there but has opinions about it (contains swearing). Listen to each in the app before you choose. Both are UK English only: set the unit's language to English (UK).
- **Up-to-date maps whenever you want them.** OpenStreetMap changes every day. Run OpenLion again any time for a new card.

## Will it work in my car?

OpenLion is for the **Holden iQ navigation in the VE Commodore Series II (MY11–MY16)**, the unit that reads its maps from a full-size SD card in the dash. It works with these factory map cards:

| Card | Volume code |
|---|---|
| MY10 | CD_7172 |
| MY12 | CD_7597 |
| MY15 | CD_8217 |
| MY17 | CD_8418 |

OpenLion checks your card before it builds anything. If your card isn't supported yet, OpenLion says so, saves a report with no map data in it, and helps you send it in so support can be added.

## What you need

- A **Windows 10 or 11 PC (64-bit)** or a **Mac with Apple silicon (M1 or later) and macOS 12 or later**, with **8 GB of memory or more** and **about 6 GB of free disk space** (about 2 GB more if you add G-NAF house numbers). More memory and cores make the build faster.
- Your **factory map card**. OpenLion only reads it. [How to remove the SD card from the iQ](https://www.autoinstruct.com.au/manufacturer/holden/ve-commodore/holden-iq-navigation-upgrade/).
- A **blank SD card** of 1 to 32 GB, formatted FAT32. A 2 GB card is plenty. Use a full-size card, or a microSD card in a full-size adapter.
- An **internet connection**. The map download is about 1 GB, plus about 1.9 GB for the optional G-NAF house numbers.
- **About half an hour to 2 hours**, depending on the PC. Most of it is the map build, which runs by itself.

## How it works

1. **Download and install** [OpenLion](https://github.com/hyprninja/OpenLion/releases/latest) (`OpenLion-1.5.0.msi`). It installs for your Windows account only and doesn't need administrator rights. Windows may warn that the app is from an unknown publisher, because it isn't code-signed yet. Click **More info**, then **Run anyway**.
   **On a Mac**, open `OpenLion-1.5.0-mac.dmg` and drag OpenLion to Applications. It isn't notarized by Apple yet, so the first time you open it macOS says it can't check it: open **System Settings › Privacy & Security**, scroll down and click **Open Anyway** next to OpenLion.
2. **Read and accept the warning.** OpenLion is unofficial, and you use it at your own risk.
3. **Insert your factory card.** Set its lock switch first if you can. OpenLion copies it to your PC, checks every file, then checks that it understands your card's map format.
4. **Get the map data.** Choose Australia, New Zealand, or both (the factory card had both). OpenLion downloads the latest OpenStreetMap extract for each from Geofabrik and checks it. If you already have a `.osm.pbf` file, you can pick that instead.
5. **Speed data (optional).** Tick each state or country you want extra speed limits for. Only the sources for the countries on your card are offered. The downloads are small (0.5 to 22 MB each).
6. **House numbers (optional, Australia).** Tick the box to add house numbers from G-NAF. OpenLion downloads the latest quarterly release from data.gov.au (about 1.9 GB) and checks it. OpenStreetMap's numbers always come first.
7. **Voice (optional).** Keep the factory voice or choose Elodie or Jimbo. Press Play to hear each one. Each voice is an 11–15 MB download from this project's releases.
8. **Build the maps.** This takes about half an hour on a fast PC with plenty of memory, and up to 1½ hours on one with 8 GB. You can leave the PC to it; OpenLion keeps it awake. At the start, OpenLion downloads the latest official speed camera, school zone and level crossing lists (a few MB), and for New Zealand, LINZ's addresses and suburbs (about 30 MB, refreshed once a month).
9. **Write the new card.** Take out the factory card, insert the blank card and type its drive letter (on a Mac, its name) to confirm. OpenLion writes the maps, then reads every file back to check it.
10. **In the car:** with the car switched off, put the new card in the nav slot. Then start the car and wait for the map. If you chose a new voice, set the unit's language to **English (UK)** in its settings.

If anything isn't right, put your factory card back in. It's exactly as it was.

## Your factory card is protected

OpenLion only ever **reads** the factory card. Before it writes anything, it checks the target card several ways and refuses it if any check fails:

- Is this the factory card you copied (same volume serial)?
- Does it hold any navigation maps?
- Is the factory card still in the reader?
- Is any other card inserted?
- Is it a removable SD card of a sensible size, formatted FAT32?

It runs these checks again right before writing, in case cards were swapped. The new maps go to a temporary folder on the card and only become the card's maps once every file has been read back and checked.

### On a Mac

- **Lock the factory card before you insert it.** macOS puts hidden index folders (`.Spotlight-V100`, `.fseventsd`) on any unlocked card it mounts. They don't touch the maps, and OpenLion mounts an unlocked factory card read-only before copying it, but by then macOS may already have added them. A locked card stays exactly as it was.
- **Formatting the blank card:** in Disk Utility, choose the card, click **Erase**, and pick **MS-DOS (FAT)** with **Master Boot Record**. For small cards Disk Utility may pick FAT16 instead of FAT32; OpenLion then shows the exact Terminal command to format the card as FAT32.
- macOS keeps file information on FAT cards in hidden `._` files beside every file. OpenLion removes them from the new card, then unmounts and mounts it again so every file is read back from the card itself, not from memory.
- When the card is written, press **Eject card**, then take it out.

## Questions

**Does this replace the factory software?** No. Only the map tables change (and the voice files, if you choose a new voice). Everything else on the new card, including the software and settings, is copied unchanged from your own factory card.

**Is it legal?** OpenLion includes no Holden, GM, Navteq or Continental data. It works only from the card you already own. The new maps are built from OpenStreetMap, which is free to use under the [Open Database License](https://opendatacommons.org/licenses/odbl/), and from government open data under the licences listed in [Credits](#credits).

**How often should I make a new card?** Whenever you like. Each build uses the latest OpenStreetMap data and the latest official lists.

**Something went wrong. What now?** OpenLion saves a report (with no map data) and shows you how to [open an issue](https://github.com/hyprninja/OpenLion/issues/new/choose). Include your car's model and year and what happened.

**Can I improve the maps?** Yes. Anyone can edit OpenStreetMap at [openstreetmap.org](https://www.openstreetmap.org). Fix your street, and the next card you build will include it.

## Thanks

Thanks to **Jonny Bee**, who shared a copy of a MY17 card. That card is how OpenLion learned the newer map format, so MY17 cars can use it too.

Thanks to **Laurie N**, who shared a copy of a MY15 card, so MY15 cars can use OpenLion as well.

Thanks to **cxvabc**, who sent in the report and a copy of a MY10 (CD_7172) card, so MY10 cars can use OpenLion too.

## Credits

Map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), available under the [ODbL](https://opendatacommons.org/licenses/odbl/). The Australia and New Zealand extracts are provided by [Geofabrik](https://download.geofabrik.de).

Optional and official data, used where you choose it or where it's published:

| Data | © | Licence |
|---|---|---|
| Victorian speed zones | State of Victoria (Department of Transport and Planning) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| NSW speed zones, school zones and level crossings | Transport for NSW | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| Queensland speed limits | State of Queensland (Department of Transport and Main Roads) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| Western Australian speed limits and rail crossings | Main Roads Western Australia | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| ACT speed zones | ACT Government (Transport Canberra and City Services) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| Tasmanian speed limits | State of Tasmania (Department of State Growth) | [CC BY 3.0 AU](https://creativecommons.org/licenses/by/3.0/au/) |
| New Zealand speed limits | NZ Transport Agency Waka Kotahi (National Speed Limit Register) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| Speed camera locations | State of Victoria (Cameras Save Lives); Australian Capital Territory (Access Canberra); elsewhere OpenStreetMap contributors | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) / ODbL |
| New Zealand addresses and suburbs | Sourced from the LINZ Data Service | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| Australian house numbers (G-NAF) | Incorporates or developed using G-NAF © Geoscape Australia, licensed by the Commonwealth of Australia | [G-NAF EULA](https://data.gov.au/data/dataset/19432f89-dc3a-4ef3-b943-5326ef1dbecc) |
| Elodie and Jimbo voices | OpenLion, made with ElevenLabs | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |

OpenLion is released under the [MIT License](LICENSE).

> **Disclaimer.** OpenLion is an unofficial tool, not made by, affiliated with or endorsed by Holden, General Motors or Continental. It is provided as is, with no warranty. You use it at your own risk: the author is not responsible for damaged or erased cards, a navigation unit that stops working, wrong directions, or anything else. Always follow road signs and the road rules over the navigation.

---

# Technical documentation

Everything below is for developers and anyone curious about how the iQ's maps work.

## The iQ's map format

The iQ reads its maps from a full-size FAT32 SD card. The map data is an unencrypted Siemens VDO / Continental database ("Arriba" data model, table format 5.0.5) built from Navteq data. Most tables are zlib-compressed `zip\x01` FLAT containers. The `navigation/` folder holds:

- `db_aunz/db/`: the map database
  - `au.mod`, the schema, which also names the datamodel version
  - `auz.rd` / `auz.str`, road records and street names
  - `auz.mp0`, the detailed map tiles: a kd-tree of parcels holding nodes, segments, curve shapes, turn restrictions, signposts, lanes and cross-tile joins
  - `auz.si`, segment attributes: road class, speed limits, name links, roundabouts
  - `auz.mg1`–`mg4`, routing overview layers, all derivable from `mp0`
  - `auz.fea`, background features: water, parks, land use, rail, coastline, labels, overview roads
  - search tables (`ctr`, `stt`, `cny`, `cty`, `cl`, `ct`, `rl`, `rt`, `cal`, `cat`) and their speller trees
- `edb/POI/*.db3`: points of interest, a SQLite database whose "road sense" categories also hold speed cameras, school zones and level crossings
- `voices/`: voice prompts as WAV clips, joined using a phrase table in each language's grammar file (`.bgl`)
- the unit's software, libraries, version and configuration files, which OpenLion never changes

The four supported cards differ in their datamodel:

| Card | Map data | Datamodel | Notes |
|---|---|---|---|
| MY10 (CD_7172) | AUNZ 2009Q4 | Arriba 5.2.1, compact | varint positions, nodes and lengths; shapes and road links inside `mp0` segments |
| MY12 (CD_7597) | 2011 | Arriba 5.2.1 | the card the format was first worked out on |
| MY15 (CD_8217) | AUNZ 2014Q1 | 5.2.1 road records, 5.3 map-file layout | |
| MY17 (CD_8418) | 2016 | Arriba 5.3 | 26-byte road records, 326-byte parcel headers, u16 feature name lengths |

The datamodel is detected from `au.mod` and `auz.mp0`. For every card, every replaceable table round-trips byte-identical through OpenLion's readers and writers, and every structural claim in the reports was checked against the full factory dataset (for example, all 11,282 map parcels and 192,282 feature parcels on the MY12 card decode and frame exactly).

## How a card is built

1. **Copy and check.** The app copies the factory card and hash-checks every file. `openlion.compat` re-encodes every replaceable table and requires byte-identical output, so a card OpenLion doesn't fully understand is stopped here.
2. **Read OpenStreetMap** (`osmium`): roads and their attributes, turn restrictions, boundaries and names, areas and lines for the background, POIs, addresses, cameras, school zones and level crossings. Each read runs in its own process and is cached, with a SHA-256 sidecar, until the extract changes.
3. **Add official data**: speed limits where OSM has none, G-NAF and LINZ house numbers, LINZ suburbs, government camera, school zone and level crossing lists.
4. **Write the tables**: the road graph is tiled into `mp0` with a kd-tree, `mg1`–`mg4` are derived from it, and the road records, names, search tables, background features and POI database are written in the card's own datamodel. Australia and New Zealand each get their own search catalog.
5. **Assemble** a complete `navigation/` folder: only map tables (and voice files, with a voice pack) are replaced, and every other file is copied and hash-verified against the factory copy.
6. **Check** the result (`tests/card_check.py`): every parcel frames, the kd-tree reaches each parcel once, every name link lands on a road record, every feature parses, POIs keep the factory structure, and sample address lookups in each capital succeed.
7. **Write** it to the blank card (app only), reading every file back before the folder is put in place.

The build picks a memory plan from the PC's free RAM: about 3.6 GB peak on an 8 GB PC, or in parallel on a big one (Australia and New Zealand in about 25 minutes on 44 threads). The maps are identical either way.

## Format reports

The reports in [`docs/`](docs) describe the format and each part of the build in detail.

| Topic | Reports |
|---|---|
| Card layout, file inventory, FLAT container | [01](docs/01_inventory_report.md) |
| Schema, road records and street names | [02](docs/02_road_table_format.md) |
| Map tiles, curve shapes, segment attributes, speed limits | [03](docs/03_map_tiles_format.md), [04](docs/04_shapes_attributes.md) |
| Turn restrictions, long nodes, cross-tile joins, one-way streets | [05](docs/05_turns_nodes_joins.md) |
| Signposts, exit numbers, lanes, junction views | [06](docs/06_signposts_lanes.md) |
| Background features (`.fea`) | [07](docs/07_features_fea.md) |
| Routing layers `mg1`–`mg4` | [08](docs/08_overview_layers.md) |
| Table writers and identity builds | [09](docs/09_writer.md) |
| Address search tables and speller trees | [10](docs/10_search_tables.md) |
| Tiling and the kd-tree | [11](docs/11_tiling.md) |
| OSM roads, attributes, turn restrictions, roundabouts | [12](docs/12_osm_import.md) |
| OSM names and address search | [13](docs/13_osm_names_search.md) |
| OSM background layer | [14](docs/14_osm_background.md) |
| Full-country builds | [15](docs/15_full_australia.md) |
| Card assembly and the in-car test | [16](docs/16_test_card.md) |
| Points of interest | [17](docs/17_poi_canberra.md) |
| Australia, New Zealand or both | [18](docs/18_countries.md) |
| MY17 and MY10 cards (MY15: see [`schema.py`](openlion/schema.py)) | [19](docs/19_my17_card.md), [21](docs/21_my10_card.md) |
| Memory and parallel builds | [20](docs/20_memory.md) |
| Government speed limits | [22](docs/22_gov_speed_zones.md) |
| Speed cameras | [23](docs/23_speed_cameras.md) |
| House numbers: format, OSM, G-NAF | [24](docs/24_house_numbers.md), [25](docs/25_gnaf_house_numbers.md) |
| Voice packs | [26](docs/26_voice_packs.md) |
| School zones, level crossings, LINZ addresses and suburbs | [27](docs/27_alerts_linz.md) |
| The app's state machines: every step's states and moves, as logged in `app.log` | [28](docs/28_app_states.md) |

Not done yet: making the `.fea` smaller, and the other road-sense alerts (blackspots, "check speed").

## Repository layout

```
app/          the app (.NET 10): the wizard, card copy and write, downloads, engine runner; a WPF window
              for Windows and an Avalonia window for macOS, sharing everything else
openlion/     the engine: pure-Python (3.9+) readers, writers and the OSM build (osmium for OSM reads)
tests/        engine unit tests, plus round-trip, re-tiling, region and card checks against your own card copy
docs/         format reports; docs/reference/manifest_sha256.tsv is the file list + SHA-256 of the reference MY12 card
packaging/    release builds: embedded Python + engine + app → MSI (WiX 6) and macOS .app/.dmg; pinned engine wheels
tools/voice/  making voice packs with ElevenLabs (see tools/voice/README.md)
.github/      CI and the card report issue form
```

## The Windows app

The app (`app/src/OpenLion`, with the logic in `app/src/OpenLion.Core`) is a wizard: welcome and warning, factory card, copy, map data, speed data, house numbers, voice, build, write.

- **Drives and safety.** `DriveService` watches for cards. `CardGuard` runs the factory-card checks above, again on a fresh drive list right before writing. `CardWriter` writes to `navigation.partial`, reads every file back and hash-checks it, then renames the folder and gives the card the factory card's volume label.
- **Card copy.** `CardCopier` copies and hash-verifies the factory card, including its other folders (such as `config/`). `KnownCards` compares it with known file lists, and the engine's compatibility check (`CompatCheck`) decides whether it can be built. If not, `ErrorReport` saves a zip with no map data and the user's paths hidden, and points to the [card report form](.github/ISSUE_TEMPLATE/card_report.yml).
- **Downloads.** `OsmDownloader` (Geofabrik, resumable, MD5-checked), `SpeedZones`, `Gnaf` and `VoicePacks` each only follow redirects to their own allowed hosts, cap the download size and treat a short or cut-off download as incomplete. Speed cameras, school zones, level crossings and LINZ data are downloaded by the engine during the build, keeping the last good copy if a download fails.
- **Build.** `EngineRunner` / `MapBuild` run `openlion.make_card --json` on the bundled Python and show progress per stage. `KeepAwake` stops the PC sleeping.

The installed app is self-contained: the .NET runtime, an embedded Python 3.12 with `osmium` (wheels pinned by SHA-256), and the engine (`openlion/`, the `tests/` checks and the reference manifest) all sit in its folder. Nothing else needs to be installed.

### The Mac app

The Mac app (`app/src/OpenLion.Mac`, solution `app/OpenLion.Mac.slnx`) is an [Avalonia](https://avaloniaui.net) window over the same view models (`app/src/OpenLion/ViewModels`) and the same `OpenLion.Core`. What the two apps need from their window toolkit (file pickers, sounds, timers, "show in Finder/Explorer") is in each app's `Platform.cs`. `OpenLion.Core` builds twice: `net10.0-windows` for Windows, and `net10.0` for macOS, where `Core/Mac/` stands in for the Windows-only files:

- `DriveService` reads volumes with `diskutil`. A card is removable media or in an SD slot, never a disk image; a FAT volume's UUID (made by macOS from its FAT serial number) stands in for the volume serial, so `CardGuard` applies the same rules.
- `CardWriter` removes macOS's `._` attribute files from the new card, then unmounts and mounts it again before the read-back, so every file is read from the card (Windows reads unbuffered instead). It names the card with `diskutil rename`.
- `KeepAwake` runs `caffeinate`. The bundled Python and engine are in `OpenLion.app/Contents/Resources`. Working files are in `~/Library/Application Support/OpenLion`.

### Building and testing

To build the MSI (needs the .NET 10 SDK, WiX 6 and any Python with pip):

```powershell
dotnet tool install -g wix --version 6.0.2
wix extension add -g WixToolset.UI.wixext/6.0.2
pwsh packaging/build-release.ps1 -Version 1.5.0     # → dist/OpenLion-1.5.0.msi (+ .sha256)
```

To build the Mac app (on an Apple silicon Mac with the .NET 10 SDK and the Xcode command line tools):

```bash
packaging/build-mac.sh 1.5.0     # → dist/OpenLion-1.5.0-mac.dmg (+ .sha256) and dist/mac/OpenLion.app
```

On a Mac, the same tests run with `dotnet build app/OpenLion.Mac.slnx -c Release -warnaserror` and `packaging/engine-requirements-mac.txt`.

Tests (CI runs all of these on every push and pull request, on Windows and on macOS):

```powershell
dotnet build app/OpenLion.slnx -c Release -warnaserror
dotnet test app/tests/OpenLion.Core.Tests -c Release --no-build
python -m pip install --only-binary=:all: --require-hashes -r packaging/engine-requirements.txt
python -m unittest discover -s tests -p "test_*.py"
```

Settings for development:

| Variable | Effect |
|---|---|
| `OPENLION_HOME` | Keep the app's working files here instead of `%LOCALAPPDATA%\OpenLion` (macOS: `~/Library/Application Support/OpenLion`) |
| `OPENLION_PYTHON`, `OPENLION_ENGINE` | Python with `osmium` installed, and the folder holding `openlion/` (used when there's no bundled engine next to the app) |
| `OPENLION_BBOX` | Build only this area, e.g. `148.76 -35.92 149.40 -35.12` (Canberra), for quick test builds; the app marks the result as a test build |
| `OPENLION_FACTORY_NAV` | A factory `navigation/` copy, so the app tests can run the full compatibility check |

## The engine

| Module | Purpose |
|---|---|
| `flat.py` | Unpack and re-pack `zip\x01` FLAT tables |
| `common.py` | Shared 0x5E table header |
| `schema.py` | Datamodel versions (MY10 compact, 5.2.1, MY15, 5.3), detected from `au.mod` and `auz.mp0` |
| `roads.py` | Road records (`.rd`) and street names (`.str`): read and write |
| `mapfile.py` | Map tiles (`mp0`, `mg1`–`mg4`) reader: nodes, segments, shapes, attributes, names, speeds, turn bans, signposts, lanes |
| `mapwrite.py` | Map tile writer: parcel container, list codecs, file assembly |
| `features.py` / `feawrite.py` | Background feature file (`.fea`) reader / writer |
| `search.py` | Address search tables and speller trees: read, write, build, validate |
| `graph.py` | Layout-independent road graph, and an extractor from any map file |
| `tiler.py` | Road graph → map file: kd-tree, parcels, border copies, all lists |
| `layers.py` | Derive routing layers `mg1`–`mg4` from the `mp0` graph |
| `countries.py` | Australia and New Zealand: catalogs, states and regions, how each is mapped |
| `osm_import.py` | OSM roads → road graph and segment attributes (speed limits, roundabouts, turn restrictions) |
| `osm_names.py` | OSM boundaries and names → road records, cities and all search tables |
| `osm_fea.py` | OSM areas, lines and coastline → background feature file |
| `osm_poi.py` | OSM amenities → POI database, keeping the factory schema and statements; categories matched by name |
| `osm_addr.py` | OSM addresses → house-number ranges on street segments |
| `gov_speed.py` | Government speed zones (lines and polygons) → speed limits where OSM has none |
| `gov_signs.py` | Speed signs (Queensland) → limits walked along the road to the next sign |
| `gov_addr.py` | G-NAF house numbers, OSM first |
| `gov_nz.py` | LINZ addresses and suburbs for New Zealand |
| `gov_cameras.py` / `cameras.py` | Government camera lists, and cameras snapped onto the carriageway with direction and speed limit |
| `alerts.py` | School zones and level crossings as road-sense POIs |
| `voice_grammar.py` / `voicepack.py` | The grammar file's phrase table, and checking and installing voice packs |
| `pipeline.py` | OSM extract → every replaceable table (cached stages, checks, sample lookups) |
| `resources.py` / `memory.py` | Memory plan from free RAM and CPU count; memory measurement |
| `make_card.py` | What the app runs: pipeline → `build` → `tests/card_check.py`, with JSON progress (`--json`) |
| `build.py` | Writes a complete `navigation/` folder (`--identity` or `--tables <dir>`), replacing only map tables and hash-verifying everything else |
| `compat.py` | Card compatibility check: re-encodes every replaceable table and requires byte-identical output (`--json` for the app); reports hold no map data |
| `render.py`, `render_fea.py` | Draw decoded roads and features to PNG, no third-party libraries |
| `fat32_list.py` | Read-only file lister and hasher for a raw SD image |

From the repo root, given your own copy of a card's `navigation/` folder:

```bash
# Inspect and check a factory card
python -m openlion.compat navigation
python tests/roundtrip.py navigation/db_aunz/db
python tests/retile.py navigation/db_aunz/db
python -m openlion.flat navigation/db_aunz/db/auz.si auz.si.raw
python -m openlion.render navigation/db_aunz/db/auz.mp0 auz.si.raw out.png 144.95 -37.86 145.06 -37.79
python -m openlion.build navigation out/navigation --identity

# Build a card (what the app runs)
python -m openlion.make_card australia-latest.osm.pbf navigation work card/navigation
python -m openlion.make_card AU=australia-latest.osm.pbf navigation work card/navigation --extract NZ=new-zealand-latest.osm.pbf
python -m openlion.make_card australia-latest.osm.pbf navigation work card/navigation \
    --speed-zones vic.bin --speed-zones nsw.bin \
    --addresses g-naf_aug26_allstates_gda2020_psv_110.zip \
    --cameras cameras --gov-data govdata \
    --voice-pack openlion-voice-elodie-v1.zip
python -m openlion.make_card australia-latest.osm.pbf navigation work test/navigation --bbox 148.76 -35.92 149.40 -35.12

# The same in steps
python -m openlion.pipeline australia-latest.osm.pbf navigation/db_aunz/db work
python -m openlion.build navigation card/navigation --tables work/db --poi work/edb/POI/aunz_sql.db3
python tests/card_check.py card/navigation navigation

# Voice packs
python -m openlion.voicepack check openlion-voice-elodie-v1.zip navigation
```

Other `make_card` options: `--memory auto|low|high`, `--no-camera-download` and `--no-gov-download` (use the lists already downloaded), `--manifest` (the factory copy's file list).

## Legal and data policy

- **No factory map data is in this repo**: no card contents, no decoded tables, no renders made from factory data, no junction-view artwork. (The "before" picture is a photo of the car's own screen.) The Whereis/Navteq/Holden map data and GM/Continental software are proprietary. Work from your own card.
- Format documentation and tools are original work.
- Maps built from OpenStreetMap are © OpenStreetMap contributors and must be distributed under the **ODbL**, with attribution.
- Government data is used under the licences in [Credits](#credits). OpenLion modifies it: speed limits are matched onto OSM roads, and camera, school zone and level crossing points are snapped onto them.
- Cards with New Zealand on them contain LINZ addresses and suburbs, sourced from the LINZ Data Service (Toitū Te Whenua Land Information New Zealand) and licensed for reuse under CC BY 4.0. OpenLion modifies them: numbers are matched to OSM streets and stored as ranges, and suburb boundaries are simplified to about 10 m (report [27](docs/27_alerts_linz.md)).
- Cards built with G-NAF house numbers (the app's House numbers step, or `--addresses`) contain G-NAF data. Incorporates or developed using G-NAF © Geoscape Australia licensed by the Commonwealth of Australia under the [Open Geo-coded National Address File (G-NAF) End User Licence Agreement](https://data.gov.au/data/dataset/19432f89-dc3a-4ef3-b943-5326ef1dbecc) (CC BY 4.0, plus a rule against using it to compile addresses for sending mail). OpenLion modifies it: house numbers are matched to OSM streets and stored as ranges (report [25](docs/25_gnaf_house_numbers.md)).
- **Voice packs** contain only OpenLion's own audio, never factory clips. The factory beeps, pauses and other languages stay as they are on your card, and the longer phrases are added to your own card's grammar file when the card is built. The Elodie and Jimbo voices were made with ElevenLabs (on a paid plan, which allows this use) and are licensed **CC BY 4.0**: credit "Elodie voice" or "Jimbo voice, OpenLion, made with ElevenLabs".

## Safety

Treat your original card as read-only. Image it, ideally with the lock switch on and a reader that honours it, and experiment only on copies written to a separate full-size SD card.
