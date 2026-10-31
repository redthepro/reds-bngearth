# Phase 03 — bounded scheduling and consistent activation

Status: standalone implementation independently reviewed and validated. The full engine/retained-memory acceptance gate remains pending.

## Scope and revision

Isolated checkout: `C:\Users\Culle\OneDrive\Desktop\beamng-earth-2\source`.
Starting and current HEAD: `aae4d6126d65a07315d0b28cc4a81dd68d86280a` (`earth2/development`). Earlier phase changes remain in the working tree. No upstream revision change, push, publication, live installation change, or engine control is authorized by this phase.

Raw evidence root: `%LOCALAPPDATA%\beamng-earth-2\benchmarks\03-streaming`.

## Frozen implementation contract

- Preserve the dedicated heightmap/urgent/base/ground/ahead pools, separate request and prefetch process lanes, road/far-building/delta pools and optional horizon pool. The admission controller does not replace them with a FIFO.
- Server-wide nonblocking reservations separate optional visual, demand terrain, speculative terrain, roads, navigation and detached photo work. Bytes are conservative work estimates, not hard bounds on process RSS. Worker completion owns release; cancelling a waiter does not release running work.
- Deduplicate before submission, shield independent consumers of shared futures, retain denied queue entries, and retry within a bounded interval. Validate identity, request and source generation immediately before publishing completed work.
- Negotiate additive lifecycle feedback through the existing protocol. Bound sent-but-unaccounted payload/install cost and client queued/importing cost. A transport receipt is distinct from successful installation, activation and release. Expired feedback must conservatively pause optional production.
- A parent-issued compatibility receipt is scoped to a connection/anchor/request. Local DEM epochs are not a worldwide immutable source revision. Do not stamp a new receipt onto old worker output. Unknown compatibility must withhold dependent navigation.
- Check navigation serial/receipt before reading and again immediately before deferred activation. Advance collision receipt only after successful installation. Validate road replacement before retiring compatible support; unsupported bridge segments must be withheld.
- Preserve existing held-anchor/session/slot-serial acknowledgements and main-thread governor. Cancellation is cooperative at explicit stage boundaries; running Python/process/engine calls are not presumed interruptible.

## Review cases established before implementation

The independent reviewer reproduced all three using production methods/modules and tiny in-memory/stub-engine fixtures:

1. A generation-1 terrain result could reach its caller after source generation advanced to 2, even though cache publication rejected it.
2. Same-anchor navigation serial 2 followed by serial 1 regressed client state to serial 1.
3. An invalid same-key road replacement returned false after deleting the old tile.

Exact snapshots, hashes and `reproduce_before.py` are under `review` in the raw evidence root. These demonstrate application state races, not physical engine collision behavior.

## Verification

Latest deduplicated independent validator results: **29 Python modules, 528 unique cases: 526 passed, 2 skipped; 13 Lua modules, 1,362 assertions passed**. No latest failed module remains. The skips are existing standalone-LuaJIT inspection and a stock-mesh test using a Linux game path; they do not establish Windows game installation status. Lua execution used Lupa's LuaJIT, not a live engine.

These are **staged** results, not a claim that all 528 cases/1,362 assertions reran at the final manifest. Unchanged workcontrol/streaming and pure Lua suites retain earlier passing evidence; affected production/lifecycle cases reran after each fix. Freeze 5/6 changed only test metadata assertions; production has remained at processing SHA256 `67616849c0ff3089f4d794914b8737b1c403fdc98c4d1eb7dc573a13b76ee906` since freeze 4. Every batch records before/after Python/Lua hashes, exact argv, versions, outputs and exit codes. The two intermediate metadata parity failures are preserved explicitly in `validation/test-summary.json`; the corrected blockworker 5-case and terrain preparation 10-case suites both passed.

Python coverage includes protocol and actual negotiated local socket flow; road/terrain/bridge/tunnel/nav geometry; shared preparation/materialized cache parity; source invalidation/generation races; network limits (47 netfetch cases); request replay, stale/different-block acknowledgments, cancellation after height sampling, transport pressure, photo saturation and demand versus prefetch reservations. Lua includes actual stubbed core/session behavior, governor, follow/rebase/terrain swap, roads/navigation/farfield, drop holds and framing. Engine stubs are assumptions, not collision or frame-time measurements.

