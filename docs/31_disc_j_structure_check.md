# 31: Disc J against the factory disc

Copied from the test-disc folder on 17 Nov 2026. Two reports: the first comparison, then the stricter structure check that replaces its guesses.

## Factory disc vs disc J: everything that isn't the same

17 Nov 2026. Factory = `1-control-factory.iso`; J = `discJ_victoria_child_slots.iso`.
Read-only comparison; nothing in the code or the discs was changed.

**Limit of the comparison.** 5,950 of the factory's 6,454 street-level tiles use the
0x0E codec, which still can't be decoded. Their headers (bbox, section counts,
house-number pointer, parent, the 0x60-0x70 words) are plain and were compared for all
6,454. Their contents (roads, nodes, names) could only be compared against the 504
decodable street tiles, which are mostly rural. Levels 1-3, search, POIs and grids are
fully decodable on both discs.

### 1. What is identical

- Everything outside `carindb`: the system area, both volume descriptors, every other
  file, and every LBA and size. `carindb` is the same size (95,392,768 bytes).
- Inside `carindb`: the volume header (0x12), metadata (0x13), root (0x07), country
  trie (0x0B), and the whole background and licence block from 0x5168200 to the end.
- All 17 grid (0x08) units sit at the factory addresses, with identical headers.
- Unit headers are well formed throughout (owner, page count). zlib streams have the
  same style on both: `78 DA` header, level 9, zero tail, same slack pages.
- Tile structure: section order, 4-byte alignment, the first section at 0x74, and
  s11-s13 always empty. Every tile is a factory shape (square or 2:1), aligned to its
  own size.
- Parent and child links: every tile with a parent is listed by that parent, and no
  tile is listed by anything else (3 exceptions, item 2.2). Edge nodes all lie on the
  tile edge, and all neighbour tiles exist.
- Chains (next/prev) in 0x0C, 0x0E and 0x10 are valid. Trie entry format, leaf flags and
  targets in 0x0D, 0x0F and 0x11 are the same.

### 2. Things the factory never does (structural)

Ordered by how likely they matter. For each, "also on" lists the earlier discs that have
it, to separate new risks from old ones.

### 2.1 Street tiles say they have house numbers, but don't
- In the header word at 0x70 of a street tile, 0x01010000 appears on the factory exactly
  on the 4,149 tiles that point to a house-number unit (0x54 ≠ 0); all others have
  0x00010000. The word evidently means "has house numbers".
- **J has 3,391 street tiles with 0x01010000 and no house-number unit (0x54 = 0)**,
  and no 0x04 units at all. A unit following the flag would read address 0 (the volume
  header) as house numbers.
- Cause: the builder copies 0x60-0x74 from whichever factory tile covers the new tile's
  centre (`carin_build.py:764-767`, `carin_write.py:299`).
- The same copy makes 0x60, 0x64 and 0x68 meaningless on J. On the factory they grow
  with the tile's size and look like lengths in metres (median 0x60: 737 on 0xC000
  tiles, 27,717 on 0x180000 tiles). On J they belong to a different-sized factory tile
  (median 4,446 on 0x180000 tiles).
- Also on: 7 (2,853), 12 (10,708), H (5,779). All of these loaded, so this doesn't block
  loading. It does sit on the street-level path, where every rebuild has failed.

### 2.2 A street tile bigger than its parent (new in I and J)
- 3 J street tiles of 0x180000 square have a level-3 parent that is only 0x180000 ×
  0xC000 (half as tall). They stick out of the parent, and the parent doesn't list them.
  They're at 145.08°E 41.9°S and 145.36°E 41.3°S (north-west Tasmania), and 152.16°E
  28.6°S (Queensland/NSW border).
- The same areas have 6 level-3 tiles whose slot size (0x180000) is taller than the tile
  itself, so their slot grid has **0 slots**.
- On the factory, every street tile is inside its parent and every coarse tile has at
  least one slot.
- Also on: none of 7, 12 or H. **This is the only structural fault here that is new in I
  and J compared with H**, the last rebuild that loaded.

### 2.3 Nodes that the other levels don't have
- On the factory, every level-3 node, including tile-edge crossings, has a street-level
  node at the same point (checked where the street tiles are decodable). Every level-2
  node has a level-3 node at the same point. Every level-3 node flagged 0x4000 has a
  level-2 node there.
