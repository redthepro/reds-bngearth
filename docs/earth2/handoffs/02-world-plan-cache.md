# 02 — Shared preparation and persistent compiled terrain

2026-10-26 (America/New_York). Phase 02 standalone implementation, staged verification, measurements and independent final review complete. No phase 03 scheduling/provider/catalog work or game operation.

## Starting state and changes

Workspace: `C:\Users\Culle\OneDrive\Desktop\beamng-earth-2`; isolated `source` branch `earth2/development`, HEAD unchanged at `aae4d6126d65a07315d0b28cc4a81dd68d86280a` (`v1.8.2`). Phase 01's four uncommitted source files were preserved. No reset, upstream adoption, commit/push, original-checkout operation, mod installation, game/Blender control, secrets or geographic downloads.

Python: `%LOCALAPPDATA%\beamng-earth-2\venvs\py312\Scripts\python.exe`, 3.12.14; native earthfast 69. Same observed phase 01 packages and Windows 11/Ryzen 7 7700X (8 cores/16 threads), RAM 50,587,820,032 bytes. GPU/VRAM/game performance not measured. All generated state/raw reports remain under `%LOCALAPPDATA%\beamng-earth-2`.

| Files | Result |
|---|---|
| `server/terrainprep.py`, narrow `heightmap.py` changes | Shared sample/shaping/grading/arch/surface/tunnel/encoder preparation used by both `Server._make_heightmap_here_` and `blockworker.build`. Quantization/PNG/TER byte helpers preserve existing outputs. |
| `server/worldplan.py` | Explicit canonical-fact versus anchored-preparation contract, request-local bounded fact capture, content/algorithm identities, immutable generation store and common settings receipts. |
| `server/compiledcache.py` | Persistent compiled payload store: atomic validated blob, strict roles/checksums/schema, priority-aware dedup, OS-backed active pins, entry/byte limits and bounded failure bypass. |
| `server/dem.py` | Quality attached to exact tile future result; provisional arrays excluded from LRU, nested quality preserved, synchronous idempotent completion/epoch update before exposing results, explicit source snapshot invalidation, failed polar sample epochs. |
| `server/earthd.py`, `blockworker.py` | Once-per-job Grader/chunk/bridge/tunnel generations; same-anchor upgrades/settings invalidate current caches; old holders remain isolated. Server rejects stale heightmap publication, uses distinct output paths and respects held/active cleanup. |
| `server/navgraph.py` | In migration mode, bridge navigation requires continuous finite deck coverage and actual emitted-node support. Missing/fragmented support is omitted rather than mapped onto ground below. Legacy behavior remains available with migration off. |
| `tools/baseline_fixture.py` | Optional existing diagnostic mode routes the same phase 01 terrain input through shared preparation and reports compilation evidence. Default fixture remains compatible. |
| Six new test modules + `tests/fixtures/worldplan/roads.json` | Plan identity, partial upgrades, stale completion, bridge gaps, settings, retirement, publication/pins, restart, seams, cancellation and real OSM road geometry parity with analytic elevation. |

Paths in that table are relative to `source`. New test modules: `test_worldplan.py`, `test_dem_provisional.py`, `test_server_generations.py`, `test_worker_generations.py`, `test_terrainprep.py`, `test_compiledcache.py`.

## Contract and migration boundary