Independent reviewer also passed 30 lifecycle/workcontrol Python cases in 0.162 s and 27 Lua lifecycle assertions against freeze 4, then independently checked both test-only addenda. Its report accepts the standalone slice and leaves the full engine/retained-memory gate pending.

The independent final admission probe saturated 8 visual, 4 road, 2 navigation and 4 photo jobs across two connection fixtures; demand still admitted its two reserved jobs. Controlled start latency was **0.1051 ms**, one Python observation rather than a production percentile. Cancelling a running consumer retained **64 MiB of visual estimates** until the underlying jobs stopped; no cancelled logical publication occurred, the sibling shielded consumer survived, photo raw futures coalesced, and all counters returned to zero. A separate final fixture verifies one blocked prefetch, denial of a second distinct prefetch, and demand completing before prefetch releases. See `validation/admission-probe-freeze4.json`, lifecycle logs and reviewer scripts.

Reproduction uses the dedicated environment and fresh report labels (runners refuse overwrite):

```powershell
$stateRoot = Join-Path $env:LOCALAPPDATA 'beamng-earth-2'
$pythonExe = Join-Path $stateRoot 'venvs\py312\Scripts\python.exe'
$evidence = Join-Path $stateRoot 'benchmarks\03-streaming'
Set-Location 'C:\Users\Culle\OneDrive\Desktop\beamng-earth-2\source'
$env:BNGE_TEST_OFFLINE='1'; $env:BNGE_SKIP_DRIVE='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:TEMP=Join-Path $stateRoot 'runtime\tmp'; $env:TMP=$env:TEMP
& $pythonExe "$evidence\validation\run_tests.py" --label replay-contracts test_workcontrol test_streaming_lifecycle test_streaming
& $pythonExe -B tools/run_lua_tests.py test_core_stubbed.lua test_core_robustness.lua test_streaming.lua test_streaming_lifecycle.lua test_governor.lua test_terrain_swap.lua test_follow.lua test_rebase.lua test_navgraph.lua test_roads.lua test_drophold.lua test_farfield.lua test_framing.lua
```

The exact 26-module regression argv and split/resume results are retained in `validation/regressions-freeze4/results.json`, `regressions-freeze5/results.json` and `regressions-freeze6/results.json`. `validation/test-summary.json` selects the latest result per module without double counting reruns; `validation/final-report.md` provides the independent evidence summary. `git diff --check` passed; Git only noted existing CRLF normalization for blockworker.

## Implemented behavior and limits

The phase changes `server/workcontrol.py`, `earthd.py`, `streaming.py`, `terrainprep.py`, `blockworker.py`, `roads.py`, `navgraph.py`; Lua `core.lua`, `farfield.lua`, `roads.lua`, `navgraph.lua`; new Python workcontrol/lifecycle and Lua lifecycle tests; protocol agreement, core stub, blockworker graph metadata, preparation metadata parity and one existing generation assertion. Prior phase files and uncommitted changes remain intact. Protocol framing/version, governor, follow logic and Rust kernels are unchanged.

| Admission scope | Default count / estimated byte ceiling | Charge per admitted job |
|---|---|---|
| Far visual, shared across connections | 8 / 128 MiB | 8 MiB; count cap means 64 MiB effective default estimate |
| Demand/provisional terrain | 4 / 64 MiB | 32 MiB; effective maximum 2 |
| Speculative terrain | 1 / 32 MiB, independent of demand | 32 MiB |
| Roads | 4 / 64 MiB, independent pool/admission | 16 MiB |
| Navigation | 2 / 32 MiB, independent pool/admission | 16 MiB |
| Urgent photos | 2 / 64 MiB | 32 MiB |
| Optional photos/refresh | 2 / 64 MiB | 32 MiB |

Demand keeps its process lane; prefetch cannot borrow its admission reservation. Photo refresh no longer occupies the heightmap executor. Detached photos coalesce in flight and retain their own reservation after terrain returns. Denied photos can leave a neutral base; denied refresh remains eligible later. Existing provider host concurrency/token/urgent reservations, byte quotas, retries and backoff in `netfetch.py` are retained rather than duplicated.

