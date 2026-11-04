# Phase 3a: Road table (`.rd`) and string table (`.str`)

Date: 2026-11-04. Parser: `openlion/roads.py`. `roads.py` can dump every road to TSV locally (not distributed).

## Key discovery: `au.mod` is the schema

`au.mod` (uncompressed, 40 KB) is a machine-readable **data model** ("Arriba 5.2.1, Europe and China Unification with Truck Attributes"). It names every table, header field, record field, size, bitfield and cross-reference. It holds 1,552 entries.

Entry shape (partly decoded): `u8 type, name\0, u16 varId (0xFFFF = none), u32 minSize, u32 maxSize, child count / bitfield info, …`. Fields can reference other fields by `varId`, for example a list count pointing at `nameNum`. Type bytes seen so far:

| type | meaning (inferred) |
|---|---|
| 0x00 | fixed char array |
| 0x02 | u8, or a bitfield inside one |
| 0x08 | unsigned int, or a fixed struct |
| 0x0A | u16 / list container |
| 0x0B | table root |
| 0x18 | signed int32 (all lat/long, IDs, counts) |
| 0x1A | u32 reference (offset or ID into another table) |

**Decoding the rest of the format is now mostly a matter of reading this schema properly.** A general schema interpreter is the next tool to write.

## Common header (0x5E bytes), every decompressed table

| Off | Type | Field |
|---|---|---|
| 0x00 | char[16] ×5 | stamp `SIEMENS`, copyright, db_release `2.0.0`, db_version `AU5059903F.84`, comp_version `5.0.5` |
| 0x50 | u32 | dbID |
| 0x54 | u16 | fileID (rd = 4, str = 60) |
| 0x56 | u32 | rec_cnt |
| 0x5A | u32 | byte_cnt (= decompressed size) |

## Road record (`auz.rd`)

624,756 records, variable length, packed (no alignment):

| Off | Type | Field | Notes |
|---|---|---|---|
| 0 | u8 | countryID | 0 = AU (548,381), 1 = NZ (76,375) |
| 1 | u32 | cityID | into `.cty` (to verify) |
| 5 | u8 | roadInfo | bitfield: highway, preferred, explicate, ramp, name type (3 bits), interchange. Bit order **unconfirmed**. 0x76 on 96% of roads, EastLink included |
| 6 | i32 | min_long | degrees × 10^5 |
| 10 | i32 | min_lat | |
| 14 | i32 | delta_long | bbox width |
| 18 | i32 | delta_lat | bbox height |
| 22 | u16 | maxNumber | house number range |
| 24 | u16 | minNumber | |
| 26 | u8 | nameNum | |
| 27 | u16 | intersectionCount | |
| 29 | {u32, u8} × nameNum | roadNameOffset, mainSeqNr | offset is relative to **end of `.str` header** (+0x5E) |

What a record is: one **named street within one locality**, with a bounding box and house-number range. In other words, it's the **address search index**. It isn't drawable geometry or the routing graph. Those live in the tiled `mp0` / `mg1-4` files, and the schema's `seg_list`, `seg_turn_restr`, `speed_restrictions`, `delta_long u16` and similar entries describe them.

Checks:

- Framing: all 624,756 records end exactly at `byte_cnt`.
- Names: all 889,578 references resolve to a string start.
- Round trip: re-serialising every record gives a byte-identical table.
- Ground truth: Swanston St, Melbourne at 144.962–144.967°E, 37.817–37.793°S, numbers 1–944. EastLink in 5+ segments through Bangholme and Dandenong South.

## String table (`auz.str`)

505,727 entries after the header: `u8 trans, char name[], 0x00`. The schema calls this `roadNameList`: `trans` then `name`.

- `trans = 0x04`: 505,648 normal names, sorted, uppercase Latin-1. Some carry `;L`-style suffixes and `, LOCALITY` qualified forms (e.g. `EASTLINK, BANGHOLME`).
- `trans = 0x02`: 79 placeholders `[n]` for unnamed roads.

## Compression round trip

`openlion/flat.py pack()` rebuilds **all 16 FLAT tables byte-identical** to the originals using standard zlib level 9 with 3,072-byte blocks. Edited tables can therefore be written in exactly the factory style.

## Open items

1. Write a general `au.mod` schema interpreter, so every table parses from the schema instead of hand-written layouts.
2. Use it on the `mp0` / `mg1-4` tile directory and segment records (geometry and routing). That's the bulk of a converter.
3. Confirm roadInfo bit order, and the cityID → `.cty` link.
4. Tamper test (in car). The tooling for it is ready: edit one string in `auz.str` (same length), `pack()`, copy to a spare card.
