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

That pins the geometry rule: **`TPD/` must sit immediately after `carindb`.** The factory and disc 6 both do; disc 4's 8 MB gap breaks it. `carin_iso.replace_db` already splices them contiguously, so nothing there needs changing — disc 3's rejection was its content, not the move.

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


## What this changes

- The protection question is narrowed to a single empirical test in the car (disc 2 of the [29](29_vz_map_disc.md) plan). No further format work is blocked on it.
- The pipeline is reproducible from a fresh Geofabrik extract with two cache-building calls and one `carin_build` command; nothing depends on state from an earlier OpenLion build beyond what those calls produce.
