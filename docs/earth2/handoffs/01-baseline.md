# 01 — Baseline and useful instrumentation

Date: 2026-10-26, America/New_York. Phase 01 standalone acceptance passed: repeatable measurements, existing-test baseline/regressions, overhead checks and provisional scoped budgets recorded and independently reviewed. Engine validation and geographic-category coverage remain explicitly pending. No world-generation optimization or phase 02 implementation.

## Revision, environment and scope

- Package: `C:\Users\Culle\OneDrive\Desktop\beamng-earth-2`.
- Source: `source`, branch `earth2/development`, starting/current HEAD `aae4d6126d65a07315d0b28cc4a81dd68d86280a` (`v1.8.2`). Prechange worktree clean. No upstream adoption, commit, reset, push, or original-checkout operation.
- Interpreter: `C:\Users\Culle\AppData\Local\beamng-earth-2\venvs\py312\Scripts\python.exe`, Python 3.12.14; requirements from kickoff already installed. This phase reran `pip check`: exit 0, no broken requirements.
- Observed packages: numpy 2.5.4, Pillow 13.0.0, duckdb 1.5.6, fsspec 2026.9.0, certifi 2026.10.23, lupa 2.8. Native `earthfast.VERSION=69` available. Rust 1.98.1 remains available; no Rust code changed or rebuild attempted. Standalone `luajit` remains unavailable; Python LuaJIT binding exists.
- Host: Windows 11 Pro 10.0.26200; AMD Ryzen 7 7700X, 8 cores/16 logical processors; RAM 50,587,820,032 bytes (~47.1 GiB); Radeon RX 6900 XT and integrated Radeon reported. WMI `AdapterRAM` is not authoritative VRAM capacity; VRAM unverified. No engine version, vehicle, graphics settings or frame time was measured.
- All generated environments/caches/raw results are under `%LOCALAPPDATA%\beamng-earth-2`. No mod installation, live game/Blender control, global datasets, credentials or model downloads. The original project/session was not inspected or interrupted.

## Changes and measurement contract

| File | Purpose |
|---|---|
| `source/server/blockworker.py` | Extend existing `_accounted` CPU/job accounting with opt-in `BNGE_TIMINGS=1` bounded wall/error/build-stage aggregates and `timing_snapshot`; preserve return values, exceptions and original CPU counters. |
| `source/tools/profile_build.py` | Extend the existing profiler with structured offline JSON, explicit cache/output paths, source and processing fingerprints, failure exit codes, cold/warm-restart modes; expensive cProfile now requires `--profile`. Legacy geographic cached-build mode remains. |
| `source/tools/baseline_fixture.py` | Diagnostic-only tiny deterministic adapter using existing `mapfixtures`, real MapData cache, grading, roadmesh, heightmap and road writers. No network/game. |
| `source/server/tests/test_baseline_benchmark.py` | Independently authored integration/contract tests for fresh processes, persisted hits, output parity, errors, path/cache guards and accounting switch. |
| `scripts/Resolve-BaselineRoutes.py` | Bounded source-only OSM route acquisition and default offline replay, no game calls; query-keyed cached responses. |
| `config/baseline-routes.json` | Seven source-resolved road samples, exact coordinate/node/way/version/hash evidence plus synthetic antimeridian seam. OSM attribution and ODbL license retained. |
| `config/01-baseline-budgets.json` | Observations and provisional standalone guardrails, with engine-dependent values pending; design/evidence file, not runtime configuration. |
| `handoffs/01-baseline.md` | This integration handoff. |

No second telemetry service, dependencies or framework were added. Existing fetch counters (`netfetch` snapshot/daily counters), MapData layer timings/hits/provenance, terrain build stages and worker CPU accounting remain authoritative. Existing Lua `perflog`, governor snapshots, core frame marks/follow/rebase timing cover future engine inspection; Lua behavior was not changed.

Structured stages: `source_fetch`, `normalization`, `terrain_planning`, `road_planning`, `compilation`, `serialization`, `queue_wait`, `engine_installation`, `activation`. Wall seconds and process-wide CPU seconds are explicitly distinct. MapData normalization is inclusive of fetch/decode/cache I/O, so its duration overlaps fake transport fetch timing; never sum them as exclusive stages. Queue wait is an uncontended diagnostic executor sample, not production queue age. Engine installation/activation are null with reasons, never zero estimates.