- Canonical fact identity includes geographic cells, full normalized road coordinates/topology/attributes/rules/ownership, source content digests, versions and provenance. Missing numeric attributes remain explicit missing tags, not zero; raw array digests preserve source distinctions. Derived anchor XY and timing/cache-hit fields do not define canonical facts.
- The declared request halo is bounded to 64 cells/2 MiB of fact arrays. Long profiles and tunnel chains can reach beyond 450 m; this halo is explicitly **not certified closed**. Budget omission is unknown, never complete. Anchor history is not a dependency scan.
- Prepared identity includes exact WGS84 anchor ENU frame, lattice/size/quantum/LOD, unquantized prepared heights, quantized grid, **pre-hole surface indices**, **post-hole TER indices**, `zBase`, `maxHeight`, settings, schema and actual preparation/geometry/native/dependency fingerprints. Identical grids with a 64 m vertical offset have distinct geometry identities. Conservative unquantized identity can miss reuse for subquantum changes.
- Ephemeral request IDs, anchor IDs and output filenames do not define persistent content keys. Canonical facts can retain an ID across a new anchor; anchored geometry and transforms rebuild. Existing anchor quantum and request/rebase identifiers are preserved. No XY-only rebase relabeling or cross-anchor grading-profile reuse is claimed.
- Vertical reference is provider-native elevation metres with unknown datum unless an adapter supplies evidence; existing geodesy interpretation is retained. Geographic precision is not accuracy.
- Current DEM/MapData APIs cannot certify complete immutable provider coverage. Unknown/fallback/partial decisions are not promoted to FINAL. **No persistent Grader decision plan is stored.** Existing per-run `plancache` protection remains in legacy mode and is bypassed for uncertain migrated generations.
- This complete vertical slice persists **compiled terrain payloads**, after current preparation has materialized and verified all encoder inputs. It reuses PNG/mask/TER encoding work, not uncertain geographic interpretation. Existing terrain/collision consumers receive their usual files; road/nav consumers keep the same generation's Grader/profile adapters. Input quality/support remain explicit; the manifest does not certify engine collision or bridge activation.

Switches (process environment; no live installation changed):

```text
BNGE_SHARED_TERRAIN=on       default: common preparation
BNGE_SHARED_TERRAIN=off      exact old preparation path for rollback/parity
BNGE_COMPILED_TERRAIN=1      opt in to compiled payload cache and generation migration
BNGE_COMPILED_TERRAIN=0      default: preserve existing session/cache behavior
BNGE_BENCH_TERRAINPREP=1     diagnostic fixture uses actual common preparation
```

Pools, critical-work reservations, photo futures, urgent DEM context, outer cancellation/stale-request handling, frame governor and Lua/protocol behavior were not redesigned. Synchronous preparation propagates progress/cancellation exceptions; current preparation still runs on a cache hit.

## Publication, invalidation and resource lifecycle

An artifact is one same-filesystem atomically renamed `BE2ART1` blob with bounded JSON manifest and per-product byte lengths/SHA256. Reads reject truncated, corrupt, wrong-schema/type/descriptor/role, trailing or oversized data. No pickle or archive extraction. Products are copied atomically to caller-owned session paths before unpinning; no live cache paths escape.

Default committed-store limits: 256 MiB, 64 MiB per entry, 512 entries; 1 MiB manifest, 64 product roles. Oversize/busy/unavailable caching bypasses storage without discarding required generation. `xproc.once` retains priority bypass and bounded waiting; no shared FIFO replaces reserved worker pools. OS-locked pin files protect reads/builds/installs across processes and release automatically on process death. Temporary cleanup correlates the exact writer pin, so a newer same-key reader does not protect a crashed writer's leftover blob indefinitely.

Transient fallback quality travels with the exact future, never inferred from `Dem.served` or a callback's thread-local state. Provisional results are retried; completed transient-fallback→upgrade or explicit `Dem.invalidate_snapshot()` advances generation. Polar failure→success also advances before return. Waiters and callbacks share idempotent finalization, preventing the race where a caller saw provisional data before its epoch changed. Raw negative-cache markers retain existing provider TTLs; no automatic discovery claim is made for new coverage while known-coverage/ancestor results remain memoized. Arbitrary external edits to files treated as immutable source snapshots are not automatically discovered; intentional provider upgrades must advance their explicit revision/invalidation API.

Generation replacement installs fresh Grader/ChunkCache/TunnelProfiles/BridgeProfiles bundles atomically. Old in-flight holders retain old objects; they cannot repopulate the new bundle. Same-key newer heightmap requests own their future and unique generation filenames; late old completion cannot remove/register over the newer future. Worker invalidation is reported internally to the parent for heightmap-cache invalidation. Wire anchor/request IDs are unchanged.