Far work preserves collision coverage and partially prepared group ordering, then uses predicted arrival and retained queue arrival times with the existing corridor/camera classes. Admission denial retains the queue item and retries at 100 ms. Oversized far artifacts (more than the 8 MiB payload/decoded-texture allowance) are withheld. Full critical admission gives a bounded terminal busy/retry response. Exact request replay coalesces a running task or replays its completed response; genuinely superseding requests cancel the old consumer.

Raw concurrent futures own reservations, including queued cancellation, submit failure and worker exceptions. Individual asyncio consumers are shielded from one another. Last-consumer retirement signals cooperative cancellation; checkpoints occur between parent-side expensive stages and within common in-process terrain preparation. A running native call or process job is not interrupted internally. Future identity, original input stamp, generation and request ownership are rechecked before publication; final anchor height/tunnel awaits are included. Source-invalidated tiles rebuild geometry; only cosmetic staleness under the same receipt reuses its shape.

Current companion/mod negotiate `hello.streaming.lifecycle=1`. Feedback has separate increasing `feedbackSeq` and `receivedSeq`, queued/importing byte allowance and governor installation-cost estimate. The server charges reported pending work plus transmitted items not included in that report. A receipt alone does not mean installation/release. Optional production starts closed and pauses when feedback expires (3 s). Invalid or regressing reports are ignored and do not refresh its lifetime; terrain reservations remain available. Far pending allowance is 64 MiB and 256 estimated installation ms per connection. The writer separately caps buffered output at 64 MiB and each encoded frame at the existing 16 MiB protocol limit; oversized/slow consumers close cleanly. Legacy clients retain their wire behavior with global admission, transport and strict geometric bridge checks; installed-receipt guarantees require the updated negotiated pair.

Prepared terrain carries `activationId` plus the local connection/anchor `planReceipt`. Commit validation rejects replaced offers, older anchors and connection-wide older activation serials, including older offers for a different block. An ignored old acknowledgment cannot mutate the active receipt. Roads build/publish only against the committed terrain receipt; Lua validates that receipt on receipt and immediately before deferred collider creation. Missing metadata cannot bypass a nonnil committed receipt. Invalid road replacements preserve existing support. Navigation rejects older serials before reading, rechecks collision receipt after coroutine preparation, and withdraws previously applied navigation when receipt/support changes using the existing main-thread empty-map reset. That safety reset can hitch.

Both graph builders now require continuous finite bridge profiles. Process-built bridge edges remain withheld because process-local epochs cannot prove the same source snapshot as installed collision. In-process bridge navigation additionally requires matching actual deck objects across every covered road tile after collision reload. Conservative rectangle coverage may omit a safe edge. Ground navigation resumes after a compatible graded terrain commit; `supportComplete` here distinguishes the deliberately ungraded initial teleport block, **not provider-complete coverage**.

Lifecycle ownership is explicit around existing mechanisms:

| State | Concrete owner/transition |
|---|---|
| requested | desired set/queue and request identity |
| building | admitted lease plus raw future and in-flight registry |
| prepared | validated artifact and terrain offer, or far pending instance |
| installed | governed hidden import completes; queue pressure drops |
| activated | far swap makes the instance shown; matching terrain offer acknowledgment accepts the swap |
| retiring | serial-guarded removal/doom, crossover and gradual deletion queue |
| released | actual deletion/no object, then existing exact slot-serial/session acknowledgment |

Terrain acknowledgment accepts the existing engine swap; settled collision/navigation activation additionally waits for the existing quiet-frame/reload guards. Loaded files remain protected by held anchors/sessions/slot serials. A receipt watermark never releases those resources.

## Demonstrated review findings and resolutions

Initial review reproduced obsolete terrain returning despite cache rejection, navigation serial rollback and invalid road replacement deleting support. Subsequent stable review reproduced: final-await receipt relabeling; duplicate requests cancelling their sole consumer; old far geometry receiving a fresh source stamp; older anchor commit rollback; road/terrain receipt mixing; detached photo backlog; older terrain acknowledgment mutating the current receipt; different-block acknowledgment rollback; and prefetch consuming demand admission. Lead review caught a Lua pose-feedback variable in the wrong lexical scope. Each was fixed with targeted regressions and independently checked. Freeze 1/2/3 results remain intermediate evidence, not silently relabeled final.