The direct synthetic stages bypass worker jobs: `worker_timings.reason` says this. Pipeline enabled/disabled runs establish output parity; actual `_accounted` overhead is measured separately against the original wrapper, patched-disabled and patched-enabled. CPU across overlapping threads/jobs is not exclusive CPU attribution. Short stages can round to zero process CPU because of timer granularity; this does not establish zero CPU cost. Expensive cProfile and memory tracing are excluded from timed comparisons.

Cold means an absent/empty dedicated prepared cache; the tool refuses to clear or overwrite populated caches. Warm restart uses a new interpreter/PID and persisted MapData roads layer, validates processing/fixture/normalized/output digests, and refuses stale/incompatible inputs. Each process starts a new plan-run namespace; incomplete road plans are deliberately rebuilt. No persistent road-plan reuse is claimed. OS page cache is uncontrolled; this is prepared-cache cold, not a reboot or disk-cache purge.

## Existing-test baseline and regression checks

Before any production edits, the independent validator ran 11 modules sequentially: `test_world_consistency`, `test_roads`, `test_grading`, `test_roadmesh`, `test_cache`, `test_plancache`, `test_chunkcache`, `test_protocol`, `test_protocol_agreement`, `test_blockworker`, `test_mapdata`.

Prechange: **265 tests, 264 passed, 1 skipped, 0 failures/errors**, all subprocesses exit 0; summed process wall 138.088 s. The skip is `test_protocol_agreement.ConstantsTest.test_default_err_and_exact_level_in_lua`, which requires standalone `luajit`; installed `lupa.luajit21` does not satisfy that check. No baseline failure to subtract from new regressions.

Raw prechange evidence: `%LOCALAPPDATA%\beamng-earth-2\benchmarks\01-baseline\prechange\results.json`, per-module logs, `machine.json`, and `run_prechange.py`. Interpreter, packages and environment flags are in `results.json`. The same 11 modules postchange again ran **265 tests, 264 passed, 1 unchanged skip, no failures/errors** (summed process wall 134.486 s). Separately, **8 new contract tests passed** in 19.241 s. Combined final focused status: **272 passed, 1 skipped**. This is test status, not a timing improvement claim. Postchange logs/argv are in `postchange/results.json`, `postchange/new-contract-tests.json` and `postchange/run_postchange.py`.

Literal command pattern from working directory `source` (each named module separately, 240 s harness timeout):

```powershell
$stateRoot = Join-Path $env:LOCALAPPDATA 'beamng-earth-2'
$pythonExe = Join-Path $stateRoot 'venvs\py312\Scripts\python.exe'
$env:BNGE_TEST_OFFLINE = '1'
$env:BNGE_SKIP_DRIVE = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:TEMP = Join-Path $stateRoot 'runtime\tmp'
$env:TMP = $env:TEMP
& $pythonExe -m unittest discover -s server/tests -p test_world_consistency.py -v
# Repeat with the 10 other exact module filenames listed above.
& $pythonExe -m unittest discover -s server/tests -p test_baseline_benchmark.py -v
```

`results.json` retains the literal argv, cwd, flags, exit and durations for every executed module. Direct unittest avoids `tools/run_tests.py` writing timing history inside source. Full Python/Lua/Node suites, Rust tests and engine tests were not substituted for this focused scope.

## Reproducible benchmark and actual results

Artifacts: `%LOCALAPPDATA%\beamng-earth-2\benchmarks\01-baseline`. Only the validator ran timed benchmarks; author performed syntax/help smoke checks. Execution was serialized. The fixture is a few synthetic GeoParquet roads and deterministic analytic DEM, not one of the geographic driving routes. Its release `2099-01-01.0` is an intentionally fake fixture identifier, not a real future provider release; source-version labels describe the fixture inputs. Source release, fixture digest, normalized digest, native version, dependency versions, per-file processing hashes, dirty source paths, PID and plan-run IDs are included in each report. HEAD alone does not identify the uncommitted instrumentation patch; retain the processing fingerprint.

Example repeat commands from `source`, using a **new absent cache/output pair** each time:

```powershell
$env:BNGE_TIMINGS = '1'
$benchRoot = Join-Path $stateRoot 'benchmarks\01-baseline\manual-repeat-01'
& $pythonExe tools/profile_build.py --synthetic --cache-mode cold --cache-dir "$benchRoot\cache" --output-dir "$benchRoot\cold" --json "$benchRoot\cold\result.json"
& $pythonExe tools/profile_build.py --synthetic --cache-mode warm_restart --cache-dir "$benchRoot\cache" --output-dir "$benchRoot\warm" --json "$benchRoot\warm\result.json"
# Use another fresh output directory for every further restart.
# --profile is for diagnosis only, outside baseline comparisons.
```