Session output copies and compiled storage have distinct lifetimes. Generation road/nav/patch directories are anchor-tagged; retirement uses the existing gradual deletion queue after held anchors, active/queued jobs and protected terrain keys release. Since existing terrain acknowledgments identify keys rather than generations, all copies for a protected anchor remain until that anchor can retire. **There is no new per-live-anchor/session-copy byte cap**; the hard byte/entry caps above apply to compiled storage. This conservative retention avoids deleting still-loaded engine files; finer release/activation transactions belong to phase 03.

## Verification and measurements

Raw root: `%LOCALAPPDATA%\beamng-earth-2\benchmarks\02-world-plan-cache`.

- Initial independent contracts: 46 passed before subsequent review fixes; retained as intermediate evidence, not relabeled final.
- Fixed contract pass before final identity tightening: 44 tests (worldplan 7, server 8, worker 7, preparation 9, store 13) passed; the separate DEM provisional suite had 6 passes.
- Stable pre-identity full regression: 18 modules, **389 tests: 387 passed, 2 skipped, no failures/errors**, summed process wall 169.993 s. Logs in `regressions/results.json`; fingerprint `824a2c86d3bd38c06d2a8d502aeb61d3cd3ad8c900114151dab8984e28b38421` was unchanged across that batch.
- The two skips are standalone LuaJIT protocol inspection and a `test_mesh` stock-asset check using a Linux-style path. Neither establishes actual Windows game-install status.
- The last change only strengthens prepared identity (`height_u`, vertical offset/range, algorithm fingerprints); geometry/serialization behavior is unchanged. The affected plan/preparation/store suites reran **31 tests, all passed** (8/10/13; summed process wall 7.779 s), stable final fingerprint `168c9aea3d20280b49b274e131f78bcca9f330b6ed38b82026b0741c94a851aa`.
- Across staged runs, latest coverage is **52 new cases + 389 regressions = 441 unique cases: 439 passed, 2 skipped**. This does **not** mean all 441 reran at the final identity-only fingerprint. `test-summary.json`, `new-contracts`, `fixed-contracts`, `regressions`, and `final-identity/results.json` retain exact versions/results.

The regression selection includes phase 01's 11 focused modules plus `test_baseline_benchmark`, `test_heightmap`, `test_mesh`, `test_bridgetypes`, `test_tunnels`, `test_navgraph`, `test_dem`. New cases cover simultaneous old/new generations, same-anchor actual DEM partial→complete changes, nested/polar failures, settings changes, unsupported/fragmented bridges, overlap/corner equality, manifest corruption, interrupted/stale temporary publication, cross-process fresh restart, active pins and release-aware retirement. Three source-backed OSM geometries reuse phase 01 saved coordinates/way versions/attribution; elevations are analytic, not surveyed DEM. These are standalone geometry assertions, not engine collision validation.

Reproduction from `source`, with dedicated TEMP/TMP and offline flags:

```powershell
$stateRoot = Join-Path $env:LOCALAPPDATA 'beamng-earth-2'
$pythonExe = Join-Path $stateRoot 'venvs\py312\Scripts\python.exe'
$env:BNGE_TEST_OFFLINE='1'; $env:BNGE_SKIP_DRIVE='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:TEMP=Join-Path $stateRoot 'runtime\tmp'; $env:TMP=$env:TEMP
& $pythonExe -m unittest discover -s server/tests -p test_server_generations.py -v
# Each exact module was executed independently; raw results record all argv/cwd/versions/exits.
```

