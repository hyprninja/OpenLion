# Phase 14: the licensing layer, and a verified end-to-end rebuild

Date: 2026-11-16. Modules: `openlion/carin.py`, `carin_iso.py`, `carin_build.py`, `osm_import.py`, `osm_names.py`. Source: the same `NAV_DB.iso` image used for [29](29_vz_map_disc.md), plus a Geofabrik extract of Victoria.

This report answers the two questions [29](29_vz_map_disc.md) left open about the copy protection, and records the first rebuild produced from a fresh OSM extract outside the project's own tooling.

## Is the map data protected?

Short answer: no. Three findings, all checked directly on the disc image:

1. **No encryption.** All 8,239 zlib units re-encode byte for byte (`python -m openlion.carin roundtrip NAV_DB.iso`: "re-encoded 8239 zlib units: identical"). Street, place and POI text is plain lowercase ASCII.
2. **No checksum over the map data.** The root unit (table 0x07) carries one unexplained 32-bit value, `0x9A955A96`, at offset 0x130, followed by the licensing-root address (`0x05AF8001`: offset `0x5AF800`, one page). Candidates tested against it: CRC-32 and Adler-32 over `carindb` whole, per-table and concatenated; the licensing chain (`0x5AF8000`–`0x5AF9400`); the root unit itself; `CARINET`; every `TPD/` file; the first 743,424 bytes and the whole image; Fletcher-32 and a 32-bit byte sum over `carindb`; CRC-32 over the unit headers alone. None match. The value behaves like an identifier or version stamp, not a digest — and `carin_build` copies it verbatim regardless.
3. **The licensing tables are plain text.** Tables 0x1F–0x28 decode without keys: "No supplier activated" in six languages (0x20), "Product activated" (0x21), the Australian state list (0x22), "No TI Service activated" (0x23), per-state rows (0x24), "unknown product … australia … whereis" (0x28). They are status strings and product metadata, not a manifest of map contents.

The `DAYTON` file is 42 bytes: `00 2a 2117 bbee 0a6b d30a 4446 8832 0ee6 509d 638e 6c00 9599 7a47 3771 abc8 7f18 2d49 cbb0 f1b5 0e39 be71` — a leading zero and a 0x2A length byte, then 40 bytes of encrypted data. It and the encrypted `TPD/acCIQ_au.04.I_20030926` files are the C-IQ activation surface, between the head unit and the disc's CD-ID (11175). Nothing in them is known to cover map bytes, and nothing outside them could cheaply do so: a hash of the map tables would have to live somewhere in `carindb`, and every table there is accounted for except the value above.

So the rebuild posture in [29](29_vz_map_disc.md) — replace map tables, keep the licensing block byte for byte — is the right one, and the residual risk is the same one disc 2 of the car test plan answers: whether the PC5400 itself handshakes a disc digest. If it does, it does it over the whole medium and no in-place patching would pass either; the label test (disc 2) distinguishes this from a format mistake in the rebuild.

## First independent rebuild

A rebuild was produced on the machine that holds the source image, using the project's own modules end to end — no pre-built caches:

| Step | Command / module | Result |
|---|---|---|
| OSM extract | Geofabrik `australia-oceania/australia/victoria-latest.osm.pbf` | 233 MB |
| Roads cache | `osm_import.read(pbf)` → `roads2_AU.pickle` | 493,440 ways, 33 s, 118 MB |
| Boundaries cache | `osm_names.read_boundaries(pbf, None, AU.boundary_levels, AU.catalog)` | 13 MB, 5 s |
| Rebuild | `python -m openlion.carin_build NAV_DB.iso OUT.iso --roads … --boundaries … --bbox 144.6 -38.1 145.7 -37.5` | 22 s total |

The bbox is Melbourne metro, chosen so the artefact stays small enough to share. The build log:

```
network: 317,450 roads, 240,262 nodes
level 1: 996 roads, 13 tiles      level 2: 4,902 roads, 43 tiles
level 3: 20,330 roads, 144 tiles  level 6: 317,450 roads, 2,860 tiles
tiles: 3,060, largest 33,495 bytes
laying out 3,776 units; fixed block from 0x5168200
carindb: 95,392,768 bytes (95,392,768 before)
```

The new `carindb` lands on the same size by coincidence of layout (the freed map-table space absorbs the new units and padding); `carin_iso.replace_db` handles the general case.

