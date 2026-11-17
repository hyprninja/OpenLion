# 33: Codec 0x0E (work in progress)

## Where it's used

The unit header's codec byte 0x0E appears on:

| Units | Table |
|---|---|
| 5,950 | street tiles (0x00), 92% of them |
| 742 | street search (0x0E) |
| 1,146 | background (0x14, 0x15, 0x16, 0x1C, 0x1D) |

The low byte is the unpacked size in pages, as with zlib.

It is **not** a general compressor. Each table's plain header stays readable, some
sections are copied raw, and the rest is a bit stream that packs the records field by
field.

## Street tiles (table 0x00): container, verified on all 5,950

```
0x00-0x73   plain tile header: section offsets and counts for the UNPACKED tile,
            bbox, house-number unit, parent, the 0x5C-0x70 words
0x74        u8 p0, u8 p1      (p0 7-15, p1 7-11; meaning not known yet)
0x76        s3, raw: the 8 x (first road, first node) group table, 32 bytes
            s9, raw: n9 x 8 bytes (neighbour address, point on the shared edge)
            s10, raw: n10 x 8 bytes (forbidden turns)
...         bit stream, MSB first, to the end of the unit (zero padded)
```

## Bit stream: decoded so far

**Edge-node records (s6).**
- One per edge node, in s6 order. Proven on all 5,950 tiles: every tile has exactly n6
  matches, and all 153,366 cross-tile pairs point back to the right record.
- Each holds, in order:
  - x (15 bits, 64-unit steps from the bbox minimum);
  - a short code, `0` or `11`;
  - y (15 bits);
  - a short variable code;
  - the neighbour tile's address (32 bits, raw);
  - the partner node's offset in the neighbour's s6 (16 bits, raw).
- Coordinates are checked against the 922 edge nodes whose partner sits in a plain tile.
  Some records store their coordinates another way (probably as a delta), so this layout
  isn't complete yet.

**Order of the stream.**
- Edge nodes show up as the roads reach them, so the stream seems to go road by road:
  road fields, any new nodes, then the shape points.
- A tile with 1 road, no inner nodes and 2 edge nodes has about 368-400 bits before its
  first edge record. Those bits follow a fixed template across 312 such tiles, with a few
  fields that change.

**Not yet decoded:**
- the opening fields (names hierarchy s0-s2, the road record);
- shape points (not fixed-width deltas; tested widths 3-16 bits);
- strings (not fixed-width letter codes from 4 to 8 bits, so probably Huffman or a
  dictionary);
- p0 and p1.

## Second round (same day)

**Opening fields (one-road tiles).**
- Every tile's stream starts with the same 24 bits: `1` followed by 23 zeros.
- Bits 25-40 hold the offset where the unpacked tile's strings start (exact on all 77
  tested tiles).
- Bits 96-111 hold that offset minus 1,014.
- The road's 32-bit road ID is stored raw, at bit 312 in one-road tiles. It matches the
  level-3 road's ID where the two share one.
- Bits 80-95 (≈13,000-14,500) change with the shape data, not with the shape-point count.
  Not identified.

**Strings.**
- They sit in one block near the end of the stream, after the shape points.
- Tiles with identical names share an identical 440-bit block, even when their roads
  differ.
- The code for "australia" is 31 bits (about 3.4 bits a letter), identical in 212
  WA/SA tiles and appearing twice there (country and state name). That points to an
  entropy code: probably a Huffman table built per tile, or a shared table chosen by
  context.
- Not raw, not fixed-width (4-9 bits, any letter offset or substitution), and no
  references to other units.
- Searched without success: a single prefix code for the tile's five names, in any order,
  with or without terminators or length fields.

**Shape points.**
- One-road tiles show a repeat period of p0 + p1 bits (23 when p0, p1 = 15, 8; 24 when
  they are 15, 9). So the two parameter bytes are most likely field widths for the
  shape-point records.
- The exact split of a record isn't found yet. Fixed splits checked against the road's
  known ends and its OSM path don't fit, so there is probably a per-point flag or
  escape.

## What can already be read from every town tile without the full decode

- every section count and offset, the bbox, house-number unit, parent and header words;
- the group table (first road and first node of each group);
- neighbours and the shared-edge points;
- forbidden turns;
- every edge node's partner tile and partner offset, and most edge-node coordinates.

## Ground truth available for the rest

- Edge nodes shared with the 504 plain street tiles: 922 exact coordinates and offsets.
- Zoomed-out roads: every level-3 node sits on a street node (verified on the plain
  tiles), so the zoomed-out levels give exact street-node positions.
- Street search (0x0E, zlib): tile, first road offset and road count, plus the street
  name, for every street.
- House-number units (0x04, plain): the road count and numbers per road, in tile order.
- POI road links: tile, road offset and side.
