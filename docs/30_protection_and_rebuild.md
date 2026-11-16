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

## What this changes

- The protection question is narrowed to a single empirical test in the car (disc 2 of the [29](29_vz_map_disc.md) plan). No further format work is blocked on it.
- The pipeline is reproducible from a fresh Geofabrik extract with two cache-building calls and one `carin_build` command; nothing depends on state from an earlier OpenLion build beyond what those calls produce.