## Verification of the built disc

| Check | Result |
|---|---|
| `pytest tests/test_carin.py tests/test_carin_build.py` | 16 passed |
| `carin roundtrip` on the new image | all 3,812 zlib units re-encode identically |
| `carin info` on the new image | table layout as designed: 2,860 detail tiles, 662 street-index units, search tables rebuilt |
| Licensing tables 0x1F–0x28 | byte-identical to the factory |
| Background block (`0x5168200`–`0x5AF8000`) | byte-identical to the factory |
| Changed sectors | 41,679, all inside the `carindb` span (LBA 363–46,941); every other file untouched |
| Spot check | "lygon street" present in the rebuilt 0x0E street index |

One note for future runs: `carin_build` needs a Python with `pyosmium` (`pip install osmium`); the system Python 3.9 on this Mac was fine with osmium 4.x from PyPI.

## Disc 3 rejected: isolating why

The first full-AU rebuild was burned and the unit answered **"Wrong or defected media"** — i.e. it refused the medium before drawing anything. The label test (disc 2) still works, so changed map bytes are fine. That leaves three things the full-AU disc changed at once:

1. **map content** — rebuilt roads, search, no POI layer, no house numbers;
2. **`carindb` size** — 95,392,768 → 178,578,432 bytes (+83,185,664);
3. **licence geometry** — `carindb` grew, so `TPD/` moved from LBA 46,942 to 87,560 and the PVD volume size changed (46,960 → 87,578 sectors).

What was ruled out offline on the rejected image:

| Check | Result on disc 3 |
|---|---|
| ISO 9660 structure | valid: two identical primary descriptors, volume space = 87,578, both path tables and every directory record updated for the moved `TPD` |
| Licensing bytes (0x1F–0x28) | byte-identical to the factory, at their original offsets |
| `DAYTON`, `TPD/…` | byte-identical; `TPD3.DIC` is plain text (`TPD-UPI acCIQ_au 13 /acCIQ_au.04.I_20030926`) |
| Every tile re-parsed and re-serialised | 35,458 tiles, 0 mismatches |
| Every road layer's grid → cell → tile chain | 0 bad addresses |
| Any unit's intra-unit u16 offsets > 64 KiB | none |

So the medium is well-formed; the unit is rejecting something it expects and doesn't find.

## The Melbourne disc in the car

The Melbourne rebuild (factory-size `carindb`, nothing moved) went in next. The unit **accepted it**: warning, Start Menu, Navigation — no media error. It reached the map screen, drew the position cursor, and then sat with the hourglass spinning over an essentially blank map field (no roads drawn); it did not lock up. On a later reload the unit reported "No data carrier in navigation computer!", which may be the drive or the unit giving up rather than a format fault.

Accepted-but-blank is a different failure from disc 3's outright rejection, and it rules the container out: the ISO, the licensing block, the root and the directory units are all read fine. What the rebuilt disc *lacks* compared with the factory are whole tables: 0x04 (routing / house numbers, 4,150 units), 0x06 (per-tile network, 683), 0x10 (POI records, 573) and 0x11 (POI index, 30). Everything the build did write validates offline (tiles, grids, lookups), so the hang is most plausibly the unit initialising a subsystem whose table no longer exists.

Both dropped tables live in perfectly contiguous factory blocks, so the build now has `--keep-factory-poi`, which reserves them like the background block:

| Block | Extent | Contents |
|---|---|---|
| POI slab | `0x1DB2800`–`0x2520C00` | tables 0x11, 0x10, 0x06, the POI grid (3 × 0x08) and its 804 cells — nothing else |
| Routing | `0x4609C00`–`0x4A9FA00` | table 0x04 and one 0x08 |

With the flag, the rebuild keeps both blocks byte for byte at their factory offsets, skips the empty-POI grid, and leaves root entry 1 pointing at the factory POI grid (the rebuilt search already writes zero POI links). A Melbourne build with the flag is `disc7_melbourne_keep_poi.iso`: `carindb` lands on exactly the factory size again, so the whole disc geometry is unchanged, and the kept POIs are the 2004 factory ones — stale but structurally real. Verification: all reserved ranges byte-identical, only root entries 0/2/3/4 patched, all 8,438 zlib units round-trip, 16 tests pass, tile/grid audit clean.



Three discs were built from the working factory image so each changes exactly one thing:

