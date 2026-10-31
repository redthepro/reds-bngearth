# Reviewed repository baseline

Repository: [redthepro/reds-bngearth](https://github.com/redthepro/reds-bngearth)

Reviewed revision: [aae4d6126d65a07315d0b28cc4a81dd68d86280a](https://github.com/redthepro/reds-bngearth/tree/aae4d6126d65a07315d0b28cc4a81dd68d86280a)

This is a read-only code review, not a benchmark. The bootstrap pins this revision so the recommendations have a reproducible starting point. Reconcile later upstream changes before adopting them.

## Existing strengths to retain

- Python companion, Rust `earthfast` kernels, and Lua engine integration.
- Geographic cells, feature ownership, raw/memory-mapped caches, stage versions and digests.
- Global Overture/OSM enrichment, WorldCover, canopy height, terrain fallbacks, imagery selection, bathymetry, and weather.
- Request/anchor IDs, prepare/commit behavior, resource release acknowledgments, rebasing, frame governor, and backpressure.
- A substantial existing test suite, including world consistency and protocol agreement tests.

## Concrete review targets

| Path | Why inspect it |
|---|---|
| `server/earthd.py` | Large orchestration surface, pool reservations, heightmap preparation, anchor changes |
| `server/blockworker.py` | Terrain preparation overlaps the in-process path; bridge/nav fallback deserves review |
| `server/plancache.py` | Per-run road-plan cache intentionally protects against persistent incomplete DEM decisions |
| `server/streaming.py`, `protocol.py`, `budget.py` | Existing scheduling, transfer and lifecycle contracts |
| `server/terraincache.py`, `mapdata.py`, `netfetch.py` | Existing reuse, source access and network controls |
| `server/roads.py`, `grading.py`, `navgraph.py` | Shared road support/visual/navigation decisions |
| `server/osmtags.py` | Extend actual Layercake schema, preserving OSM lineage |
| `server/dem.py`, `maprasters.py`, `vegetation.py` | Global provider/normalization extension points |
| `mod/beamng_earth/lua/ge/extensions/beamngEarth/core.lua` | Engine object installation, state and collision transactions |
| Adjacent `governor.lua`, `streaming.lua`, `rebase.lua` | Existing frame controls and coordinate transformation |
| `server/tests/test_world_consistency.py`, `test_protocol_agreement.py` | Start with established invariants |

Inspect symbols in the pinned checkout; avoid depending on line numbers as edits progress.

## Specific hypotheses to verify

- One shared terrain preparation implementation can reduce divergence between `earthd` and `blockworker`.
- Persistent compiled cells can avoid repeated CPU work across restarts once completeness and dependency invalidation are sound.
- A failed bridge profile should not silently leave a navigation route on the ground below the bridge.
- Cooperative stage cancellation can reduce work that survives an anchor change.
- Large coordinator files can be decomposed around stable contracts without rewriting every subsystem.
- Preserve reserved worker capacity: the current multiple-pool layout exists partly to avoid starvation.

The architecture document includes engine measurements from an older version while newer repository material targets later BeamNG releases. Treat them as hypotheses to re-probe on the actual version. This package does not assert a specific installed version or measured performance on this PC.

Existing research already mentions national building/lidar sources. The priority here is to generalize global baselines and provider interfaces, not relabel old planned sources as newly discovered.
