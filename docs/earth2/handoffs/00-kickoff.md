# 00 — Isolated workspace kickoff

Date: 2026-10-26 (America/New_York). Scope: phase 00 only. Operational acceptance gate passed: isolated pinned checkout, usable dedicated environment, grounded next-phase commands, three distinct subagent contributions and independent verification. Exact lead model/effort remains unconfirmed by host telemetry.

## Source and changes

- Package: `C:\Users\Culle\OneDrive\Desktop\beamng-earth-2`.
- Isolated source: `C:\Users\Culle\OneDrive\Desktop\beamng-earth-2\source`.
- Origin: `https://github.com/redthepro/reds-bngearth.git`.
- Starting/current commit: `aae4d6126d65a07315d0b28cc4a81dd68d86280a`, exactly the revision in `docs/06-repository-baseline.md`; tag `v1.8.2`; branch `earth2/development`.
- Setup ran `& .\scripts\Bootstrap-Workspace.ps1` when source was absent. The inspected script clones without checkout, switches to the exact reviewed revision on a new branch, verifies HEAD, and refuses to overwrite an existing destination. No upstream revision was adopted.
- Created dedicated state directories `cache`, `runtime`, `benchmarks`, `assets`, plus `venvs\py312`, `runtime\tmp`, and pip cache under `C:\Users\Culle\AppData\Local\beamng-earth-2`.
- Created this handoff. No production source or shared configuration changes. No commits/pushes, original checkout operations, mod installation, game/Blender control, credentials, geographic datasets, or Sketchfab model downloads.

The source has its own Git directory under `source\.git`; it is not a worktree of the original project. Initial `git -C source status --porcelain=v1` was empty. The original checkout was deliberately not inspected; its untouched status follows from the bounded operations, not a comparison of its files.

## Runtime and Windows reconciliation

The repository requires Python 3.10+ (`server/requirements.txt`, README). Its Windows/Linux CI explicitly selects Python 3.12 (`source/.github/workflows/tests.yml`), making 3.12 the grounded choice. `py -0p` lists only system Python 3.14.7; no compatibility assumption was made for it.

Selected existing bundled interpreter:
`C:\Users\Culle\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe` — verified Python 3.12.14.

Dedicated environment:
`C:\Users\Culle\AppData\Local\beamng-earth-2\venvs\py312\Scripts\python.exe`.

Executed from the package in PowerShell:

```powershell
$stateRoot = Join-Path $env:LOCALAPPDATA 'beamng-earth-2'
$envTarget = Join-Path $stateRoot 'venvs\py312'
# Created only because the target was absent; existing environments are preserved.
& 'C:\Users\Culle\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m venv $envTarget
$env:TEMP = Join-Path $stateRoot 'runtime\tmp'
$env:TMP = $env:TEMP
& (Join-Path $envTarget 'Scripts\python.exe') -m pip install --disable-pip-version-check --only-binary=:all: --cache-dir (Join-Path $stateRoot 'cache\pip') -r source/server/requirements.txt lupa
```

Both commands exited 0. Wheel installation resolved numpy 2.5.4, Pillow 13.0.0, duckdb 1.5.6, fsspec 2026.9.0, certifi 2026.10.23, lupa 2.8. This is an observed environment, not a new project dependency lock. DuckDB's repository restriction `>=1.5,<2` was preserved. No source builds were attempted.

`earthfast` and `earthvoice` declare Rust minimum 1.89; `earthfast` uses edition 2021 and PyO3 0.25 with `abi3-py310`. Setup observed rustc/cargo 1.98.1, stable `x86_64-pc-windows-msvc` (LLVM 22.1.8). The checkout carries `server/earthfast.pyd` (5,913,600 bytes). `server/fast.py` requires native version >=69; the old Python kernel twins are gone. Do not infer a fallback from the stale Cargo description.

`python tools/build_native.py` builds both terrain and voice modules. CMake is needed for bundled Opus; `cmake`, `cl`, `link`, and standalone `luajit` were not found on PATH. This does not prove MSVC tools are absent, but a native rebuild is unverified. No unrelated toolchains were installed. Lua tests can use the installed `lupa.luajit21` through the repository runner.

No nested `AGENTS.md` was found by `rg --files --hidden source -g AGENTS.md`. Source requirements/build/test instructions were read in `README.md`, `server/README.md`, CI, `native/earthfast/Cargo.toml`, `native/earthvoice/Cargo.toml`, and tools runners. Linux `python3`, POSIX environment assignments and default game/cache paths must be translated to PowerShell, direct environment interpreter calls and dedicated local paths. Do not run `install.py`, copy the mod, or start the server/game as part of this phase.