| Disc | Change vs factory | Isolates |
|---|---|---|
| 4 `disc4_tpd_moved.iso` | 4,096 zero sectors inserted between `carindb` and `TPD/` — map data byte-identical, licence files at LBA 51,038 | licence files' absolute position |
| 6 `disc6_grown_factory.iso` | factory `carindb` bytes unchanged, grown to 178,578,432 with a 0x05 extension chain; same sector count (87,728) and `TPD` LBA (87,561) as disc 3 | `carindb` size / disc geometry, with factory content |
| 5 `disc5_melbourne_rebuild.iso` | the Melbourne rebuild: rebuilt content, `carindb` exactly factory size, nothing moved | rebuilt content at factory geometry |

| Outcome | Means |
|---|---|
| 6 boots | size and licence position are fine → disc 3's fault is in the rebuilt content |
| 6 fails, 4 boots | the unit ties something to `carindb`'s length or the volume size; full-AU must then be made to fit the factory file size |
| 4 fails | the unit expects `TPD/` at its factory position; a rebuild that changes size must keep the licence files pinned |

Disc 5 separates "the rebuild is wrong everywhere" from "the rebuild is wrong only at full scale".

## What the isolation discs found

All three ran in the car:

| Disc | Result | Reading |
|---|---|---|
| 4 — licence files moved +4,096 sectors, factory map bytes | **would not load** | the unit does not find `TPD/` where it moved it |
| 6 — factory map bytes, `carindb` grown to 178,578,432, licence files where disc 3 put them | **works perfectly** — map paints, zoom in and out, position cursor | `carindb` size is irrelevant, and so is the absolute disc position of `TPD/` |
| 5 — rebuilt Melbourne content at factory size | boots to the map, then **hard-locked on zoom**; the unit had to be unplugged | the rebuilt *content* breaks it |

*Correction (review, below): disc 4 didn't move the licence files, it blanked them, so it says nothing about where `TPD/` may sit. Disc 6 is the clean geometry result: `carindb` grown, `TPD/` moved with its directory updated, and it works.*

Disc 6 also shows why the factory data is the wrong end point: it is 2004 data, so anything built since — the Hopper's Crossing estates included — is simply absent. Expected, not a fault.

### Tile-layer audit of the rebuild

With in-tile references decoded correctly (every one is an **absolute byte offset**, and node references legitimately land in s5 *or* s6), the rebuilt tiles pass every invariant the factory passes:

| Check | Factory | Rebuild |
|---|---|---|
| road → node offsets in range, 8/16-byte aligned | clean | clean |
| shape lists monotonic, fence at section end | clean | clean |
| per-node circular road lists close | clean | clean |
| s6 partner back-references (tile **and** offset) | clean | clean |
| grid cell sub-grid size vs section size | clean | clean |
| per-tile maxima roads / nodes / edges / shapes | 632 / 381 / 59 / 702 | 646 / 442 / 65 / 1,952 |
| worst tile size | — | 33,495 B (limit 64 KB) |

The corruption is therefore not in the tile record graph. The remaining difference between disc 5 and the working disc 6 is the tables the build dropped — which is what disc 7 (`--keep-factory-poi`) tests.

