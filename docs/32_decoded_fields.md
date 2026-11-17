# 32: The unknown tile and search fields, decoded

17 Nov 2026. Everything here was derived from the original disc's own data. Where a
real-world meaning is claimed, it was checked against current OpenStreetMap for
Melbourne. "100%" means no exception in everything decodable on the original: all of
levels 1-3, and the 504 decodable street tiles.

CARINET on the disc is not reader code: it's the TMC traffic-message table (Dutch,
German, … event texts and location codes), so the head unit's software isn't available.
Nothing here comes from it.

## Decoded exactly

### Node flags (s5, s6), bits 14-15: the coarsest level the node reaches

| Bits | Meaning |
|---|---|
| 0x0000 | the node also exists on level 1 |
| 0x4000 | its coarsest level is level 2 |
| 0x8000 | its coarsest level is level 3 |
| 0xC000 | street level only |

- The same code is used on every level: level-1 nodes are always 0; level-2 nodes 0 or
  0x4000; level-3 nodes 0, 0x4000 or 0x8000. On the original it holds on all 3,144
  zoomed-out tiles, and on 1,004,337 of 1,004,392 street nodes once the town tiles were
  decoded (doc 33).
- **Disc J writes "how many levels up" instead, which reverses the code.** Almost every
  node is wrong: for example, 405,651 street-only nodes carry 0 ("on level 1").

### Node flags, low byte: the junction type at street level

| Value | Meaning |
|---|---|
| 0x100 | dead end (1 road at street level) |
| 0x500 | through node (2 roads at street level) |
| 0x000 | junction (3+ roads at street level) |

- On the zoomed-out levels the value describes the street-level node, not the level's own
  road count. A zoomed-out node with 2 roads that is a junction at street level gets 0.
- On the original this holds for about 97% of nodes. The rest are 0x200 (≈1%, not
  decoded) and a few through nodes at 3-way junctions.
- **On disc J's zoomed-out levels, 12,387 nodes are wrong.** J counts the level's own
  roads, so a level-1 node is marked a dead end where the street level has a junction.
- Edge-node flag 0x2000 marks a node on a tile edge. 0x3000 (no 0x500) is simply an edge
  node sitting on a junction.

### Road fields +18, +20, +22 (and +30 on street level) are section pointers, not names

- They point at this road's records in s10, s11, s12 and s13, the same way +4 points at
  its shape points. The next road's pointer marks where this road's records end, and the
  closing record carries the end of each section. 100%.
- J writes them correctly; its s10-s13 are empty.

### s10: forbidden turns

- Each record is `(u32 tile, u16 target road, u16 end)` and lies in the "from" road's s10
  range. It forbids going from this road into the target road.
- On the original, 14% of roads on levels 1-3 have one. Of those that share a node:
  - 60% forbid a U-turn, 35% a right turn (across traffic), 5% a left turn.
  - In 92% of cases traffic on the "from" road can reach the shared node, and traffic
    can leave it along the target road.
- 7,605 targets don't share a node; 7,024 of those connect through one road between
  them, e.g. no U-turn through a median gap.
- Content, not structure. OSM turn-restriction relations would fill it.

### Shape-point third word: grade separation

- 0x200: the road passes **over** another here. 77% sit on an OSM bridge, against 2% of
  unmarked points.
- 0x100: it passes **under** here (crossed by a bridge, or in a tunnel). 64% match.
- Also used at street level (now readable): 2,014 points with 0x100 and 127 with 0x200
  in the town tiles.

### Road word +24, bits 8-9: slip-road type (copies the road form)

| +24 | Forms | Slip road |
|---|---|---|
| 0x100 | 1, 8 | entry |
| 0x200 | 2, 9 | exit |
| 0x300 | 3, A | connector |

100% on the original. Bits 0x400 and 0x1000 are rare (≈0.1%) and not decoded.

### Road form (class low nibble), checked against how the roads connect

| Form | Meaning |
|---|---|
| 0 | motorway carriageway |
| 4 | divided-road carriageway |
| 1 | entry ramp onto a motorway (1,172 of 1,738 go from a road onto a motorway) |
| 2 | exit ramp off a motorway |
| 3 | motorway-to-motorway connector |
| 8 | entry slip onto a divided road |
| 9 | exit slip off a divided road |
| A | divided-road-to-divided-road connector |
| B, C | ordinary roads (C mostly urban) |

- **Disc J gives every OSM `*_link` road form 1 (entry ramp)**, exits included, and
  leaves +24 at 0. It never uses forms 2, 3, 8, 9 or A.

### One-way direction (class bits 4-5)

- 0x10 = traffic flows A to B; 0x20 = B to A. Confirmed with divided roads: Australia
  drives on the left, so the eastbound carriageway is the northern one.
- J uses the same convention.

### Street-road attribute word (+28)

