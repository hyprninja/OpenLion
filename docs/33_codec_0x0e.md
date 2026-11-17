# 33: Codec 0x0E

17 Nov 2026. Code: `openlion/carin_codec_e.py`; check: `tools/carin_research/check_codec_e.py`.

## Status

| Part of a street tile | State |
|---|---|
| Name hierarchy s0-s2, roads s4, nodes s5/s6, shape points s7 | **Decoded.** 5,950 of 5,950 tiles pass every structural check |
| Name entries s14, the entry pointers of s0/s1/s2, road word +24 | **Decoded.** 5,950 of 5,950 tiles pass every tail check |
| String text | **Decoded.** One fixed prefix code for the whole disc; all 5,950 tiles decode to exactly the stored length with a NUL before every string start (4.9 MB of text) |
| Plain-byte output (`to_plain`) | **Done.** All 5,950 tiles unpack to plain bytes that land exactly on their header's section offsets and round-trip byte for byte through `carin_tile.parse` / `write` |
| Street-search units (742) and background units (1,146) | Not started |

It is a field-by-field bit packing, not a general compressor, and has nothing to do with
the licensing data.

## Where it's used

5,950 street tiles (table 0x00), 742 street-search units (0x0E), 1,146 background units.
The low byte of the codec word is the unpacked size in pages.

## Street tile container

```
0x00-0x73   plain tile header, describing the UNPACKED tile
0x74        u8 p0, u8 p1
0x76        s3 raw (32 bytes), s9 raw (n9 x 8), s10 raw (n10 x 8)
...         bit stream, MSB first, zero padded
```

Widths used below:

| Name | Value |
|---|---|
| P | bits of an in-tile byte offset: `bit_length(pages * 512 - 1)` (10 for 2 pages ... 15) |
| p1 | width of a coordinate delta (nodes and shape points) |
| p0 | a short road ID has `16 + p0` bits |
| W4 | `bit_length(n4)`; W7 `max(1, bit_length(n7))`; W10 `max(1, bit_length(n10))`; Ws `bit_length(n2 - 1)` |

Two coding habits run through the whole stream:
- **Explicit or previous:** one flag bit; 1 = the value follows, 0 = same as the previous
  record.
- **Coordinates:** `0` + p1 bits = previous + v; `10` + p1 bits = previous − v;
  `11` + 16 bits = absolute. The first node of s5, the first of s6 and the first shape
  point are a bare 16 + 16 bits.

## Stream order

**1. Name records, s0 then s1 then s2** (n0 + n1 + n2 records):
`flag`; if 1: `str2` (P), `parent >> 1` (P−1); then `str` (P). Flag 0 keeps the previous
record's str2 and parent. The other two words of each record come at the end of the stream.

**2. Roads** (n4 records, tile order):

| Field | Coding |
|---|---|
| s10/s11/s12 indices | flag; if 1: W10 + 1 + 1 bits |
| class, flags, attributes | flag; if 1: 3 × 16 bits |
| node A, node B | `offset >> 2` in P−2 and P−1 bits |
| 1 bit | always 0 (probably the flag of word +24) |
| first shape point | flag; if 1: index in W7 bits |
| next road at A, at B | road indices, W4 bits each; n4 = none (offset 0 in a plain tile) |
| road ID | flag; 1: 32 bits; 0: 16 + p0 bits, high bits zero |
| street record | flag; if 1: s2 record index in Ws bits |

Then the closing record: the s10 group (flag, value) and the shape index (flag, value).
They equal n10 and n7 on every tile.

**3. Nodes**, s5 then s6: flags (flag; if 1: the top 11 bits of the 16-bit word), x, y,
first road (W4, an index). Edge nodes add the neighbour address (32) and partner offset (16).

**4. Shape points:** x, y, then 3 bits (always 0 on the 1.2 million points).

**5. Name tail:**

