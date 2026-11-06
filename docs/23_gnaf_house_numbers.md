# Phase 14: House numbers from G-NAF

Date: 2026-11-06. Modules: `openlion/gov_addr.py` (new); `osm_addr.py`, `osm_names.py`, `pipeline.py`, `make_card.py`, `tests/test_gov_addr.py` (new), `tests/test_house_numbers.py`.

Report 22 added house numbers from OSM. OSM has them on only a third of Australia's streets (200,009 of 608,240), and in the ACT on 2,186 of 9,802. G-NAF, the government's national address file, lists every address in Australia. This phase fills the gaps from G-NAF while keeping OSM's numbers where both have one. It works like the Victorian speed zones (report 21): a government open-data file passed to the build, OSM first.

## The data

**Geoscape Geocoded National Address File (G-NAF)**, August 2026 release, from data.gov.au (dataset `19432f89-dc3a-4ef3-b943-5326ef1dbecc`). It is updated quarterly.

- **File:** `g-naf_aug26_allstates_gda2020_psv_110.zip`, 1,854,931,188 bytes (8.8 GB unpacked, 212 files). The GDA2020 edition is used because it matches the coordinates OSM uses to within centimetres; GDA94 is ~1.8 m off.
- **Licence:** the Open G-NAF End User Licence Agreement, which is CC BY 4.0 plus one rule: G-NAF must not be used to compile addresses for sending mail unless each one is checked against another source. A navigation card doesn't send mail.
- **Attribution:** in the README, in the wording data.gov.au asks for, with a note that OpenLion modifies the data (numbers are matched to OSM streets and stored as ranges).
- **Not used:** "G-NAF Core", the simplified two-table edition, needs a sign-up form at Geoscape. The full edition is a plain download.

`gov_addr.read()` reads the zip in place, one state at a time, so it never needs the 8.8 GB on disk:

| Table | Used for |
|---|---|
| `<STATE>_STREET_LOCALITY` | street name + full street type (`STREET`) + suffix code (`N` → `NORTH` via `Authority_Code_STREET_SUFFIX_AUT`), normalised like the OSM road names |
| `<STATE>_ADDRESS_DETAIL` | `NUMBER_FIRST`, `NUMBER_LAST`, `STREET_LOCALITY_PID`; only current (`DATE_RETIRED` empty), principal (`ALIAS_PRINCIPAL` = P) addresses |
| `<STATE>_ADDRESS_DEFAULT_GEOCODE` | the address point; `STL` / `LOC` / `UNK` geocodes are left out, since they only mark the street or locality |

Units and flats at one street number (a block of 40 units at "12") become one point at their mean. A range like 5–9 gives both ends. The read keeps only one state's tables in memory at a time.

## OSM first

`gov_addr.combine()` starts from the OSM addresses and adds each G-NAF number unless OSM already has **the same number on a street of the same name within ~330 m**. Everything after that is report 22's code unchanged: stray-number filter, matching to the nearest same-named segment, ranges per segment side.

The extra volume turned up two things report 22's OSM data never hit:

- **Numbers above 65,535.** G-NAF has a few, and the road record stores min/max as u16, so the cap is now 65,535 for both sources.
- **Ranges far too long for their segment.** One segment side came out as 3 → 132,647, which `long_addr` can't hold. A side's numbers now span at most what its segment could carry: 2 per metre of segment, at least 60. When numbers fall outside that, the biggest group that fits is kept. A range that still can't be stored leaves that side without numbers, and the segment keeps its plain link, instead of failing the build.

## Use

```
python -m openlion.make_card australia-latest.osm.pbf <factory navigation/> <work> <out navigation/> \
       --addresses g-naf_aug26_allstates_gda2020_psv_110.zip
```

`pipeline.py` takes the same `--addresses`. The G-NAF read runs as one more cached read (`gnaf1_AU.pickle`) alongside the OSM reads. `make_card` puts the G-NAF file's size, mtime and fingerprint into the cache key, so a new quarterly release is never mixed with an old read. G-NAF is ignored for a card without Australia.

## Results