## Implementation map and dependencies

| Existing components | Retain / next inspection |
|---|---|
| `server/earthd.py` Server/Connection, `blockworker.py` build/roads_tile/navgraph | Existing orchestration and worker boundaries; preserve separate critical-work pools. The source retains heightmap/base/urgent-base/ground/UI/tile/delta/horizon/ahead reservations. |
| `roads.py`, `grading.py`, `navgraph.py` | One authoritative road/support plan for visuals, collision and navigation; bridge failure and seam invariants. |
| `plancache.py` start_run/PlanCache, `terraincache.py` | Keep raw/memory-mapped reuse, versions/digests; preserve per-run protection against incomplete DEM decisions until safe dependency invalidation exists. |
| `streaming.py` priorities/worker_limit, `protocol.py`, `budget.py` | Existing priority, backpressure, request/anchor IDs, prepare/commit and release acknowledgments. |
| `mapdata.py`, `netfetch.py`, `osmtags.py`, `dem.py`, `maprasters.py`, `vegetation.py` | Extend adapters and worldwide fallbacks, with explicit no-data, CRS, vertical datum, units, timestamps, confidence, licenses and provenance. |
| `native/earthfast`, `server/fast.py` | Required native kernels; measure bottlenecks before changing language boundaries. |
| `mod/beamng_earth/lua/ge/extensions/beamngEarth/core.lua`, adjacent governor/streaming/rebase | Engine installation, collision transactions, frame budgets and transforms. |

Phase dependencies: 00 setup → 01 baseline → 02 shared plan/cache → 03 streaming → 04 providers → 05 catalog; 06 placement requires both 04 and 05; 07 validation/release requires prior evidence. Phase 01 is next; no phase 01 instrumentation or optimization was implemented here.

Package `config/*.json` and `.env.example` are templates/reference names, not runtime settings automatically consumed by the current mod. The seven location centers plus antimeridian fixture in `config/benchmark-scenarios.json` are candidates, not surveyed driving routes. Budgets remain unmeasured.

## Verification commands and evidence

Setup executed successfully:

```powershell
git -C source rev-parse HEAD
git -C source branch --show-current
git -C source remote -v
git -C source rev-parse --absolute-git-dir
git -C source status --porcelain=v1
py -0p
rustc -vV
cargo --version
rustup show active-toolchain
```

The independent verifier ran targeted offline checks, with logs under `%LOCALAPPDATA%\beamng-earth-2\benchmarks\00-kickoff`. Direct unittest avoids the repository-local `build/test_times.json` written by `tools/run_tests.py`.

| Actual check | Result | Measured duration |
|---|---|---|
| Environment imports | Python 3.12.14; shipped `earthfast.pyd`, `fast`, `lupa.luajit21` import successfully; exit 0 | Process 1.208 s |
| Native version supplemental probe | `earthfast.VERSION=69`, `fast.MIN_VERSION=69`, `fast.require` succeeds; exit 0 | Not separately timed |
| `pip check` | No broken requirements; exit 0 | Process 1.802 s |
| `test_world_consistency.py` | 9 tests pass; exit 0 | unittest 0.050 s; process 2.416 s |
| `test_protocol_agreement.py` | 35 tests run, OK with 1 skip; exit 0 | unittest 81.157 s; process 83.832 s |
| Post-test source inspection | HEAD unchanged; status and diff empty; independent `.git`, no `commondir` or object alternates | Not timed |

The protocol skip is `ConstantsTest.test_default_err_and_exact_level_in_lua`: it specifically requires standalone `luajit`, absent on PATH. Installed `lupa.luajit21` does not satisfy that test. These are Python/fixture checks, not game validation or performance benchmarks. Durations describe this run only, not an optimization comparison.

Literal test commands ran with working directory `C:\Users\Culle\OneDrive\Desktop\beamng-earth-2\source` using the dedicated interpreter:

```powershell
& $pythonExe -m pip check
& $pythonExe -m unittest discover -s server/tests -p test_world_consistency.py -v
& $pythonExe -m unittest discover -s server/tests -p test_protocol_agreement.py -v
```

Flags were `BNGE_TEST_OFFLINE=1`, `BNGE_SKIP_DRIVE=1`, `PYTHONDONTWRITEBYTECODE=1`, with TEMP/TMP under dedicated state. Each test process had a 240 s harness timeout. `verification.json` records exact interpreter/argv/exit/process duration; `environment.log`, `native-version.log`, `pip-check.log`, `world-consistency.log`, and `protocol-agreement.log` retain output. The package-relative commands below are equivalent reproduction commands.