| Part | Coding |
|---|---|
| string table start, end | P bits each; the end leaves out the last NUL |
| dictionary | 6 entries: 5-bit length, then 7-bit ASCII characters. Unused slots hold one byte 0x41. Typical: `ali aus ern est out`, or `road ral aus der gin gui`. Often alphabetical but not always (`ark`, `ay\0`, `ghw`, ` hi` on 0x3F16A00), and an entry can hold a space or the NUL that ends a string |
| coded text | all strings in table order, each with its NUL, `end + 1 - start` bytes in all (see "String code" below) |
| s0 word 1 | flag 1 + `0x0000`, flag 1 + `0x000D`, flag 1 + `0x0000`: the 13 of the country record; identical on every tile |
| per-road column | n4 + 1 flag bits, always 0 |
| name entries (n14) | pointer (flag, P bits); flags high byte (flag, 8 bits); flags low 5 bits (flag, 5 bits) |
| s2 entry words | per record, two values: flag; if 1: a delta `>> 1` in P−1 bits; 0 repeats the last delta. Values accumulate |
| s1 entry words | per record 2 × (P−1) bits, `>> 1`, absolute |
| s0 entry word | per record P−1 bits, `>> 1` |
| 2 bits | always `00` |
| road word +24 | per road and the closing record: flag; if 1, 16 bits; 0 repeats the previous value. Bits 8-9 are the slip-road type (1/2/3 on forms 1/8, 2/9, 3/A, 100% on 1.16 M roads); 0x400 and 0x1000 occur as on levels 1-3 (doc 32). This column is what the earlier notes saw as "extra groups after `001`" on 844 tiles: tiles with no non-zero +24 end in `001` and zeros |

## How it was checked

- `check_codec_e.py`: on all 5,950 tiles the decoded edge nodes carry exactly the
  neighbour and partner values found independently by scanning for neighbour addresses
  (153k records); the closing record gives n7 and n10; every road end is a valid node
  offset; every next-road index is ≤ n4; all 1.0 M nodes and 1.2 M shape points lie
  inside their tile.
- Node deltas against the 922 edge nodes whose position is known from a plain neighbour:
  correct (the handful of misses in the research script were its own parse errors).
- Shape points against OpenStreetMap on the 312 one-road tiles: sampled tiles follow the
  road within a few units (1 unit ≈ 1.3 m); one tile differs by up to 133, where the road
  has since been realigned.
- Road attribute bit 0 against the plain house-number units: 185 of 185 sampled roads
  agree, which also proves the stream is in tile order.
- Short road IDs against level 3: 117 one-road tiles share the ID with a level-3 road and
  the missing high bits are zero in all of them.
- Street records against street search: on the tile worked through by hand (0x296F400)
  every road's record matches its searched name.
- Name entries on tiles worked by hand reproduce the expected pointers and flags
  (0x200 area, 0x10 route, 0x20A second street name).

## Guessed, not proven

- That the 1-bit road field and the per-road column are flags of word +24 and the s13
  pointer. They are 0 everywhere, so the unpacked value is 0 either way.
- Short road IDs on roads after the first: taken as absolute. Only first roads were
  checked against level 3.
- Flag-0 on the first road's street record: taken as record 0... the default before the
  first explicit value is not established (one record index is never written explicitly
  in about two thirds of tiles; it is most likely 1).
- Node flag word: only its top 11 bits are stored; the low 5 are taken as 0.

## String code

One prefix code, the same on every tile (18 Nov 2026):

| Code | Meaning |
|---|---|
| `000` `001` | a, e |
| `0100` `0101` `0110` `0111` | s, t, r, NUL |
| `10000`-`10111` | space, d, g, h, i, l, n, o |
| `110100` + 3 bits k | k = 1-6: dictionary entry k−1; k = 7: apostrophe; k = 0 never seen |
| `110101` + 3 bits | 4: `/`, 6: `.`; others never seen |
| `11011` + 4 bits | digit 0-9 (10-15 never seen) |
| `1111` + 5 bits | any letter by number, a = 1 ... z = 26 (0 and 27-31 never seen) |
| `1100…`, `1110…` | never seen |

How it was found:
- 4,652 tiles whose dictionary has no fragment of "australia" all start with the same
  bits, so the code is fixed, not built per tile.
- Grouping tiles by how "australia" splits against their dictionary (`a u s t r a l i a`,
  `aus t r ali a`, `a u s t r a l <slot>` and so on) gave the slot codes and the
  first letters.