The production acceptance checks used `integration-freeze-4.json`, SHA256 `44b2792d107fd2fbb0e784ca3cd59afe117bcf09294d010f3d2fc954138b3ad4` (17 production/test files). The final manifest is `integration-freeze-6.json`, SHA256 `b8e75993d72e4fc814dfbf84d4ab43feae75d004750b97f9aa3800d842d3ca43` (19 files): every freeze-4 hash is unchanged. Two test-only additions account for new safety metadata while retaining exact product/geometry/rule parity: `test_blockworker.py` explicitly asserts `bridgeSupportKnown is False`; `test_terrainprep.py` asserts stable worker inputs report `_worker_epoch_changed is False`. Earlier failed comparisons remain in raw regression logs. Reviewer verified all final hashes before/after, independently passing the focused blockworker case in 0.017 s and preparation case in 0.246 s. Reports: `review/final-review.md` and `review/final-review.json`.

The reviewer's actual 12-terrain-request/blocked-photo reproduction changed from **12 pending photos to 2**, without holding terrain admission. Actual core/socket checks exercised vehicle, free camera and hello reset: feedback sequence 2 → 3 → 0 → 1. No provider or live engine was used.

## Matched baseline measurements

The validator alone ran **16 fresh processes serially**, with 16 distinct PIDs/plan-run namespaces and stable Python/Lua fingerprints. Same recorded host as phases 01/02: Ryzen 7 7700X (8 cores/16 threads), Windows 11 Pro build 26200, Python 3.12.14, native earthfast 69, NumPy 2.5.4/Pillow 13. GPU RX 6900 XT was not exercised by this fixture; its WMI memory field is not a VRAM measurement. No game version/settings apply because no game ran. Protected background activity and OS page cache remain uncontrolled.

Two independent normalized-source caches each start cold; warm comparisons include legacy, common cache-off, first compiled build and fresh-process compiled hits, with reversed comparison order. Compiled first build is source-warm/compiled-cold. The same external encoder forwarding/heightmap preload used in phase 02 was retained. No profiler/tracemalloc was enabled. Source fixtures, normalized digest and all three emitted products match phases 01/02: **51,878 bytes**, output SHA256 `b0807e31f6696fb03fb6c23600aaf6071c1bcb3002f9647f4fda967f288a169d`.

| Source-warm mode | Runs | Phase 02 fixture median, s | Phase 03 fixture median, s | Phase 03 process CPU median, s | Phase 03 whole-process median, s |
|---|---:|---:|---:|---:|---:|
| Legacy diagnostic | 4 | 0.955995 | 0.945090 | 0.914063 | 2.617058 |
| Common/cache off | 4 | 0.948194 | 0.941130 | 1.000000 | 2.625772 |
| Compiled first build | 2 | 0.968989 | 0.967920 | 1.000000 | 2.716705 |
| Compiled restart hit | 4 | 0.944560 | 0.943602 | 0.976563 | 2.589757 |

Common preparation including PNG median: 20.3236 ms; compiled first: 36.7011 ms; compiled hit: 16.9878 ms. Legacy separates serialization, so its differently scoped terrain stage is not compared with those values. Both first builds called the PNG encoder once; all four fresh-process hits called no encoders. Warm runs made zero fake transport requests; each source-cold seed made four. Cold fixture wall range was 1.581306–1.600932 s (median 1.591119 s); phase 01 cold median was 1.584056 s. Phase 01 warm median 0.929796 s used a slightly different fixture import boundary; matched phase 02/03 comparisons are preferable.

All 16 processes exited zero. Lifetime working-set peaks were **87,408,640–91,172,864 bytes**; peak process commit **606,773,248–618,635,264 bytes**, including imports/setup. These are measured process peaks for the small fixture, not queue reservation proof, engine RAM or VRAM. The compiled artifact remained 54,407 bytes, two request-local fact cells, provisional quality, open halo and no persisted decision plan. Uncertainty was not promoted to final coverage.

