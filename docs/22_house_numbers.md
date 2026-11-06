# Phase 13: House numbers

Date: 2026-11-06. Modules: `openlion/osm_addr.py` (new); `mapwrite.py`, `osm_names.py`, `pipeline.py`, `tests/card_check.py`, `tests/test_house_numbers.py` (new).

Feedback from a user: the OSM card wasn't "laying out street numbers". Up to now, every OSM segment linked to its street with a plain `abs_addr` link, and every road record had a house-number range of 0 (reports 13, 16 and 19 listed this as not done). Address search could find a street but not a number on it. This phase decodes how the factory card stores house numbers and fills them from OSM `addr:housenumber`.

## The format

Report 04 named the fields but not their bits:

```
short_addr 11 bytes: u16 seg, u8 nameinfo, u32 roadID, u8 addrinfo, u8 delta, u16 base
long_addr  13 bytes: u16 seg, u8 nameinfo, u32 roadID, u8 addrinfo, u8 msb_base, u16 lsw_base, u16 delta
```

`au.mod` names `addrinfo`, `delta` and `base` but nothing inside `addrinfo`. I worked them out from the factory card (2,393,075 numbered links, MY12), starting with Swanston St and Collins St in Melbourne, where the house numbers are known:

| Field | Meaning |
|---|---|
| `addrinfo` bits 0–3 | side and parity. Right side: 3 even, 7 odd, 11 mixed. Left side: 12 even, 13 odd, 14 mixed |
| `addrinfo` bits 6–7 | the top bits of `delta` |
| `delta` | signed: 10 bits in `short_addr` (bits 6–7 + the byte), 18 bits in `long_addr` (bits 6–7 + the u16) |
| `base` | the house number at the segment's **left end** (hands bit 0, the lower node index in the factory); `base + delta` is the number at its right end |

"Right" and "left" are as seen travelling from the left end to the right end. In the factory the left end is always the lower node index (74,127 of 74,127 sampled segments). The OSM writer makes `seg_a` the left end, so the same rule applies there.

Evidence:

- **Parity:** across all 2.39M factory links, codes 3 and 12 have an even base on 100.00% of links (552,172 and 504,974), and codes 7 and 13 have an odd base on 100% (510,350 and 554,651). Codes 11 and 14 have both parities.
- **Sides:** Swanston St has even numbers on the east side (Melbourne Town Hall, 90–130) and odd on the west. Collins St has odd numbers on the south side (Rialto, 525). On both, and in both travel directions, the even/odd sides land on the codes in the table.
- **Signed delta:** the minimum and maximum over each street's links, with a 10/18-bit signed delta, equal the road record's `minNumber`/`maxNumber` for 427,053 of 469,165 streets (91%). Another 37,215 (8%) differ only because the factory rounds an odd maximum up by one. A plain unsigned byte fits only 41%, and an 8-bit signed delta 88%. No number decodes as negative.
- **Round trip:** re-encoding the 2,283,981 links with the six codes above gives the factory bytes for all but 415. Of those, 376 carry a rare extra bit (bit 4, ~0.02%), and 39 are `long_addr` links whose values would fit in `short_addr`.
- **Other codes** (1, 4 and a few others, 108,679 links, 4.5%) aren't needed for OSM data and stay unexplained.

How a numbered segment is linked:

- One numbered link per side that has numbers (both sides on 26,531 of 47,308 sampled numbered segments), and **no** `abs_addr` link to the same road.
- Lists sorted by segment, as the tiler already writes them.
- `nameinfo` is mostly 0 (82% of numbered links).

## From OSM

`osm_addr.read()` collects every node and area (buildings, at their centre) with both `addr:housenumber` and `addr:street`. The street name is normalised like the road names. `12`, `12A`, `3/45` (unit 3 of number 45), `1-5` and `12;14` are read as 12, 12, 45, 1 and 5, and 12 and 14. It's a new cached read (`addr1`), done in its own worker process like the others.

`osm_addr.assign()` runs inside `osm_names.build`:

1. **Stray numbers.** A number more than 3 × the spread + 6 away from the median of its 6 nearest neighbours on the same street (within ~150 m) is dropped. This removes tagging slips like a lone "7" among 23–29 on Lonsdale St, Braddon (0.9% of ACT numbers).
2. **Matching.** Each address goes to the nearest segment of a street with the same name within ~280 m. It takes the side of the road it lies on and its position along the segment (0 at the left end, 1 at the right).
3. **Ranges.** For each segment side:
   - **Parity:** at least 80% one parity makes the side even or odd, and the other-parity numbers are dropped. Anything else is mixed.
   - **Range:** a straight-line fit of number against position gives the direction. The range covers every number seen, and is extended towards the segment's ends by at most the span seen, so gaps between numbered segments shrink.
   - **Bounds:** ends are kept to the side's parity and are at least 1 (2 on an even side).
4. **Road records** get the minimum and maximum over their links (5.2.1: `minNumber`/`maxNumber`; 5.3: `hasNumbers` = 1).

`mapwrite.encode_range` writes `short_addr` when the base fits in 16 bits and the delta in −512..511, and `long_addr` otherwise.

`card_check` now decodes every numbered link. A negative number counts as a problem. Codes outside the six above and ranges past the street's maximum are reported as info, since the factory card has both. The factory MY12 card shows 2,393,075 numbered links, 0 negative.

## Results

**ACT** (`--bbox 148.75 -35.95 149.45 -35.10`, MY12 and MY17):

| | |
|---|---|
| OSM house numbers | 33,537 on 2,113 street names |
| matched to a street | 32,886 (98%); 302 dropped as stray, 264 with no street of that name nearby, 85 too far from it |
| numbered segment sides | 4,054 even, 4,011 odd, 76 mixed |
| numbered segments / streets | 5,842 / 2,186 |
| card_check | 0 problems on MY12 (5.2.1) and MY17 (5.3); 8,770 numbered links |

Lonsdale St, Braddon: odd 3→29 along one carriageway and even 4→40 along the other, rising northwards, as on the street.

**Australia**, full extract (MY12):

| | |
|---|---|
| OSM house numbers | 4,453,882 on 111,940 street names |
| matched to a street | 4,272,420 (96%); 56,241 dropped as stray, 97,796 with no street of that name nearby, 26,412 too far from it |
| numbered segment sides | 399,597 even, 401,241 odd, 52,888 mixed |
| numbered segments / streets | 560,027 of 3.2M / 200,009 of 608,240 |
| card_check | 0 problems; 924,435 numbered links, lookups in every capital |
| time | names stage 315 s instead of 113 s (+3.4 min on the 44-thread PC), read `addr1` 167 s, run in parallel with the other reads |
| memory | names step 3.6 GB instead of 3.4 GB; the address read's worker peaks at 5.5 GB with node locations in memory (the low plan keeps them on disk, as for the POI read) |

`assign()` works one street name at a time, so only that street's segment grid, neighbour grid and observations are in memory. A first version with Australia-wide grids took the names step to 4.5 GB. Candidate segments whose bounding box is already farther away than the best match are skipped (485 s → 315 s). Between those two versions only ties changed: an address exactly as close to two consecutive segments of its street (at their shared node) can go to either, so `auz.mp0` differs while `auz.rd`, `rl`, `rt` and `str` and every count are identical. Repeated builds are byte-identical.

## Limits

- **Coverage follows OSM.** OSM only has house numbers where mappers have added them. In the ACT that's 33.5k numbers on 2,186 of ~9,800 streets. Streets without OSM numbers still find the street, as before. G-NAF, the government's open address file, has every address in Australia and could fill the rest. It would work the same way as the government speed zones (report 21). *Done in report 23 (`--addresses`).*
- `addr:interpolation` ways (numbers spread along a line between two tagged ends) aren't read yet.
- Addresses with `addr:place` instead of `addr:street` (some rural properties) have no street to attach to.
- The car's search behaviour with these ranges hasn't been seen yet. On the factory card, a number between two segments' ranges probably goes to the nearer one, but this isn't confirmed.