| Bit | Meaning | Disc J |
|---|---|---|
| 0x80 | urban; equals class bit 15 (100%) | correct |
| 0x01 | this road has house numbers in its 0x04 record (100%: 303 of 303 set, 0 of 564 unset) | 0, which is right while there are no house numbers |

### Street-level name entries (s14 flags)

| Flag | Meaning |
|---|---|
| 0x200 | area name (country, region, suburb, state) |
| 0x208 | street name, listed first |
| 0x20A | street name, listed second |
| 0x010 | route number, listed first |
| 0x011 | route number, listed second |

- Each street record (s2) owns the entries from its own entry pointer up to the next
  record's. The first entry has the "first" flag and matches the record's name.
- s2's last word is either 4, or (in 14 Melbourne CBD tiles) a pointer to the suburb's
  area entry. Both forms are on the original.
- On the original, numbered highways list the route first (`s123`, then the name) on 472
  of the 561 streets that carry both. Disc J always lists the name first. That's allowed by the format, but
  the unit will probably label J's highways by name rather than by shield.

### Street search (0x0E) name records

| Flag | Meaning |
|---|---|
| 0x0002 | street |
| 0x1000 added | a reordered keyword copy, so "lady denman drive" is also listed as "denman drive lady" (68,000 records) |
| 0x0000, 0x0200, 0x0100 | route numbers listed as streets ("c108", "m3", "ni23", "t2") |
| 0x0202, 0x0102 | streets that are also numbered routes |

- The second name is the suburb, used when a list covers a whole city.
- The street list word's low byte is 1 when the street has house numbers (100%).
- **Disc J has no reordered keyword copies and no route-number entries.** This is search
  content, not structure.

### Places (0x0C)

- 0x1002 marks reordered place names too ("adelaide north").
- The original's second name is the city or region a suburb belongs to
  (abbotsford → melbourne). J's is the state (abbey → western australia).

### Levels and groups

- A road keeps its group on every level it appears on (95%+). Each finer level adds one
  group of less important roads:

  | Level | Original | J |
  |---|---|---|
  | 1 | groups 0-1 | group 0 |
  | 2 | groups 0-2 | groups 0-1 |
  | 3 | groups 0-3 | groups 0-2 |
  | Street | groups 0-6 (6 = class 0x33C, 3 m/s) | groups 0-4 |

- **J leaves out the least important group the original shows on each zoomed-out level**
  (on level 3 that's 35% of the original's roads, mostly urban collectors).

### Road IDs

- On the original, a road ID is usually unique within a level and is kept across levels.
  Both carriageways of a divided road often share one, as do pieces across tile edges.
  They look like the map supplier's own link IDs.
- J's IDs are its own, unique per road and kept across levels. Nothing found that this
  breaks.

## Partly decoded

### Street-tile header words 0x60, 0x64, 0x68

- Each tracks the distance from the tile to the nearest node of one zoomed-out level:
  0x60 to level 1, 0x64 to level 2, 0x68 to level 3 (rank correlation 0.83-0.84, best on
  the diagonal).
- When one node is the nearest for all three levels, the three values are usually
  identical.
- The exact formula isn't recovered: probably a road-network distance in metres from the
  tile's nodes. Working it out needs the town street tiles (0x0E codec).
- J copies them from an unrelated original tile, so J's values describe a different
  place.

### Road flags, low 4 bits (values 0x2, 0x5, 0x6, 0x9)

- Two 2-bit fields, one per road end.
- On the original:
  - They are never set on a road that is faster than every other road at that junction.
  - They are mostly at junctions with one-way or divided roads.
  - A road joining a carriageway on its left always gets A=1 / B=2.
  - 43% of marked junctions have OSM traffic signals, against 22% of unmarked ones.
- The best rule from the junction's own geometry, directions and road forms gets 90%, so
  they depend on something the map doesn't hold (probably give-way or junction-control
  data from the supplier).
- 0 is valid: the original has 0 on 62% of road ends.

### Not decoded

- 0x6C (4, 8, 10-14 or 359) in the street header.
- Node flag 0x200 (≈1%; not roundabouts or signals).
- Road +24 bits 0x400 / 0x1000.
- Attribute bits 0x30 / 0xA00 (4 roads).

## New faults on disc J found by this decoding

These add to the 14 in `DISC J STRUCTURE CHECK.md`:
1. **Node level bits reversed** on almost every node.
2. **Junction type on zoomed-out levels** wrong at 12,387 nodes.
3. **Slip roads:** all marked as entry ramps, with +24 left at 0.
4. **One group missing on each zoomed-out level.**

Content J leaves out that the original has (the unit can't use what isn't there):
- forbidden turns (s10)
- bridge and tunnel marks
- route-first labels for highways
- reordered keyword search entries
- route numbers in search
- house numbers (format in `DISC J STRUCTURE CHECK.md`)