Actual measurements: two independent cold caches and ten fresh-process warm-restart runs, including four reversed/alternating accounting-on/off pairs. All 12 fixture subprocesses and 12 wrapper microbenchmark subprocesses exited 0. All fixture PIDs and plan-run namespaces were distinct; cold had 4 fake transport callbacks, warm had 0 with a true persisted roads-layer hit. Cold fake transport handled 55,062 bytes: 54,154 Overture + 899 STAC + 9 simulated-404 bytes. The fixture omits optional Layercake OSM-tag parquet, exercising missing-tag fallback; it is not complete road-tag coverage or a live provider outage. Existing fetch counters preserve the 404; `source_fetch.errors=0` means no callback exceptions, not that every HTTP status was successful. All 12 emitted identical 51,878 bytes across PNG, cdae and road JSON. This demonstrates normalized-source reuse and deterministic emitted outputs, not a compiled artifact cache or engine speedup.

| Observation | Cold (2 runs) | Warm restart (10 runs) |
|---|---:|---:|
| Fixture wall median (range), s | 1.584056 (1.572077–1.596035) | 0.929796 (0.915185–0.949014) |
| Fixture process CPU median (range), s | 1.437500 (1.343750–1.531250) | 0.921875 (0.843750–1.046875) |
| Whole process wall range, s | 2.721233–3.460757 | 2.788534–4.236781 |
| Inclusive normalization median, ms | 124.2302 | 16.8640 |
| Road planning median, ms | 680.4745 | 127.6550 |
| Terrain planning median, ms | 5.1036 | 5.2139 |
| Mesh compilation median, ms | 2.0586 | 8.9977 |
| Serialization median, ms | 21.3021 | 19.5471 |
| Uncontended executor queue median, ms | 0.2444 | 0.2632 |

Totals include separately reported fixture-source generation; per-stage timings do not cover that setup or all imports. Source-fetch callbacks overlap normalization, and zero callbacks on warm are established directly. The synthetic corpus's canopy/worldcover version labels do not mean those provider pipelines were benchmarked: the workload consumes roads and an analytic DEM. Whole-process startup varies considerably, and some stage medians increase; no general speedup is claimed. Background activity from the protected other session is uncontrolled; only this phase's own tests/benchmarks were serialized.

Across the 12 fixture processes, lifetime peak working set was **88,166,400–91,422,720 bytes**; peak commit **610,492,416–617,607,168 bytes**. These include imports/setup. An external self-handle Windows `GetProcessMemoryInfo` wrapper collected peak counters, not `/proc` or the worker's Windows-unsupported RSS helper. They are not VRAM or in-game memory.

Independent wrapper microbenchmark: 4 fresh processes per mode, 20 batches of 100,000 trivial calls per mode, reversed/paired order. Median legacy wrapper **913.7025 ns/call**, patched default-disabled **948.295 ns/call**, enabled **1534.04 ns/call**. Differences of mode medians: disabled minus legacy **34.5925 ns/call** (~3.79% of this trivial wrapper), enabled minus disabled **585.745 ns/call** (~61.77% of the trivial disabled wrapper). This measures the wrapper path, not relative overhead for a real build, concurrent workers or the game. No statistical tail bound is established.

`config/01-baseline-budgets.json` distinguishes observed ranges from proposed guardrails. Proposed limits use observed maxima ×1.25, rounded upward: cold fixture wall/CPU 2.0/2.0 s; warm 1.2/1.35 s; whole process 5.5 s; lifetime working set 128 MiB and commit 768 MiB; one fixture's serialized payload 64 KiB. Only one diagnostic process runs at a time. Payload size is not transport throughput; production queue age, support distance, engine install/activation and frame budgets remain null. These guardrails are not runtime settings or statistically proven acceptance thresholds.

Raw reproducible artifacts under `measurements/`: `run_measurements.py`, `original-blockworker.py` extracted from the pinned Git revision, `results.json` (literal commands/order/reports), `summary.json` and `summarize.py`, per-run `result.json`/`peak-memory.json`, stdout logs and micro logs. Retain the existing artifacts; a repeat needs new output/cache paths rather than deleting old evidence. Fingerprints:

```text
processing  9e4f734908d9abd58cc7b80b1f0481cec9af2c319f8c0ab3329f233f4dd490fb
fixture     b2b26bd7a5436fc84aa4d6ebdccfc2830811fc5b6a1b3401ed5d6d7bb3815f3a
normalized  ab44aeb90d5524eb3285734417cb6623d8358321fb9a503f120ae235f0626a6e
outputs     b0807e31f6696fb03fb6c23600aaf6071c1bcb3002f9647f4fda967f288a169d
```

## Source-resolved routes and engine procedure

The resolver first attempted seven bounded Overpass queries; all returned HTTP 502. One alternate Kumi request timed out. It then used API v0.6 read-only map GETs: 0.003° squares around each center, expanding only the forest query to 0.015° when the smaller response contained no eligible road. Maximum response read is 2 MiB; no authentication or full datasets. The seven retained final samples total 3,599,347 bytes, spanning 214,860–1,005,666 bytes per response (earlier bounded attempts are separate). Each selected route stays on contiguous nodes of one way and is capped at 1.2 km. Reverse `oneway=-1` geometry is respected; access tags marked private/no/destination/agricultural/forestry are excluded. Missing access tags are not proof of current physical/legal access or engine drivability.

| Scenario ID | OSM way / version | Selected length | Source name |
|---|---|---:|---|
| dense_urban | 5671095 / 23 | 65.52 m | Stuyvesant Street |
| rural_forest | 727578016 / 8 | 1183.91 m | unnamed in source |
| arid | 24293437 / 14 | 492.55 m | شارع عَمَّان |
| tropical_urban | 4741908 / 13 | 213.92 m | Parliament Road |
| mountain | 679088117 / 16 | 124.04 m | Agustinas |
| high_latitude | 22901544 / 14 | 72.57 m | Killengreens gate |
| southern_rural | 777634273 / 7 | 68.62 m | Moorhouse Avenue |
| antimeridian | synthetic | N/A | seam coordinates only, no road |

`config/baseline-routes.json` records exact node/coordinate sequences, way timestamp/version, source query/hash/raw path and proposed settings. All real samples are `source_resolved_engine_drivability_pending`; category coverage remains unverified. In particular Agustinas and Moorhouse Avenue are urban roads, so the mountain/rural categories are **not fulfilled** merely by resolving their centers. Short segments are not representative sustained-driving workloads. Do not tune these away or claim global coverage; resolving appropriate longer mountain/rural routes remains an engine-baseline preparation task.

Raw route inputs and replay output: `%LOCALAPPDATA%\beamng-earth-2\benchmarks\01-baseline\routes-osm-map`. Original failed query manifest remains in `routes`. Local file mtime is labeled as such; copied-file mtime is not acquisition time. OSM source element revisions/timestamps are preserved. Coordinate reference is WGS84 latitude/longitude degrees, with no elevations/vertical datum supplied.

Executed bounded acquisition and final replay from the package:

```powershell
& $pythonExe scripts/Resolve-BaselineRoutes.py --fetch
& $pythonExe scripts/Resolve-BaselineRoutes.py --fetch --provider osm-map --output-root "$stateRoot\benchmarks\01-baseline\routes-osm-map"
# Final saved responses replay without network; exit 0:
& $pythonExe scripts/Resolve-BaselineRoutes.py --provider osm-map --output-root "$stateRoot\benchmarks\01-baseline\routes-osm-map"
```

An unoccupied, separately authorized engine test session is required before actual route driving. Preserve existing session state; record actual BeamNG build, full vehicle/config path, resolution/graphics, weather/clock, provider revisions and network conditions. Proposed vehicle is stock base-manual Ibishu Covet, 10 m/s, clear fixed noon; actual config/settings are null until verified. First inspect support/collision and navigation along source routes, then record exact driven trace and identical input replay. Run cold, restart with retained cache, then steady drive, logging safe drivability/support distance, p50/p95/p99/max frame times, declared hitch thresholds, install/activation timings, queue ages, stale/cancelled counts, RAM/VRAM, scene/collision counts and bytes. Use existing core/perflog/governor diagnostics and source tools only after confirming Windows compatibility. `tools/perfmon.py` uses Linux `/proc`/`pgrep`; do not assume host metrics work on Windows. No game tool was invoked here.

## Documentation and agent ledger

