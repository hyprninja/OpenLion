# 33: Codec 0x0E

17 Nov 2026. Code: `openlion/carin_codec_e.py`; check: `tools/carin_research/check_codec_e.py`.

## Status

| Part of a street tile | State |
|---|---|
| Name hierarchy s0-s2, roads s4, nodes s5/s6, shape points s7 | **Decoded.** 5,950 of 5,950 tiles pass every structural check |
| Name entries s14, and the entry pointers of s0/s1/s2 | Decoded on 5,106 tiles; 844 (mostly large town tiles) fail the tail check, cause not found |
| String text | **Not decoded.** Layout known (6-entry fragment dictionary, then a prefix-coded token stream); the code table is not recovered |
| Plain-byte output through `carin_tile.write()` | Not written: it needs the strings |
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
| dictionary | 6 entries: 5-bit length, then 7-bit ASCII characters. Sorted; unused slots hold one byte 0x41. Typical: `ali aus ern est out`, or `road street place ave ...` |
| coded text | all strings in table order |
| s0 word 1 | `0x0000 1 0x000D 1 0x0000 1`: the 13 of the country record; identical on every tile |
| per-road column | n4 + 1 flag bits, always 0 |
| name entries (n14) | pointer (flag, P bits); flags high byte (flag, 8 bits); flags low 5 bits (flag, 5 bits) |
| s2 entry words | per record, two values: flag; if 1: a delta `>> 1` in P−1 bits; 0 repeats the last delta. Values accumulate |
| s1 entry words | per record 2 × (P−1) bits, `>> 1`, absolute |
| s0 entry word | per record P−1 bits, `>> 1` |
| end | `001` |

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

## Strings: what is known

- The 31-bit run found earlier for "australia" is the token sequence `aus t r ali a`.
- Roughly 6 bits a token; the same word gives the same bits wherever it stands, so it is
  one prefix code over single characters plus the six dictionary slots, not a
  context code.
- Not fixed width (5, 6, 7 bits at any alignment), and a brute-force solve of one tile
  did not finish.
- Every string's start offset is known from the name records and entries, and the text
  of most is known (street names and regions from street search, `australia`, the state),
  so the table can be solved from many tiles at once: code lengths first (bit length of a
  block = sum of its tokens' lengths), then the codes.
- The head unit's firmware would show the table outright.

## Open

1. String code table.
2. The 844 tiles whose tail check fails (dictionary entries there contain NUL and space,
   e.g. `ve\0`, `er `; the stream ends with extra groups after `001`).
3. Writer to plain bytes, then `carin_tile.parse`/`write` round trip and the factory rules.
4. Street-search and background units.