External reproducible runners `run_tests.py`/`run_measurements.py` live at the raw root and refuse reused result directories. Measurements were serialized under the validator, with the same hardware/background-load caveats as phase 01. Comparison modes use identical source fixtures: legacy diagnostic, common cache-off, first compiled build and fresh-process hit. The probe forwards actual encoder calls, retains output hashes/PIDs/source-cache and compiled-cache state, and records process memory. All **16 timed processes passed**, with distinct PIDs/plan-run namespaces and identical 51,878-byte output matching phase 01's `b0807e31f6696fb03fb6c23600aaf6071c1bcb3002f9647f4fda967f288a169d`.

The comparison separately controls normalized-source cache state and compiled-cache state: two source-cold legacy seeds, source-warm comparisons, two independent first compiled builds, four fresh-process compiled hits and reversed comparison order. Source-warm runs made zero fake transport requests. The fixture release `2099-01-01.0` remains a deliberately synthetic identifier; this is not a live data-source performance test.

| Source-warm mode | Runs | Fixture wall median, s | Process CPU median, s | Whole-process median, s | Terrain preparation incl. PNG median, ms |
|---|---:|---:|---:|---:|---:|
| Legacy phase 01 path | 4 | 0.955995 | 1.007813 | 2.733519 | different stage scope |
| Common preparation, cache off | 4 | 0.948194 | 0.960938 | 2.715161 | 20.89385 |
| Compiled first build | 2 | 0.968989 | 0.945313 | 2.728320 | 38.67840 |
| Compiled restart hit | 4 | 0.944560 | 0.929688 | 2.708044 | 16.54980 |

Observed common terrain-stage ranges: 19.8623–21.4414 ms; first compiled build: 37.7020–39.6548 ms; hits: 16.2912–17.0537 ms. First builds incurred manifest/hash/publication cost; restart hits skipped the actual PNG encoder (first build `png_bytes=1`, all four hits all encoder counts zero). Whole-pipeline medians are close and CPU accounting is coarse; this does not establish a meaningful general end-to-end gain. The cache stays opt-in. No in-game frame-time or safe-drivability claim follows.

The external probe preloads heightmap and forwards encoders equally in every mode. Consequently exact import/timing scope differs from the old phase 01 run; matched comparisons here are preferable to interpreting cross-session timing differences. No cProfile enabled. OS page cache and the protected other session's background load remain uncontrolled.

One timed compiled artifact was 54,407 bytes, with two request-local fact cells; quality `provisional`, halo `closed=false`, decisions persisted `false`. Key `94c9409098209aa57d1aade86b09ab045b0a2c36448443a314f91368ae228f24`, canonical fact ID `4a8964782c820e1f1833648f8d2844175ed72d184fa2cc4d5dadb5977abb7271`, anchored prepared ID `2092328adb3fe938d70dd39ac67c4d28186168ec9e7a10126451b85bcf13c80f`. The fixture digest remains `b2b26bd7a5436fc84aa4d6ebdccfc2830811fc5b6a1b3401ed5d6d7bb3815f3a`, normalized digest `ab44aeb90d5524eb3285734417cb6623d8358321fb9a503f120ae235f0626a6e`.

Separate **untimed** fresh-process payload checks passed for all **12 PNG/TER/mask products**, byte-identical to cache-off preparation including tunnel holes. Cold called PNG/TER encoders once each and made 11 PNG saves; warm called no encoders. This validates persistent reuse for the complete product family without pretending the PNG timing table measures TER/mask performance. See `payload-checks/results.json` and `run_payload_checks.py`.

Raw measurements: `measurements/results.json`, `measurements/summary.json`, root `summarize_measurements.py`; exact argv/env/order/per-run result, manifest inspection, memory and encoder counters are retained. Lifetime process peak working set was **88,068,096–90,873,856 bytes**, peak commit **607,920,128–618,926,080 bytes** (all 16 memory API calls succeeded). These are not engine RAM/VRAM. The two legacy source-cold fixture runs took 1.596493–1.622713 s wall, 1.375–1.4375 s process CPU. No runtime budget was tuned and phase 01's standalone guardrails remain provisional, not game promises. `independent-validation.md` summarizes the validator's evidence and limits.