- Growing the code over 1,140 tiles whose strings are known from street search gave the
  rest. The letter and digit groups were then read off their pattern.

Checks:
- All 5,950 tiles decode to exactly `end + 1 − start` bytes, with a NUL before every
  string a record or entry points at, and no other NUL.
- The characters used are a-z, 0-9, space, `'`, `.` and `/`.
- 99.5% of the 389,722 street-search names for these tiles appear in their tile's
  strings, and 95.7% equal the name of the road's own street record. The rest are
  route-first records (the record's first name is the route number). The 0.5% missing
  are search-only aliases (flag 0x102, mostly bridge names) and spelling variants,
  e.g. search `bridport point,port` against the tile's `bridport point/port`.

## Plain output and the factory rules on town tiles

`carin_codec_e.to_plain(model, unit_header)` lays the model out as the plain tile.
- Every pointer is rebuilt from the section offsets in the tile's own header: road ends,
  shape and s10-s13 pointers, next-road links (index n4 = 0), street records, first roads.
- The s0 records are `(str, word1, entry, 0, 0)`; the s1 and s2 records are
  `(str, str2, parent, entry, word5)`.
- The closing road record carries the s10 and shape ends and the last +24 value.

`tools/carin_research/check_plain.py` unpacks every town tile and runs the street rules
from docs 31 and 32:

| Rule | Original town tiles |
|---|---|
| Round trip identical, layout = header offsets | 5,950 / 5,950 |
| Road runs west to east | 1,159,823 / 1,159,823 |
| Junction list holds every road | 1,002,141 / 1,002,141 |
| Junction list clockwise | 549,368 / 549,402 |
| List starts at the first road clockwise from north | 542,850 / 549,402 |
| Inner nodes sorted by group, x, y | 5,950 / 5,950 |
| Group table, roads sorted by group | 100% |
| No zero-length road | 100% |
| Attribute bit 0 = road has house numbers (against the plain 0x04 units) | 1,056,833 / 1,056,833 |
| House-number unit has one record per road | 4,122 / 4,122 |
| Edge node at the same point as its partner | 153,366 / 153,366 |
| Junction-type bits match the road count | 838,614 / 846,582 |

On all 6,454 street tiles, plain ones included:
- node level bits = coarsest level: 1,004,337 / 1,004,392;
- every level-2 and level-3 node is also a street node: 139,743 / 139,743;
- every street tile is inside its parent and has a neighbour.

It also showed three things the 504 readable tiles had hidden, which correct doc 31:
- two nodes share a position 1,345 times;
- 153 edge nodes sit on tile corners;
- junctions reach 8 roads.

Other new measurements:
- the longest street-level road has 253 shape points in town (588 in the country);
- the longest map label is 43 characters;
- route numbers are at most 4 characters, always with a shield code: `s n ni c a t b x m mi
  ai f i`;
- shape-point marks 0x100 and 0x200 occur at street level too (2,014 and 127 points).

## Open

1. Street-search and background units.
2. The guesses listed above (defaults for "same as previous" on a first record, node
   flag low bits). They give values that pass every rule above, but can't be proven
   without a second copy of the data.

## Picking this up

Run from `tools/carin_research` (the disc is `Other Cards/VZ test discs/1-control-factory.iso`):

```
python check_codec_e.py FACTORY.iso         # 5,950 structure, 5,950 name tail and strings
python check_plain.py FACTORY.iso           # unpack to plain bytes, round trip, street rules
python codec_e_strings.py FACTORY.iso 26d7800 296f400 3f16a00
```

Tiles worked through by hand: 0x26D7800 (5 strings: `australia`, `south coast`, `n1`,
`south western highway`, `western australia`), 0x296F400, 0x3F16A00, 0x298FC00.

Suggested next: **street-search and background units.** Not looked at; expect the same
habits (explicit-or-previous, three-way coordinate deltas, this string code).

What would settle the guesses: the head unit firmware (the unpacker shows the defaults for "same as previous" on a first record). The disc's CARINET file is the
traffic-message table, not code (doc 32).