**ACT** (`--bbox 148.75 -35.95 149.45 -35.10`, MY12):

| | OSM only (report 22) | OSM + G-NAF |
|---|---|---|
| house numbers | 33,537 | 168,003 from G-NAF in the box (incl. Queanbeyan, NSW): 139,031 added, 28,972 already in OSM |
| matched to a street | 32,886 | 166,662 |
| numbered segments | 5,842 | 25,314 |
| numbered streets | 2,186 of 9,802 | 7,960 of 9,802 |
| card_check | 0 problems | 0 problems |

G-NAF's Lonsdale St, Braddon is clean: even 2→36 and odd 3→35, rising northwards, with none of the stray numbers OSM had there.

**Australia**, full extract (MY12):

| | OSM only (report 22) | OSM + G-NAF |
|---|---|---|
| house numbers | 4,453,882 | G-NAF 10,125,621 (one per street number, units merged): 6,418,946 added, 3,706,675 already in OSM |
| matched to a street | 4,272,420 | 10,120,620; 112,328 dropped as stray, 426,358 with no street of that name nearby, 213,013 too far from it, 509 above 65,535 |
| numbered segment sides | 853,726 | 2,064,106 (976,581 even, 981,351 odd, 106,174 mixed; 973 trimmed to the segment's length) |
| numbered segments | 560,027 | 1,313,745 of 3.2M |
| numbered streets | 200,009 of 608,240 | 464,194 of 608,240 |
| numbered links on the card | 924,435 | 2,232,132 |
| card_check | 0 problems | 0 problems, lookups in every capital |

Time and memory (44-thread PC, OSM reads cached):

| | OSM only | OSM + G-NAF |
|---|---|---|
| G-NAF read | – | 238 s, worker peak 1.8 GB, alongside the OSM reads |
| merge (OSM first) | – | 31 s, +0.4 GB |
| names stage | 315 s | 575 s |
| roads stage peak | 3.6 GB | 3.8 GB |
| card | 575 MB | 580 MB |

The merge first used tuples and sets keyed by street name (roads stage 4.0 GB). Packing each OSM point into one integer keyed by street id and number brought it to 3.2 GB with the same result. Two runs give byte-identical tables.

## In the app

A **House numbers** step sits between "Get OSM data" and "Build maps" (`HouseNumbersStep.cs`, `OpenLion.Core/Addresses/Gnaf.cs`):

- A tick box, **on by default** and remembered (`state.json` `HouseNumbers`). It only shows when Australia is on the card.
- It asks data.gov.au's catalogue for the newest GDA2020 zip, and only downloads from `data.gov.au` over HTTPS.
- The download resumes after Cancel or a closed app. The file must match the size the catalogue lists and contain the states' `ADDRESS_DETAIL` tables.
- **Use a file I already have…** copies a G-NAF zip that's already on the PC, after the same contents check.
- One release is kept in `%LOCALAPPDATA%\OpenLion\gnaf\`. A newer quarterly release replaces it.
- The build gets `--addresses <zip>`. The release's file name is part of the build's key, so a card is rebuilt when house numbers are added, removed or updated.
- The step shows the G-NAF credit.

Tested in the app (developer build, separate data folder): the catalogue check found the August 2026 release, the download was paused and resumed and finished at the exact catalogue size, the Build maps step said "with house numbers from G-NAF August 2026", and its ACT test build (`OPENLION_BBOX`) started with `--addresses` and read G-NAF. That in-app build was cancelled during the OSM reads, so the full card build through the app wasn't run to the end. The same `make_card --addresses` command passes from the command line (above). While the OSM reads run (about 3 minutes), the screen stays on stage 1, "Reading roads", with no visible progress, which looks stuck. That was already true before G-NAF. `GnafTests` cover the catalogue parsing, host check, import, one-release-at-a-time and the build key.

## Limits

- **Street names:** G-NAF streets match OSM roads by normalised name only. Where the two spell a name differently (`ST GEORGES ROAD` vs `SAINT GEORGES ROAD`), the G-NAF numbers find no street.
- **In-car behaviour** of search with house numbers is still untested (report 22).