This world-build diagnostic **bypasses Connection/admission flow**. It establishes output/regression comparability and retained compiled reuse; it does not measure streaming latency, game hitches or safe drivability. Close medians, coarse CPU accounting, small samples and uncontrolled background work do not establish a general speedup. Phase 01 guardrails remain provisional; no performance budget was tuned from these results. The prior phase-02 separate 12-product restart probe was not rerun or counted here; current preparation parity tests do check the product family.

Raw evidence: `validation/measurements/results.json` (all argv/env/cache states/probes), `validation/measurements/summary.json` (comparisons and memory), and `validation/run_measurements.py` / `summarize_measurements.py`. Reproduce with a fresh label after correctness checks, with the environment above:

```powershell
& $pythonExe "$evidence\validation\run_measurements.py" replay-matched
```

## Agent ledger

Host-returned canonical task names serve as actual task IDs. Project defaults request Sol/medium for the lead, but the active lead model/effort cannot be introspected or switched by prose. Host-defined role settings below are available; separate runtime model telemetry is unavailable. No recursive delegation; at most three active subagents plus lead. Lead alone owns Git mutations.

| Task ID | Role/settings and ownership | Contribution |
|---|---|---|
| `/root/streaming_map` | earth_explorer, Sol/medium; read-only | Traced reserved pools, queue/admission boundaries, provider rate policy, Lua import accounting and release protections. Identified tile future identity and nav receipt gaps. |
| `/root/streaming_impl` | earth_specialist, Sol/high | Owned coordinated Python/Lua production and assigned lifecycle/protocol tests; fixed routed stale publication, duplicate, source geometry, road receipt and photo backlog findings. Passed author checks; no benchmarks/Git/engine. |
| `/root/race_review` | earth_reviewer, Astra/high; read-only | Reproduced defects using production methods/modules, challenged reservation and receipt contracts, independently verified final fixes, source manifest and core socket behavior. External scripts/report only. |
| `/root/baseline_validation` | earth_validator, Sol/high; external harness/reports only; queued after explorer completion | Independently verified staged 528 Python cases/1,362 Lua assertions, event/barrier admission probes and 16 serialized matched processes; retained intermediate failures and exact evidence. No source edits/Git/engine. |
| `/root` | Lead; `workcontrol.py`, its tests, shared contract/configuration, integration and handoff | Admission contract, integration/diff inspection, Lua scope finding; explicitly took ownership of final terrain-ACK/prefetch and missing-road-receipt fixes/tests after specialist froze. |

Context7 primary reference shared by specialist/reviewer: `/python/cpython` (High reputation; no indexed 3.12-specific ID), [concurrent futures](https://github.com/python/cpython/blob/main/Doc/library/concurrent.futures.rst), [asyncio tasks](https://github.com/python/cpython/blob/main/Doc/library/asyncio-task.rst), [streams](https://github.com/python/cpython/blob/main/Doc/library/asyncio-stream.rst). Cancellation of a future does not stop a running callable; `drain()` provides cooperative flow control rather than complete independent-write byte admission.

## Acceptance limits and next action

No remaining demonstrated blocker was found in the independently reviewed standalone slice. This does **not** close the full engine/retained-memory gate. Admission estimates, far pending bytes and committed cache limits do not bound total RSS/VRAM, JSON encoding temporaries, loaded-but-not-activated objects, retained old engine objects, or held session files. Those live resources remain acknowledgment-protected and uncapped by this change. Queue limits are provisional safety limits, not hardware-calibrated performance promises.

Persistently changing partial DEM inputs exhaust three terrain attempts and return retry error rather than publish stale support. Stable missing-data fallbacks are not certified coverage. Cross-process provider-complete identity remains unavailable; uncertain bridge navigation is withheld. Failed phase-2 rebase terrain import retains the existing coordinate-transform behavior, but sends no successful terrain acknowledgment/receipt and withholds navigation; full engine rollback is unverified.

Next action: use an unoccupied, explicitly authorized engine test setup with this matched companion/mod; record game version, hardware, settings, cold/warm state, safe-drivability time, frame tails/stalls, collision reload and failed-import behavior, bridge/tunnel driving, rebase and delayed release. Observe actual retained RAM/VRAM/object/file bytes and determine practical hard retention policy before claiming full memory bounds. Do not interrupt the other session or start phase 04. No live game/Blender setup was inspected or changed.