- On J (all from simplifying each level separately, so tile-edge crossings land in
  different places):
  - **11,598 level-3 nodes have no street-level node at that point**, 11,554 of them
    tile-edge crossings (61% of J's level-3 edge nodes).
  - 2,139 level-2 nodes have no level-3 node at that point.
  - 2,704 level-3 nodes flagged 0x4000 ("also on level 2") have no level-2 node.
  - 2,652 level-3 nodes flagged 0x8000 have no street-level node.
- The node flag bits are used differently, too:

  | Level | Bit | Factory | J |
  |---|---|---|---|
  | Street | 0xC000 | about 66% of nodes in its decodable rural tiles | about 2% |
  | 3 | 0x8000 | 40% of nodes | 4% |
  | 1 | 0x1100 | 0.2% of nodes | 36% |

- Probably matters for routing and zoom changes, which move between levels at shared
  nodes.

### 2.4 Very long roads
- Most shape points on one road:

  | Level | Factory | J |
  |---|---|---|
  | Street | 588 (decodable tiles) | 3,702 |
  | 3 | 204 | 3,588 |
  | 2 | 324 | 1,788 |
  | 1 | 402 | 1,134 |

- 2,293 J level-3 roads have more than 256 shape points. A per-road buffer sized to the
  factory's data would overflow.
- Also on: 12 and H, both similar.

### 2.5 Route numbers (shield text)
- Factory route numbers always start with a shield code and are at most 4 characters:
  `ni`, `n`, `s`, `b`, `a`, `c`, `ai`, `t` (`n1`, `ni87`, `s32`, `b400`).
- J writes raw OSM `ref` values:
  - 13,170 are bare numbers (`1`, `30`, `120`).
  - There are prefixes the factory never uses (`m`, `acw`, `mr`, `rr`, `od`, `alt`).
  - Some are junk: `francis road`, `4wd`, `c&c`, and a 65-character gazette URL.
  - 80 are over 5 characters.
- Name-entry flags differ too:
  - Factory: 0x0010 for most route numbers, 0x020A for named highways.
  - J: 0x0011 for most route numbers, and never 0x020A.
- Map labels on J run to 76 characters (factory's decodable tiles: 30). J also uses `&`,
  `/` and `-` in street names, which no factory map tile or street record uses.
- Also on: 7, 12, H.

### 2.6 Tiles with no neighbours
- 25 J tiles have no neighbours and no edge nodes: islands (Christmas, Norfolk, King,
  Kangaroo) and stray road fragments inland. The factory has none (minimum 1 neighbour).
- Also on: 7 (2), 12 (3,094), H (26).

### 2.7 Country record (0x0A) still points at the old POI index
- J zeroed the national POI-list section (offset and count at 0x14 and 0x48). It left the
  four 12-byte entries in place.
- Those entries still point at 0x1DB2812, where the factory's POI index starts. **On J,
  that address is inside a street tile** (unit 0x1DB0200).
- Harmless if the count is honoured. Not harmless if something walks the entries.

### 2.8 Data where the factory has none
- J has street, level-3 and POI cells for Christmas Island (105.6°E), Norfolk Island
  (167.9°E), the Tiwi Islands, the tip of Cape York and south-west Tasmania.
- The factory's data stops at cells 113.4-153.6°E. J's street grid has data in 3,897
  cells, against the factory's 2,226.

### 2.9 Empty grid cells
- On the factory, each run of empty cells (in x-major order) gets its own empty 0x09
  unit, so every address appears in exactly one run: 736 empty units in the street grid.
- **J has one shared empty unit per grid**: 61,639 street cells point at one unit, which
  appears in many separate runs.
- On levels 1-3 the factory's empty cells still carry a sub-grid (4×4 on level 1, 2×2 on
  levels 2 and 3). The factory's sub-cell size is never above 0x300000. J's empty
  coarse cells are 1×1 with sub-cell size = cell size (0xC00000 / 0x600000).

### 2.10 Free space
- The factory has none. J has about 29.7 MB (57,942 pages) of empty extension units,
  in six regions before the licence block. They are chained extension to extension, up
  to 255 pages each.
- On the factory an extension unit always belongs to a real unit and is at most 5 pages.
- Also on: 7 and H (loaded). Disc 6 (works) has the same kind of filler, but only after
  the licence block.

### 3. Layout

The grids (0x08) and everything from the background on are at the factory addresses.
Everything else has moved:

| Table | Factory start | J start |
|---|---|---|
| 0x0D place trie | 0x1200 | 0x2DF400 |
| 0x09 street-grid cells | after the street grid (0x2560E00) | 0x1200-0x2DF400 |
| 0x0C places | 0x2000 | 0x2DFC00 |
| 0x0F street tries | 0x55400 | 0x349400 |
| 0x0E streets | 0xBC800 | 0x2CB1400 |
| 0x11 POI index | 0x1DB2800 | 0x2B53200 |
| 0x10 POI records | 0x1DE8E00 | 0x282C000 |
| 0x06 POI tiles | 0x2449E00 | 0x2B8A200 |
| 0x00 street tiles | 0x26D5A00 | 0xBC7200 (in three runs around the pinned grids) |
| 0x03 / 0x02 / 0x01 levels | 0x4AD6400 / 0x4E43000 / 0x503BE00 | 0x5EFA00 / 0x43AC00 / 0x38A200 |
| 0x04 house numbers | 0x4609C00 | none |
| 0x09 cells of levels 1-3 | right after each grid | elsewhere; the factory slots are filler |

Discs 5, 7, 12 and H moved the same tables and loaded, so moving them isn't on its own a
reason to refuse. (B moved the street grid itself and was refused; J doesn't.)

Codecs: the factory stores 92% of street tiles in codec 0x0E. J uses zlib for street
tiles (discs A and C showed the unit accepts zlib tiles) and stores some tiles at 3
pages (factory maximum: 2 on street level and level 1).

### 4. Field values the factory uses and J never writes

Meaning unknown for most. J writes the common value everywhere.

- **Road flags, low bits** (0x2, 0x5, 0x6, 0x9): on 30-60% of factory roads on levels
  1-3, and on street level (0x429, 0x129 …). Factory high bits: 0x8000 and 0x4000
  (0x8420, 0x5620). J: only 0x020, 0x120, 0x220, 0x320, 0x420.
- **Road word +24**: 0x100, 0x200, 0x300, 0x1000 … on 2-3% of factory coarse roads and
  0.3% of street roads. J: always 0.
- **Street road attributes +28, bit 0** (0x1081, 0x1001): 16% of factory street roads.
  Also 0x1030, 0x10B0, 0x1A80. J: only 0x1000 and 0x1080.
- **Third word of a shape point** (256 or 512): 2-4% of factory coarse shape points. J:
  always 0.
- **s10 per-road-end records**: on 33-47% of factory coarse tiles and 55% of street
  tiles. J: never (written empty on purpose).
- **Node flags 0x200 and 0x1-0x2 low bits** (0x1200, 0x5200, 0x1501 …), and **edge-node
  flags without 0x500** (0x3000, 0x7000, 0xB000). J: never.
- **Last word of the street records (s2)**: a pointer-like value on 7% of factory
  records. J: always 0x0004.
- **Road IDs**: the factory's are spread over the full 32 bits. J's are sequential, all
  below 0x220000.
- **Groups**: the factory uses groups 0-1 on level 1, 0-2 on level 2 and 0-3 on level 3.
  J uses only 0, 0-1 and 0-2: J never fills the finest group of a level.

### 5. Content left out or reduced (by design)

- **House numbers**:
  - No 0x04 units.
  - Every street record has the "none" range (32767 ×4); the factory has real ranges on
    65%.
  - Street-list low byte always 0 (factory: 1 on 74%).
  - Never the 0x1002 flag, which the factory sets on 9% of streets and 26% of places.
- **Second street name**: 0x0E name2 is always 0 on J (factory: set on 38%).
- **POIs**:
  - No brand lists in places (0x0C s5); the factory has them in every place unit.
  - POI tiles never carry a brand or running number (factory: 20% of records do).
  - POI records never carry a postcode (factory: 80%).
  - The road-link side is always 0 (factory: 0/1/2).
  - 36% of J places have no POI list (factory: 1.6%).
- **Overview roads**:

  | Level | Factory | J |
  |---|---|---|
  | 1 | 35,199 | 9,759 |
  | 2 | 67,368 | 38,285 |
  | 3 | 124,244 | 162,530 |

  Level-2 tiles without a parent: 25% on J, 2.7% on the factory. Level 3: 24% vs 1.7%.
- **Envelope residuals already known**:
  - Melbourne CBD street cell: 296 tiles vs 267.
  - Level-3 neighbours per tile: 10 vs 8.
  - Level-3 tiles in one cell: 62 vs 61.
  - Place trie unit: 4.6 KB vs 1.5 KB.

### 6. What separates I and J from H

H loaded and showed the menus; I was refused at load. Of everything above, only these
are on I or J but not on H:
- the 3 street tiles bigger than their level-3 parent and the 6 level-3 tiles with 0
  slots (2.2);
- the envelope changes made for I (factory tile shapes, thinning, POI caps; doc 30);
- the slot grids (J only, the fix for H's map error).

Everything else in sections 2-5 was already on H.

## Disc J: is it structured the way the original is?

17 Nov 2026. Original = `1-control-factory.iso`; J = `discJ_victoria_child_slots.iso`.
Read-only; nothing was changed. This replaces the guesses in `FACTORY VS DISC J.md`.
Every rule below was measured on the original first, then tested on J.

**Short answer: no.** Apart from the new roads, POIs and missing house numbers, disc J
breaks 12 rules that the original follows (section 2). On top of that, a set of fields
hasn't been decoded yet (section 4). Until they are, nobody can say J is right.

**Disc K (18 Nov 2026)** is built to every rule below and passes them all at 100%; see
doc 30, "Disc K".

**Update 18 Nov 2026.** Codec 0x0E is now decoded (doc 33), so the rules below were
re-checked on all 6,454 original street tiles, not just the 504 readable ones:
- Three "rules" were artefacts of the small sample and are withdrawn. On the full disc:
  - two nodes share a position in 1,345 places;
  - 153 edge nodes sit on tile corners;
  - junctions reach 8 roads.
  So J's 46 corner nodes and 8-road junction are within the original's range, and
  2.5 now covers zero-length roads only.
- Every other rule held on the town tiles, with the counts given in the table.

### What the original could be checked against

- Levels 1-3, all grids, all place, trie and POI units, and the house-number units
  decode fully: 3,144 zoomed-out tiles, 4,150 house-number units, 573 POI-record units,
  683 POI tiles.
- 742 of the 2,555 street-search units (0x0E) also use the 0x0E codec, so 91,886
  street-trie leaves on the original couldn't be read. Every one that could be read
  follows the rules in section 1.
- At street level, 5,950 of the original's 6,454 tiles use the 0x0E codec, decoded
  since 18 Nov (doc 33).
  - Each one unpacks to plain bytes that round-trip through `carin_tile`.
  - `tools/carin_research/check_plain.py` runs the street rules over them.
- "Original: 100%" means no exception anywhere on the disc.

### 1. Rules J follows exactly (verified)

| Area | Rule (taken from the original) | J |
|---|---|---|
| Disc | Every file except the map database, the volume descriptors, the database size, the root, header units and the background/licence block | identical |
| Grids | Grid units at the original's addresses with the original's headers | identical |
| Grid cells | Sub-cell size = shortest side of the smallest tile listed; each filled sub-cell names the tile covering its centre; no empty sub-cell has a tile over its centre; the tile list is in order of first appearance, scanning x-major; sub-cell ≥ cell / 64 | 100% (8,858 cells) |
| Zoomed-out tiles | Child list is a slot grid, each slot the tile under its centre | 100% (fixed in J) |
| Tile layout | Section order, alignment, s11-s13 empty, tile shapes and alignment | 100% |
| Road order | Roads sorted by group; the group table (first road and first node of each group) correct; inside a group, roads sorted by name | 100% |
| Junction lists | Every road at a node is on the node's list, and the list closes on itself | 100% |
| Links | Parent lists child; edge nodes on the edge, with their neighbour in the neighbour list; neighbour reference point mid-edge | 100% except 2.9 |
| Node flag 0x100 | Marks a dead end | consistent |
| One-way roads | Direction bits follow the original's convention. Tested on divided roads, which must keep left: the original agrees on 74-95% of pairs, J on 77-87% | consistent |
| Search | Every trie: siblings in strictly ascending letter order; every name under a leaf starts with the leaf's letters; leaves sorted; each place's street list in order; place list in order; unit chains valid | 100% (12,042 places, 339,668 streets) |
| POIs | POI tiles sorted x then y; every POI in a list has that list's category | 100% (85,455) |
| "No house numbers" encoding | Street search records with range 32767 ×4 and street-list low byte 0 (on the original the low byte is 1 exactly when the street has numbers: 100% of 1.24 M records) | consistent |

### 2. Rules the original never breaks and J does (verified)

| # | Rule on the original | Original | J |
|---|---|---|---|
| 2.1 | **A road's start node (A) is west of its end node (B)**: x, then y | 1,388,526 of 1,388,526 roads, every level | **451,171 of 949,368 roads (47.5%) run east to west** |
| 2.2 | **Roads at a junction are listed clockwise** (direction to the first shape point) | town street tiles 549,368 of 549,402; levels 1-3 99.4-99.7% (the rest are near-ties) | **street 41%, levels 1-3 47-52%** (it's simply storage order) |
| 2.3 | **A junction's list starts at the first road clockwise from north** | town street tiles 98.8%; levels 1-3 93% | **36-38%** (J always starts at the lowest-numbered road) |
| 2.4 | **Nodes within a group are sorted by x, then y** | 9,598 of 9,598 tiles | **broken in 5,318 street tiles and 2,453 zoomed-out tiles** (J uses the order nodes were met) |
| 2.5 | No road has both ends at one point | 0 on 1.16 M street roads | **679 zero-length roads** |
| 2.6 | *(withdrawn: the original has 153 edge nodes on tile corners)* | | |
| 2.7 | *(withdrawn: the original's junctions reach 8 roads, 6 roads at 49 junctions)* | | |
| 2.8 | Street tile "has house numbers" flag (0x70 = 0x01010000) only when a house-number unit is linked (0x54) | 6,454 of 6,454 tiles | **3,391 tiles flagged with no unit** |
| 2.9 | Every street tile inside its parent, and every zoomed-out tile has ≥ 1 child slot | 6,442 of 6,442 street tiles with a parent | **3 tiles overhang their parent; 6 zoomed-out tiles have 0 slots** (NW Tasmania, Qld/NSW border). **The only fault here not on disc H**, which loaded |
| 2.10 | Every level-3 and level-2 node also exists at street level, and every level-2 node on level 3 | 139,743 of 139,743 | **11,598 level-3 nodes with no street node (mostly tile-edge crossings); 2,139 level-2 nodes with no level-3 node** |
| 2.11 | A level-3 node flagged 0x4000 has a level-2 node at that point | 22,321 of 22,321 | **2,704 flagged with none there** |
| 2.12 | Every tile has at least one neighbour | 6,454 of 6,454 street tiles | **25 with none** (islands, isolated pieces) |
| 2.13 | Empty grid cells: on levels 1-3, a sub-grid of cells no larger than 0x300000; each empty run its own unit | 100% | **one shared 1×1 empty unit per grid, sub-cell = whole cell** |
| 2.14 | Country record: POI-list entries point at real POI indexes | 100% | **4 entries left pointing into a street tile (count zeroed)** |

Measured limits the original never goes past (no rule proven, but J goes well beyond):
- **Shape points on one road:**

  | Level | J | Original |
  |---|---|---|
  | Street | 3,702 | 588 |
  | 3 | 3,588 | 204 |
  | 2 | 1,788 | 324 |
  | 1 | 1,134 | 402 |

- **Route-number text:**
  - The original always uses a shield code, then the number, at most 4 characters
    across all 6,454 street tiles. The codes are `s`, `n`, `ni`, `c`, `a`, `t`, `b`,
    `x`, `m`, `mi`, `ai`, `f` and `i`.
  - J has 13,170 bare numbers, prefixes the original never uses (`mr`, `acw` …) and
    junk such as a 65-character web address.
- **Map label length:** up to 76 characters on J; the original's longest is 43.
- **Coverage:** J has cells at Christmas Island and Norfolk Island, where the original
  has nothing.

2.1 to 2.4 are about how roads join up. They are what a unit uses to give turn
instructions and route through a junction, not just to draw it.

### 3. Header words copied from the wrong tile

Street-tile header words 0x60, 0x64, 0x68 and 0x6C:
- On the original they are specific to each tile. 0x60-0x68 grow with the tile's
  size: median 737 on the smallest tiles, 27,717 on the largest.
- They are **not** lengths of the roads in the tile; I tested that and withdraw the
  earlier guess. Their meaning is still unknown.
- J copies all four, and 0x70, from whatever original tile covers the new tile's
  centre. That tile usually has a different size and different roads. So on J these
  words are wrong for nearly every tile, and 2.8 is one consequence.

### 4. Undecoded: the original uses these and J never does

These might be content (like turn restrictions) or structure. The data alone can't
tell yet.

| Field | What the original shows | J |
|---|---|---|
| Road flags, low 4 bits (values 0x9, 0x6, 0x5, 0x2) | Two 2-bit fields, one per road end. Set at 34-48% of 3- and 4-way junction ends on levels 1-3; the value depends on the junction (B-end value 1 occurs only at 3-way nodes). Looks like junction or turn information | always 0 |
| s10 road-end records | On 14% of road ends on levels 1-3. Tied to one-way roads: for direction 0x10 almost always the A end (11,846 v 1,579), for 0x20 the B end (11,881 v 1,566). Looks like entry/turn restrictions | always empty |
| Road word +24 (0x100 / 0x200 / 0x300 / 0x1000) | 2-3% of roads on levels 1-3 | always 0 |
| Street road attribute bit 0 (0x1081, 0x1001) | 16% of decodable street roads | never |
| Third word of a shape point (256 / 512) | 2-4% of shape points on levels 1-3 | always 0 |
| Node flags 0x200, 0x400, low bits 1-2; edge-node flags without 0x500 | about 1% of nodes | never |
| Node flag 0x500 | Mostly 2-way nodes, but also on 13,366 three-way nodes on levels 1-3, and absent from 3,198 two-way nodes | exactly the 2-way nodes |
| Name flag 0x020A | Named highways (Stuart Highway, Eyre Highway …) | never (uses 0x0208) |
| Last word of street records (s2) | A pointer on 7% | always 4 |
| Road IDs | Spread over 32 bits and shared between roads (e.g. both carriageways) | one per road, sequential |
| Header words 0x60-0x6C | See section 3 | copied |
| Overview groups | The original fills groups 0-1 on level 1, 0-2 on level 2, 0-3 on level 3 | 0 / 0-1 / 0-2 |

### 5. House numbers

They're part of the street data and J has none. The format is now decoded well enough
to build them:

- **0x04 unit, one per street tile.**
  - Header: `(0x10, n)`, then `u32` street-tile address at 0x0C.
  - Then n 10-byte records, one per road in tile order: `left from, right from, left to,
    right to` (0x7FFF = none), and a `scheme` (2 = odd/even sides, 1 = none/mixed, 0
    rare).
  - Checks on the original: all 4,150 units have exactly one record per road, and every
    linked tile points back.
- **Street tile.** 0x54 points at the 0x04 unit and 0x70 = 0x01010000. All 4,149 tiles
  with a 0x04 unit have house numbers in it (plus one with 0x01000000); no tile without
  a unit has the flag. One tile on the original has 0x0.
- **Street search (0x0E).**
  - Each street run carries `(even min, even max, odd min, odd max)` over its roads'
    numbers. That matches 88.5% of 1.24 M runs; the other 11.5% isn't explained yet.
  - The street's list word has low byte 1 when it has numbers (100%).
- **Data source:** G-NAF, the national address file, which is open data. OSM's
  Australian house numbers are thin.

### 6. To be sure

1. Fix 2.1-2.14 and section 3. The rules are fully specified above and each can be
   checked automatically against the original.
2. Decode the fields in section 4, starting with the junction bits and s10, as they sit
   on the routing path.
3. Decode the 0x0E codec, so the 5,950 town street tiles can be checked as well.
   Otherwise every street-level rule rests on 504 mostly rural tiles.
4. Add house numbers from G-NAF; first settle the 11.5% of street runs where the range
   rule doesn't hold.