Reproducible targeted commands, from the package:

```powershell
$stateRoot = Join-Path $env:LOCALAPPDATA 'beamng-earth-2'
$pythonExe = Join-Path $stateRoot 'venvs\py312\Scripts\python.exe'
$env:BNGE_TEST_OFFLINE = '1'
$env:BNGE_SKIP_DRIVE = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:TEMP = Join-Path $stateRoot 'runtime\tmp'
$env:TMP = $env:TEMP
& $pythonExe -m pip check
& $pythonExe -m unittest discover -s source/server/tests -p test_world_consistency.py -v
& $pythonExe -m unittest discover -s source/server/tests -p test_protocol_agreement.py -v
```

For the broader phase 01 baseline, repository CI uses the commands below from `source`; these are identified commands, not results from this kickoff:

```powershell
& $pythonExe tools/run_tests.py --par 2 --timeout 1500 --logs (Join-Path $stateRoot 'benchmarks\01-baseline\python')
& $pythonExe tools/run_lua_tests.py
```

The broader Python runner still writes source-local timing history even with external logs; account for that before phase 01. Do not silently interpret `BNGE_SKIP_DRIVE=1` as exclusion of every network test: also retain `BNGE_TEST_OFFLINE=1`. CI additionally includes native service/voice tests, synthetic delayed-loading drive tests, Node map math and UI checks; they were not run in phase 00.

## Documentation evidence

Context7 was actually called, resolving IDs before queries:

- Setup: `/git/htmldocs`, clone without checkout and switch at an exact start point; [primary switch documentation](https://github.com/git/htmldocs/blob/gh-pages/git-switch.adoc).
- Lead: `/python/cpython`, Windows venv and direct invocation without activation; [primary venv documentation](https://github.com/python/cpython/blob/main/Doc/library/venv.rst).
- Lead: `/websites/pip_pypa_io_en_stable`, wheel installation guidance; [primary pip guide](https://pip.pypa.io/en/stable/user_guide). The returned excerpts covered wheels and local wheel installation; actual wheel-only success is established by the command result above.

## Agent ledger

The host returned canonical task names as identifiers; no opaque UUID was exposed. No agent spawned additional agents. Setup alone owned initial Git mutations, then explicitly handed ownership to the lead. Reviewers have no source-write ownership.

| Actual task ID | Role; requested / known actual settings | Ownership and output | Review / resolution |
|---|---|---|---|
| `/root` | Requested Sol/medium; project TOML agrees; active lead model/effort cannot be reliably introspected or switched by the available tools | Integration, isolated environment, this handoff | Explicit configuration verification limitation retained. |
| `/root/setup` | `earth_worker`, Sol/medium; fixed host role advertises same; no separate runtime telemetry | Bootstrap creation of source and initial state dirs; read-only requirements/toolchain report; no production edits | Pinned clean clone; ownership transferred. Warned native builder also builds voice; handoff records CMake/Opus and unverified linker. |
| `/root/brief_review` | `earth_explorer`, Sol/medium; fixed host role advertises same; no separate runtime telemetry | No files; package-only component/dependency/preflight report | Clarified templates versus runtime settings, unsurveyed routes and missing engine evidence; incorporated above. |
| `/root/setup_check` | `earth_validator`, Sol/high; fixed host role advertises same; no separate runtime telemetry | No source edits; isolated validation logs and `verification.json` | Independently verified isolation, revision, clean status, native imports, dependencies and 44 test cases (1 skipped). Reviewed handoff; requested literal executed cwd/argv, native version, exact skip and actual timing, all incorporated. |

## Unresolved checks and next action

- Independent verification completed. Review findings about literal commands, native version, skipped Lua check and measured durations are resolved in this handoff.
- Lead runtime model/effort exact match cannot be confirmed from defaults alone.
- Native rebuild/toolchain integration, full Python/Lua/Node suites and live provider checks remain unrun.
- Actual BeamNG version, collision support, bridge transitions/navigation, seams/rebase, engine installation timing, frame-time percentiles, RAM/VRAM and asset behavior require an unoccupied, separately authorized test session. Existing package integration notes are not live connectivity evidence. No game probes were made.
- No hardware/game/settings/cache performance baseline was measured, and no engine correctness or speedup claim is made.

Next: phase 01, using this exact source commit and dedicated Python environment. First map baseline fixtures/instrumentation and agree measurement boundaries with the required phase team. Keep the original checkout and active game session protected.
