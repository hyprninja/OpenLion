# Phase 11: Building on an 8 GB PC, and using a big one

Date: 2026-11-05. Modules: `openlion/memory.py`, `openlion/resources.py` (new); `pipeline.py`, `osm_import.py`, `osm_fea.py`, `osm_names.py`, `graph.py`, `layers.py`, `make_card.py`; app: `BuildMapsStep.cs`.

The first outside report came from a PC with 8 GB of RAM. Version 1.0 needed 16.6 GB for Australia alone, and 21.9 GB for Australia + New Zealand. This phase makes the same build fit in about 4 GB, and makes it four times faster on a big PC. The output is unchanged: every table and the POI database are byte-identical to 1.0's.

## Where the memory went (1.0, Australia + New Zealand)

`memory.py` measures free RAM and a process's committed memory through the Windows API (no extra packages). The pipeline now logs both on every line and keeps the peak of each step.

| Step | Peak | Why |
|---|---|---|
| read roads | 8.3 GB | 2.1M ways, every point an `(id, x, y)` tuple (~245 B per point), every tag kept |
| build graph | 12.3 GB | a `Counter` over all 25M node references; the raw ways stay alive alongside the graph |
| boundaries | 16.3 GB | rings as tuple lists; everything before still alive |
| background read | 18.4 GB | 523k areas and 881k lines as tuple lists |
| POI read | 21.9 GB | osmium's in-memory node index on top of all of the above |

It's 98 minutes in total, all in one process. Python keeps freed memory reserved for its own reuse, so deleting objects mid-run barely lowers the figure.

## What changed

**Compact data.** Points are stored as flat arrays: `WayPoints` (node ids + x/y) for ways and `Shape` (x/y) for segment shapes, boundary rings and background features. Both behave like the lists of tuples they replace, so the code that reads them didn't change. Ways keep only the 8 tags the build uses. Values that repeat millions of times are shared, not copied: node info dicts, speed limits, U-turn masks, name/ref labels, and one road link per road instead of one per segment. Junctions are counted in 8 hash buckets instead of one 25M-entry `Counter`.

| | 1.0 | now |
|---|---|---|
| road read (AU) | 245 B / point, 5.2 GB | ~1.8 GB |
| graph | 1.66 KB / segment (shapes 1.1 KB) | 0.5 KB / segment |

**Every heavy step in its own process.** Each OSM read runs in a one-shot worker that writes its cache file and exits. The rest of the build runs in three stage processes: roads (graph, names and search, routing layers), background map, and POIs. They talk to the main process through a queue (progress, the POI bounding box, memory peaks). When a process ends, Windows gets all of its memory back, so the build's peak is the largest single step, not the sum of everything so far.

**Background map in strips.** Each zoom level is cut into column strips. A strip only simplifies the features that reach it, kept in their original order, so its cells come out exactly as in a one-pass build. The land/sea classification stays one recursive pass per level, because splitting it would change which cells count as "all land". The roads read also saves the overview roads separately (`overview2_*.pickle`), so the background stage doesn't load every road.

**Faster point-in-polygon.** Big rings (≥ 64 points) get a y-band index of their edges, built on first use. The even-odd test only counts edges that straddle the point's y-value, and its parity doesn't depend on order, so the answer is identical. On 20,000 random points it matched the plain test exactly. Locality and state lookups get about 19× faster: the POI stage drops from 24 minutes to 41 seconds, and the names step from 11 minutes to about 2.

## Plans (`resources.py`)

The plan is chosen from free RAM and CPU count at the start. Override it with `--memory low|high` (pipeline and make_card) or `OPENLION_MEMORY`, and set worker counts with `OPENLION_WORKERS`.

| | Low plan (small PCs) | High plan (≥ ~14 GB free) |
|---|---|---|
| OSM reads | one at a time; osmium node locations in a file on disk (`sparse_file_array`, ≤ 2.2 GB, deleted after each read) | up to free RAM ÷ 6 GB at once, locations in memory |
| stages | roads, then background, then POIs | background runs alongside roads; POIs start as soon as the graph's extent is known |
| background map | 8 strips per level, in the stage process | strips sent to up to (free RAM − 7.5 GB) ÷ 0.6 GB worker processes, each with only its own features, a few in flight per worker |

## Results (Australia + New Zealand, 2026-11-04/03 extracts)

| | 1.0 | Low plan | High plan (44 threads, 64 GB) |
|---|---|---|---|
| peak memory | 21.9 GB (one process) | 3.6 GB (roads stage); reads ≤ 2.8 GB; background 4.6 GB before the last two fixes, ~3 GB after; POIs 0.8 GB | reads 5.9 GB each; stages 3.8 / 2.9 / 0.9 GB; free RAM never below 33.7 GB |
| time | 98 min | ~85 min measured before the point-in-polygon speed-up; about 1¼ h expected now | **24.5 min** (reads 7.4 min) |
| output | | identical | identical |

"Identical" means all 17 tables and `aunz_sql.db3` compared byte for byte with the 1.0 build of the same extracts. The Wellington test area was also checked after every change in both plans.

The low-plan figures come from a full run on this 64 GB PC, where each process's committed memory was logged. Two fixes landed after it: per-level coastline cells and the overview-road cache. The high run shows their effect (the background stage's main process dropped from 4.6 GB to 2.9 GB), but the low plan hasn't been rerun since. A run inside a Windows job object capped at 5 GB, standing in for an 8 GB PC, was started and stopped during its reads for time; none of its steps had come near the cap.

## Not done

- The low plan hasn't been run end to end on a real 8 GB PC or under a memory cap. Its expected peak (about 3.6 GB) comes from per-process measurements; an 8 GB laptop's disk and CPU will also be slower than this PC's.
- The background map's land/sea pass is still one process per level, and is now the slowest part of the high plan.
