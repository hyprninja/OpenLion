# Phase 3e: Signposts, lanes, junction views

Date: 2026-11-04. Code: `openlion/mapfile.py` (`signposts()`, `lanes()`, `parcel_lists()`).

## Summary

| Item | Result |
|---|---|
| Signposts (`attr_list`) | **Decoded**: 8,834 signs in 926 parcels, framing exact everywhere |
| Exit numbers | **Decoded**: 126 signs, e.g. West Gate Fwy W2 Kings Way, W4 CityLink/Airport/Bendigo, W5 Todd Rd |
| Lanes | **Decoded**: 13,349 lanes on 4,663 segments in 281 parcels, all five lists frame exactly |
| Lane connectivity | **Decoded**: 11,294/11,294 in-parcel connections valid; every `lane_to` is within the target's lane count |
| Junction views | Identified: `intersection_view` records name the 742 day/night PNGs in `images/` (pre-drawn pictures) |

## Signposts

```
attr record: u16 rec_len, u16 seg, u16 attrID (always 1 = sign in AUNZ)
  u8  signInfo   signLevel:2, signType:3   (type 1 = route/road, 4 = destination)
  u16 msgLen
  char exitNum[8]                          (NUL-padded, e.g. "W4")
  char msg[msgLen], NUL, pad to even rec_len
msg = element ('/' element)*,  element = "4^" display_text "$5^" phonetic
```

Example (Monash/EastLink, parcel 2209):

| Segment | Type | Text |
|---|---|---|
| 10 | road | `11` / `MORNINGTON PENINSULA FWY` |
| 10 | destination | `SPRINGVALE` |
| 11 | road | `M3` / `EASTLINK` |
| 11 | destination | `DANDENONG` / `MELBOURNE` |

Route shields appear as short elements (`M3`, `11`, `M2`, `1`). The `5^` text is a phonetic transcription the voice uses, e.g. `"mEl|b@n` (MELBOURNE) and `*"sI|ti|%lINk` (CITYLINK). An OSM converter can write the display text and either omit or synthesise the phonetic part. Whether the unit tolerates a missing `5^` part needs a car test.

**Open:** `signLevel` (0/1, split 50/50); which segment the sign is placed on (approach vs ramp); `attrID` values other than 1 (none in AUNZ).

## Lanes

```
lane_offs  (4 B)  u16 seg, u16 byte offset into lane_list
                  lanes on seg = (next distinct offset - offset) / 3   (offsets not sorted)
lane       (3 B)  direction:3 (3 = left->right, 2 = right->left)
                  divider:3, exit_entrance:2, arrows:10, lane_use:1
lane_conn  (7 B)  seg:12, hand:1, turn_direction:5, u16 pair_offset, u16 arm_offset
lane_pair  (1 B)  lane_from:4, lane_to:4   (1-based)
lane_arm   (1 B)  arm1:4, arm2:4           (1-based arm index; 15 = none)
```

- **Lane direction independently confirms the one-way convention** from report 05: direction 3 occurs only on segments drivable left→right, direction 2 only right→left.
- `lane_conn`: from `seg`, arriving at the node chosen by `hand` (as in `turn_res`), lane `lane_from` continues into lane `lane_to` of the road at arm `arm1` (1-based, whereas `turn_res` arms are 0-based). `arm2` (482 uses) probably continues the path one node further.
- In this database every lane has divider = 7, exit_entrance = 3, arrows = 0x3FF, lane_use = 0, and every connection has turn_direction = 31. Those are "unspecified" defaults. **The lane data here is lane counts plus lane-to-lane connectivity only, with no arrows.** That's a good fit for OSM `lanes` / `turn:lanes`, which carry more than the factory data.
- Coverage: Melbourne 95 parcels, Sydney 63, Brisbane/Gold Coast 71, Perth 24, Auckland 20, Hobart 4.

**Open:** lane numbering side (is lane 1 the left or right lane?); the exact meaning of `arm2`.

## Junction views

`intersection_view_*` lists (790 views, 819 arms in 367 parcels) reference image names such as `1415939309`, which match `images/day/*.PNG` and `images/night/*.PNG`. Example: a Sydney view, "Macquarie St Exit / City East" and route 1 "East Suburbs / Airport", with the lane arrow. These are licensed Navteq/Holden artwork. **An OSM build must not ship them.** Either leave the lists empty or generate original diagrams. The record layout is not yet decoded; it isn't needed if the lists are left empty.

## Notes for the converter

- Signposts ← OSM `destination`, `destination:ref`, `junction:ref` (exit numbers) on motorway links.
- Lanes ← OSM `lanes`, `lanes:forward/backward`, `turn:lanes`. Lane connectivity has to be derived. Arrow fields could be filled from `turn:lanes` if the unit renders them; the factory data never exercises that, so it needs a car test.
- Speed limits ← OSM `maxspeed` (see memory note: fill as completely as possible).