These audits are reproducible: `python tools/carin_check.py <image>` walks every tile, grid cell and search leaf of a disc image and reports section, reference, circular-list, partner-link and range violations (exit status 1 when it finds any). It knows the reference conventions in [29](29_vz_map_disc.md#map-tile-header-table-000x03): absolute byte offsets, node refs into s5 or s6.


### The defect: self-loop roads left their node list unclosed

The factory has **no** road whose two ends are the same node, across 228,703 roads. The
rebuild produced 7 (128 over full Australia), and for 59 of them both next-road pointers
aimed back at the same record: the node's circular list never closes, so anything
walking "next road at this node" — the renderer included — loops on the spot. That fits
the disc 5 hard lock, which came on zoom.

`carin_write` built the links as `lst[(j + 1) % len(lst)]`. When a road's two ends meet
at one node it occupies two entries in that node's list, and "next" from the first is
the same record. The fix skips further entries of the same road, so the chain always
moves to a different road and closes. `tools/carin_check.py` (below) flags the old
behaviour as `node_list_no_close` and now reports 0 on the rebuilt disc.

## Full-Australia with the fix

Disc 10 (`disc10_full_au_fixed.iso`): the Geofabrik Australia extract, 2,177,471 drivable OSM roads, 1,702,375 nodes, 35,464 tiles (largest 33,495 B), factory POI layer and routing block kept, `carindb` 191,195,136 bytes, `TPD/` flush after it. `tools/carin_check.py`: **0 problems** over 74,729 units; all 50,113 zlib units round-trip identically. The pre-fix full-AU build (disc 8) carried exactly 46 `node_list_no_close` — the same defect at scale, now gone. Both disc 8 and disc 10 keep the factory geometry rule, so the expected in-car outcome is disc 9's behaviour across the whole country.

## Appendix: the screens, as photographed

Transcribed from the four photos of the Melbourne test (disc 5), via OCR — kept here because the exact wording is diagnostic evidence:

1. **First load** — full-screen warning over a plain background: `Attention!` / `Only use this system when it is safe to do so. It is more important to keep your eyes on the road and your hands on the wheel.` / `Due to constantly changing traffic conditions, we unfortunately cannot guarantee a 100% precision under all circumstances.` Single button `OK`.
2. **Start Menu** (after OK) — two-column menu grid: `Navigation | Trip computer`, `Map | Info`, `Settings | Emergency`, `TV/Video | Stand-by`, and `C-IQ` on its own row below. No map.
3. **1st load map view** — title bar `Navigation`; a blank pale map field with faint banding, **no roads, no labels, no scale**; the only drawn feature is a cursor (dark ring with pale cyan halo and a blue arrow). Status bar left→right: `12:16 PM`, an hourglass, a red `2`, a red battery icon, a slashed-`e` (no-signal style) icon, an aeroplane icon, a right-arrow-and-dot, and an empty `--:--h` readout. Pronounced LCD moiré from the photo.
4. **Reload cd 2nd time** — `Note` / `No data carrier in navigation computer!` / `Please insert data carrier.` with `OK`.

- The protection question is narrowed to a single empirical test in the car (disc 2 of the [29](29_vz_map_disc.md) plan). No further format work is blocked on it.
- The pipeline is reproducible from a fresh Geofabrik extract with two cache-building calls and one `carin_build` command; nothing depends on state from an earlier OpenLion build beyond what those calls produce.
## What this changes

- The C-IQ licence layer is untouched by a rebuild. `carindb`'s size is free, and `TPD/` can move as long as its directory entries follow it (disc 6).
- `carin_build --keep-factory-poi` keeps the factory POI layer byte for byte instead of dropping it. (Table 0x04 is house numbers for the factory's own tiles, not routing; rebuilt tiles don't reference it, so it is no longer reserved.)
- The self-loop node-list fix is mandatory; `tools/carin_check.py` is the regression gate — factory image and every build must report 0 problems.
- Melbourne-sized builds land on the exact factory `carindb` size, so the whole disc geometry is factory-identical.

## Review (2026-11-16, later)

A pass over every disc and every change since doc 29, before the next batch.

**Disc 4 tested the wrong thing.** Its directory entries for `TPD/` still point at LBA 46,943, which on that image is now zeros; the real licence bytes sit 4,096 sectors further on, where nothing points. The unit read blank licence files and rejected the disc, as it would any corrupted licence. So disc 4 says nothing about *where* `TPD/` may sit, and "`TPD/` must sit immediately after `carindb`" doesn't follow. Disc 6 is the clean geometry result: `carindb` grown to 178 MB, `TPD/` moved with its directory updated, works perfectly.

**The pattern behind disc 3's rejection.** `tools/carin_check.py`-clean images, grouped by whether the unit must follow a pointer past the licensing block (the factory end of `carindb`, 0x5AF9400):

| Disc | Root-referenced grids past the licensing block | Units past it | In the car |
|---|---|---|---|
| 3 (full AU) | levels 0, 1, 2, 3, 4 | 34,586 | rejected at load |
| 5, 7, 9 (Melbourne) | none | 0 | loads |
| 6 (factory, grown) | none | 638 (filler, unreferenced) | works |
| 10 (full AU, self-loop fix) | levels 0, 2, 3, 4 | 37,744 | not yet tested |

Disc 6 proves the unit tolerates a large file, not that it reads data out there: nothing it uses was past the block. Disc 11 tests exactly that.

**Disc 11 (`tools/carin_probe_far.py`).** The factory disc with one change: the detail-road grid (root entry 0, eleven 0x08 units) copied to just past the licensing block, root entry 0 pointing at the copy. Inside the factory span only the root page differs; all other files are identical; 2,000 random detail lookups through the moved grid return the factory's tiles; `carin_check` clean. If it draws the street-level map, the space past the licensing block is usable. If it's rejected, the map must fit before the background block (85 MB, 94 MB without the background layers).

**Fixes since disc 10** (all in disc 12):

| Fix | Why |
|---|---|
| Every tile has a parent on the level above | Coarse levels were tiled only where their roads run: on full AU 23% of level-2, 20% of level-3 and 4.8% of detail tiles had parent 0 (factory: 2.7%, 1.7%, 0.2%). Levels are now built finest first and each coarser level covers the centre of every tile below it, with road-free cover tiles where needed: 0 parentless tiles below level 1 |
| No self-loop roads | All 129 on disc 10 were on levels 1-3, where chain-merging closed ring roads on themselves (the factory has none). A merged chain now stops one segment short of closing. 50815c3's list-closing guard stays as a backstop |
| Reserved ranges can't be overwritten | `assemble` skipped a reserved range only when a unit crossed its start; one starting exactly on it was written over the kept factory bytes. Now any unit touching a reserved range moves past it |
| Start-up units first | Grids, grid cells and the search directory (0x08, 0x09, 0x0C, 0x0D, 0x0F) are laid out before tiles, so they land before the licensing block even when tiles overflow it |
| Overflow is an error unless asked for | `carin_build` stops if units don't fit before the licensing block; `--allow-overflow` builds the disc anyway (discs 10 and 12) |
| Detail shapes simplified to 1.5 steps (about 2 m) | OSM curves are far denser than the unit can draw: `carindb` 178.6 → 164.0 MB |
| 0x04 slab no longer reserved | Nothing references it once tiles are rebuilt (+4.8 MB of room) |

Disc 12 (`disc12_full_au_all_fixes.iso`): 2,177,292 roads, 35,624 tiles, `carindb` 164.0 MB with 25,821 tile and street units past the licensing block and none of the start-up units; `carin_check` 0 problems; tile validator 0 errors and 0 parentless tiles; 1,058,173 street entries resolve to 4,289,738 road records with 0 mismatches; POI, background and licence ranges byte-identical.

**Still open:**
- The kept factory POIs point at 2004 detail-tile addresses (about 140,000 references) that now land in rebuilt data. Until they're retargeted or dropped, navigating to a POI may read garbage.
- If disc 11 is rejected, the full-AU build must shrink from 164 MB to 85 MB (94 MB without the background layers): the street search's all-Australia list duplicates every per-suburb entry (about half of its 35 MB), and detail tiles are 110+ MB.

## Discs 7, 10, 11 and 12 in the car

All four: **load, no roads below 1 km scale, destination search hard-locks** (the unit has to be unplugged; the screen stays on after ignition off).

Disc 11 is the decisive one. It is the factory map with only the detail grid moved past the licensing block, so its tiles, cells and search are factory, yet it loses the street level exactly as the rebuilds do. A search of the whole factory database finds no second pointer to the detail grid (its address appears only in the root and in the grid's own unit header), so the unit reaches it through root entry 0 alone, and moving it is what broke street level. Disc 7 shows the same symptom with nothing past the licensing block, so location past the block can't be the only cause.

What all four share, and the factory never does:

| | Factory | Discs 7/10/12 | Disc 11 |
|---|---|---|---|
| detail grid at its factory address | yes | no | no |
| grid units (0x08) compressed | never (23 of 23 stored) | all | no |
| street-level tiles compressed | 2 of 6,454 | nearly all | no (factory) |
| largest unit, unpacked | 32,768 B (search); tiles 24,576 B | up to 33,792 B (disc 7) | factory |

## Batch A-D: one change each

`tools/carin_probe.py` makes factory discs with one change (the `far` mode reproduces disc 11 byte for byte). Each of A-C was checked: `carin_check` clean, 2,000 random detail lookups identical to the factory, and every unit decodes to the factory's content.

| Disc | Change | If street level disappears |
|---|---|---|
| A `discA_factory_zlib_grid` | detail grid zlib-compressed in place | the unit can't read compressed grids |
| B `discB_factory_grid_moved_inside` | detail grid moved inside the file (over the start of the 0x04 block; house numbers lost for 239 tiles in south-west WA) | the grid's address matters, not "past the licensing block" |
| C `discC_factory_zlib_tiles` | the 502 stored detail tiles zlib-compressed in place (check Parkdale and the Bacchus Marsh-Geelong road area) | the unit can't read compressed street-level tiles |
| D `discD_melbourne_factory_shaped` | Melbourne rebuild shaped like the factory: grids and street-level tiles stored, no unit over 32 KB (largest tile 27,875 B), nothing past the licensing block, every fix so far | (the first candidate; A-C explain it either way) |

`carin_build` changes for D: grid units are always stored; a build stops if any unit would unpack to more than 32 KB; `--stored-tiles` stores street-level tiles (Melbourne still fits the factory size); `--detail-tol` sets the detail shape tolerance.

Also asked of the tester: does destination search work on the untouched factory disc? If it locks there too, the search lock is the unit, not the rebuild.

## Batch A-D results

| Disc | Loads | Below 1 km | Search |
|---|---|---|---|
| A, compressed detail grid | yes | roads as original | works |
| B, detail grid moved inside the file | **"Wrong or defected media"**, no Navigation or Map in the menu | — | — |
| C, compressed street-level tiles | yes | roads as original | works |
| D, Melbourne, factory-shaped (stored tiles) | **"Wrong or defected media"** | — | — |

**A and C clear both compression suspects**: the unit reads zlib grid units and zlib street-level tiles like the factory's own, so stored units buy nothing and cost space.

**B and D, the saved destination.** The unit had a destination set from earlier testing (photographed: Mount Barker, WA / Muirs Highway). The 245 house-number units B overwrote belong to 239 tiles at 114.9-118.3°E, 32.0-35.2°S, which takes in Mount Barker (117.7°E, 34.6°S); the Melbourne discs have no WA roads at all. A unit that restores its destination at start-up by following a pointer into the disc would find damaged or foreign data on both, and calling that a defective medium fits. The destination may also be behind disc 3's rejection (it may have been set during the disc 2 label test, which was on the Muirs Highway) and behind the "no street level, search locks" state on 7, 10 and 12.

D is also the only disc ever tested with large stored street-level tiles (2,480 over 4 pages; the factory's largest is 2 pages), which could be a second, independent cause.

**Next batch**, each tried after cancelling guidance and clearing the destination with the original disc: B and D again, and E, which is D with zlib street-level tiles (38 small ones stored, as the factory does). E passes `carin_check`, the tile validator (0 errors) and the search walk (612,254 road links, 0 errors).

| B | D | E | Reading |
|---|---|---|---|
| loads | loads | loads | the saved destination was the cause; any grid address works inside the file |
| loads | refused | loads | destination for B; large stored tiles for D |
| refused | — | — | B's own change is the cause (the 0x04 block start, or the grid's address) |

**For users:** whatever the result, a destination or route saved against the old map can make a new disc look defective. The finished product must tell owners to cancel guidance and clear saved destinations before switching discs, and to re-enter address-book entries afterwards.

## More than one thing wrong: an audit of disc E against the factory

While B, D and E are tested, disc E was compared with the factory field by field, looking for faults independent of the start-up question.

**Grid order** (explanation 3 for B): grid after its tiles is harmless (discs 5, 7, 10 load with it). Grid after its own cells occurs on exactly one readable disc, B; disc 11 also has it but its grid is unreadable. Still open for B.

**Child lists were wrong on every rebuild.** `overlapping()` probed for the coarse tiles above a finer tile at the coarse level's minimum tile size. A street-level tile smaller than that step was probed outside itself: on disc E, 1,442 of 2,672 street-level tiles were missing from their level-3 tile's child list and as many were listed under the wrong level-3 tile (disc 7: 1,524 of 2,846). Levels 1 and 2 were unaffected. If the unit reaches street level through these lists, this alone explains "no roads below 1 km" on discs 5, 7, 10 and 12. The probe step is now never bigger than the tile; every coarse tile now lists exactly the finer tiles under it.

**Coarse-tile 0x5C is not the tile's size.** On the factory it is the size of the smallest finer-level tile under the tile (83-96% of coarse tiles, and smaller still on the rest, never larger). The rebuild wrote the tile's own size, which matches about 5% of factory level-3 tiles. Now: the smallest finer tile under it.

**Road class codes outside the factory's set.** 6% of disc E's roads (24,007 of 377,257) carried codes that appear nowhere on the factory: `0x8B14`, `0x910C`, `0x8B01`, `0x1601`, `0x2011`. `carin_net.factory_class` now snaps every code to one the factory uses: direction never changes (a two-way road stays two-way) except that a slip road with no direction becomes one-way forwards; the road's form is kept before its speed. `0x8B14` becomes `0x9114`, `0x910C` becomes `0x8B0C`, `0x8B01` becomes `0x8B11`.

**No empty coarse tiles.** The factory never has a level 1-3 tile without roads; the parent-cover pass made a few. They are dropped, and the tiles under them keep parent 0 as some factory tiles do.

Disc F (`discF_melbourne_child_fix.iso`) is disc E with these four fixes: `carin_check` clean, tile validator 0 errors, search walk 612,254 road links with 0 errors, every coarse tile's child list and 0x5C as above. The tester was asked to burn F instead of E if E isn't burned yet.

## Disc E refused: what separates refused discs from loading ones

E (Melbourne, zlib street-level tiles) was refused like D, so D's large stored tiles were not the cause. Every tested disc was then scored on a dozen properties against its outcome (`/tmp` analysis, reproduced here):

| Property | Refused: 3, B, D, E | Loaded: 1, 2, 5, 6, 7, 10, 11, 12, A, C |
|---|---|---|
| a stored 0x08 grid unit at a readable address where the factory has none | B, D, E | none |
| the POI grid (root entry 1) past the licensing block | 3 | none |
| zlib 0x08 grid units | 3 | 5, 7, 10, 12, A |

No single property splits all four refused discs from the rest; these two together do, with no exceptions. B's grid was a byte copy of the factory's (stored) placed at a new address; D and E used `GridUnit.compress = False` ("store like the factory"), added after disc 7. Every rebuild that loaded had zlib grids, and A showed a zlib grid works in the factory position. Why the unit objects to a relocated stored grid isn't known; the rule is enough: **grid units are always zlib** (`force_zlib`, even when compression saves less than a page). Disc 3's cause can't recur: the POI layer is kept in place, start-up units are laid out first, and overflow past the licensing block is an error.

This explains B without the saved-destination theory, which stays untested.

Disc G (`discG_melbourne_zlib_grids.iso`) is F with zlib grids; its only stored grids are the factory's own, unchanged, at their factory addresses (POI and background), as on every disc that loaded. `carin_check` clean, tile validator 0 errors, search walk 612,254 road links with 0 errors. F was withdrawn before burning.

**Saved-destination theory: ruled out.** B and D were retried after cancelling guidance and clearing the destination with the original disc; both were still refused. The stored-grid rule above is the only explanation left that covers B, D and E.

## Fitting the map: state discs

Disc 11 showed the unit can't read past the licensing block, so everything must fit in the space before the background block: 77.6 MB with the factory POI slab kept, 85.3 MB without it.

Lossless savings first: merging street-level roads that pass through a node no other road touches, when name, route numbers, class, suburb and urban/rural all match (2.18 M → 1.93 M roads); suburbs sharing street-search units instead of one mostly empty unit each; a larger street-level tile budget (largest tile 23 KB, under the 32 KB ceiling). All of Australia went from 164 MB to 148 MB of `carindb`, still about 53 MB over. For scale, the factory fitted about 1.06 M street-level roads in 32.7 MB using the 0x0E codec (about 31 B a road); OSM has twice the roads and zlib manages about 50 B a road.

So: **one state per disc** (`carin_build --states VIC`). Street detail for every road in the state (by the OSM state boundary its midpoint falls in), plus every road level 3 carries nationwide (`STATE_DISC_NATIONAL_GROUP = 2`), so every coarse tile has street-level tiles under it as on the factory; with group 1 instead, 523 level-3 tiles outside Victoria had nothing under them. The three coarse levels are national. Search follows the street level: full suburbs and streets for the state, major roads elsewhere.

| Disc | Street-level roads | New data | Free before the background block |
|---|---|---|---|
| Victoria | 690,946 | 52.4 MB | 25.1 MB (+7.7 MB if the factory POIs are replaced) |

The Victoria build passes `carin_check`, the tile validator, the search walk (304,577 street entries, 1,160,486 road links, 0 errors), has no relocated stored grid, exact child lists, and the POI, background and licence ranges byte-identical. The spare room goes to current OSM POIs (one state per disc, not several states sharing).