## Review evidence and agent ledger

Host task names are actual returned IDs; opaque UUIDs and separate model-runtime telemetry were unavailable. Lead requested Sol/medium, project defaults agree, but active lead model/effort cannot be independently introspected. No xhigh escalation or recursive agent trees. At most three subagents were active concurrently; the validator was queued after exploration completed. Lead alone owned Git mutations (none needed).

| Task | Requested/known role settings; ownership | Contribution and findings |
|---|---|---|
| `/root/geometry_map` | earth_explorer, host-defined Sol/medium; read-only | Traced exact duplicated callers/cancellation boundaries, source/DEM quality gaps, anchor ENU semantics, request halos and existing parity fixtures. |
| `/root/world_compiler` | earth_specialist, host-defined Sol/high | Owned common preparation/store/byte helpers/worker wiring and assigned tests. Transferred earthd ownership to lead before generation integration. Fixed JSON list/dict normalization and malformed headers; exact writer-pin orphan cleanup; worker/source settings consistency; vertical identity regression. Author ran focused tests, no benchmarks. |
| `/root/invariant_review` | earth_reviewer, host-defined Astra/high; no source edits | Reviewed before edits and stable patches. Reproduced NaN fact loss/history fanout, Future callback race, polar gap, missing settings invalidation, fragmented bridge support, lifecycle cleanup and vertical-identity aliasing. All fixes recorded in targeted regressions; independently reran critical reported cases (5 focused checks passed in 0.080 s, plus exact Future race before/after). Final identity-only source review accepted; affected reruns and performance remain validator evidence. |
| `/root/baseline_validation` (reused) | earth_validator, host-defined Sol/high; external harness/reports only | Independent staged tests (441 unique cases), immutable-source fingerprint checks, 16 matched timed processes, two untimed full-product restart checks and raw evidence. No source edits or game calls. Final source/figures/paths/staged disclosure/fixture provenance verified; final handoff accepted without corrections in root final-review.json. |
| `/root` | Lead; worldplan/schema, DEM/Server/nav integration, source fixture and diagnostics/handoff | Fixed reviewer findings, coordinated edit/test barriers and disjoint ownership, preserved source revision and phase 01 edits. |

Relevant Context7 calls: `/python/cpython` resolved/queried for atomic `os.replace`, future callback/result ordering, OS locks and timing; `/numpy/numpy` resolved/queried for array representation and avoiding pickle. Primary references: [Python filesystem operations](https://github.com/python/cpython/blob/main/Doc/library/os.rst), [futures](https://github.com/python/cpython/blob/main/Doc/library/concurrent.futures.rst), [Future implementation](https://github.com/python/cpython/blob/main/Lib/concurrent/futures/_base.py), [NumPy array format](https://github.com/numpy/numpy/blob/main/doc/neps/nep-0001-npy-format.rst). The reviewer independently demonstrated that Future result waiters wake before callbacks finish; the fix does not rely on callback completion.

## Gate and next phase

The standalone shared-path/compiled-artifact slice has passing geometry/parity/invalidation/cache tests, measured restart reuse and independent final acceptance (`%LOCALAPPDATA%\beamng-earth-2\benchmarks\02-world-plan-cache\final-review.json`). Live compatible cross-process engine activation, frame time, collision contact, bridge/tunnel driving, rebase installation and generation-release timing remain unrun. Closed-halo/provider-complete persistent geographic decisions and cross-anchor profile reuse are deliberately not claimed by this first compiled terrain slice. Current provider coverage cannot be certified by the existing APIs; it remains provisional rather than a fabricated final plan.

Next: phase 03, after this handoff closes. Preserve shared preparation, canonical/prepared identities, uncertainty flags and active-entry protections. Freeze cancellation/activation/release contracts before changing scheduling; do not replace critical reservations with one FIFO. Use an unoccupied authorized engine session for engine-only checks; keep the original project/session protected.