Context7 policy applied. Source mapping needed no external API assumption. Specialist queried `/python/cpython` timing/profiling docs: perf_counter is elapsed wall, process_time includes process CPU; profiler overhead is not benchmark evidence. Validator queried Python subprocess docs for cwd/env/capture/timeout and ctypes signatures. Win32 Context7 resolution `/websites/learn_microsoft_en-us_windows_win32_api` returned no matching GetProcessMemoryInfo excerpt after two scoped queries, so validator used primary Microsoft documentation: [GetProcessMemoryInfo](https://learn.microsoft.com/en-us/windows/win32/api/psapi/nf-psapi-getprocessmemoryinfo), [PROCESS_MEMORY_COUNTERS](https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-process_memory_counters), [GetCurrentProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getcurrentprocess). Lead resolved and queried `/python/cpython` bounded urllib reads/timeouts; `/websites/wiki_openstreetmap_wiki_overpass_api` query/output/bounds; `/websites/wiki_openstreetmap` API map-call semantics. Other primary references: [Python time](https://github.com/python/cpython/blob/main/Doc/library/time.rst), [subprocess](https://github.com/python/cpython/blob/main/Doc/library/subprocess.rst), [urllib](https://github.com/python/cpython/blob/main/Doc/library/urllib.request.rst), [OSM map call](https://wiki.openstreetmap.org/wiki/Map_call), [Overpass QL](https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL). [OSM copyright](https://www.openstreetmap.org/copyright) was opened to verify ODbL and contributor attribution.

Host task names are the actual returned IDs; opaque UUIDs were not exposed. No recursive agent trees. The lead alone owned Git mutations (none needed); shared ownership contracts preceded source edits. The verifier completed unchanged baseline before releasing the edit barrier.

| Task ID | Role / requested and known settings | Ownership/output | Review findings and resolution |
|---|---|---|---|
| `/root` | Requested Sol/medium; project defaults agree; active lead model/effort not introspectable | Contracts, routes/resolver, config, integration/handoff | Accepted standalone/engine separation; no source production authorship. |
| `/root/baseline_map` | `earth_explorer`, host-fixed Sol/medium; no separate telemetry | Read-only instrumentation/fixture map; independent route review | Corrected fixture path to `mapfixtures.py`, ChunkCache location to `heightmap.py`; route review caught missing final antimeridian write, mtime/acquisition labeling, provider bounds, category overclaims. All four fixed and re-review accepted; hashes/continuity/versions verified. |
| `/root/instrumentation` | `earth_specialist`, host-fixed Sol/high; no separate telemetry | Only blockworker.py, profile_build.py, baseline_fixture.py | Source patch stable; no benchmarks run by author. Clarified direct synthetic stages do not invoke worker wrapper; overhead checked separately. |
| `/root/baseline_validation` | `earth_validator`, host-fixed Sol/high; no separate telemetry | Separate test_baseline_benchmark.py; pre/post test logs, 12 fixture processes, 12 wrapper processes, memory evidence and independent stable patch review | No material implementation defect; corrected test JSON path/stable-input comparison before execution; clarified synthetic on/off is output parity only, measured actual wrapper separately, rejected Linux RSS zero, corrected nonexistent environment.log reference. Results integrated above; independent-review.md and final-review.json retained at the phase artifact root. Final review verified hashes, counts, all eight budget calculations and accepted after this report-path correction. |

## Acceptance status and phase 02 entry

Standalone acceptance evidence and final independent handoff/budget review are complete. The final review's sole remaining report-path correction is resolved above; see `%LOCALAPPDATA%\beamng-earth-2\benchmarks\01-baseline\final-review.json`. Known unresolved work: full engine baseline (including steady drive and actual settings), sustained/category-representative mountain/rural/isolated routes, scenario variants for partial DEM/late cancellations/regional providers in measured end-to-end runs, full broader suites and native rebuild/toolchain readiness. Existing unit tests are not substitutes for those measurements. Exact lead model telemetry remains unavailable. No runtime configuration or engine installation was changed.

Next phase: 02 world-plan/cache. Start from this pinned commit plus the recorded processing patch/hash, reproduce fixture digests and relevant tests, then freeze shared terrain/road-plan, completeness, source-revision, boundary/halo and invalidation contracts before editing. Preserve current plan-run isolation and critical-worker reservations; compare common terrain extraction against the recorded output bytes. No optimization claim is supported yet.
