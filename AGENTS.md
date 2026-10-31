# Working on Red's BeamNG.Earth fork

This file applies throughout the repository. Follow the user's current request and any more specific directory
instructions. Read `README.md`, `VERSION` and the relevant code before relying on historical design documents.
Do not put credentials, private player data or machine-specific secrets in repository instructions.

## What runs where

| Component | Responsibility | Entry points |
| --- | --- | --- |
| Companion, Python | Downloads/caches map data, schedules workers, builds terrain/roads/buildings, serves local game IPC | `launcher.py`, `start.py`, `server/earthd.py` |
| Game extension, Lua | Imports scene objects, manages collision blocks/rebases, budgets frame work, integrates BeamMP | `mod/beamng_earth/lua/ge/extensions/beamngEarth/core.lua` |
| Game UI, Vue/JavaScript | Map, settings, server browser, social/voice panel | `mod/beamng_earth/ui/ui-vue/mods/beamngEarth/` |
| Native terrain, Rust/PyO3 | CPU kernels, runtime SIMD dispatch and optional GPU compute | `native/earthfast/` |
| Native voice, Rust/PyO3 | CPAL devices/streams, Opus and bounded audio mixing | `native/earthvoice/`, `server/voice*.py` |
| BeamMP plugin | Authoritative connected players, anchors, map visibility, voice groups/session identity | `beammp/Server/BeamNGEarth/main.lua`, `lib/voice.lua` |
| Hosted voice service | Mumble authentication, groups and routing policy | `deploy/voice/` |

The companion generates the world on each player's PC. BeamNG renders it and simulates vehicles. The BeamMP and
voice services do not run every player's terrain generation or vehicle physics. Game coordinates use a local ENU
frame; geographic/ECEF coordinates survive origin changes. Keep rebases, teleports and session identity explicit.

`docs/ARCHITECTURE.md` and older specifications contain valuable probe results, but some defaults and target game
versions are historical. Verify current behavior in code, tests and the README.

## Choose a repository skill

Read only the skills relevant to the task. Paths are relative to this repository and travel with a clone.

| Task | Skill |
| --- | --- |
| Loading, CPU/network throughput, hardware scaling, SIMD, camera priorities | [.agents/skills/bngearth-streaming/SKILL.md](.agents/skills/bngearth-streaming/SKILL.md) |
| Roads, bridges, buildings, landmarks, scenery and lighting | [.agents/skills/bngearth-world-geometry/SKILL.md](.agents/skills/bngearth-world-geometry/SKILL.md) |
| Voice devices, UI, map visibility and private groups | [.agents/skills/bngearth-social-voice/SKILL.md](.agents/skills/bngearth-social-voice/SKILL.md) |
| Upstream merges, versions, CI, executable packaging and releases | [.agents/skills/bngearth-release-integration/SKILL.md](.agents/skills/bngearth-release-integration/SKILL.md) |
| BeamMP/Mumble installation, Podman and an authorized live update | [.agents/skills/bngearth-server-ops/SKILL.md](.agents/skills/bngearth-server-ops/SKILL.md) |
| New ideas, prototypes, architectural alternatives and expansion | [.agents/skills/bngearth-feature-lab/SKILL.md](.agents/skills/bngearth-feature-lab/SKILL.md) |

## Working agreements

- Inspect `git status`, the current branch, remotes and `git worktree list` before editing. Different worktrees
  share Git history, but each has its own checkout, ignored binaries and build environment. Preserve unrelated edits.
- The fork normally publishes through `main`. Follow the user's requested branch/PR/push workflow; do not force-push,
  rewrite release tags or treat this file as authorization to change a live server.
- Use `rg` for discovery. Keep changes focused, and use existing layers before adding another service or language.
- Keep fork improvements and upstream attribution. An upstream merge needs semantic review of automatically merged
  scheduling, cache, geometry, protocol and packaging code as well as textual conflicts.
- Keep README claims tied to evidence. Distinguish implementation, automated simulation, local game verification
  and multiplayer/load testing. A successful mic meter test does not establish that another player heard audio.
- Update the README for user-visible behavior and `CHANGELOG.md` for releases. Read `VERSION`; do not infer the current
  release from these instructions. Documentation-only changes normally keep the application version.
- Complete authorized work and report what changed, checks performed, delivery commit and any remaining limits.
  Ask for missing information only when it affects the task and cannot be discovered from the project.

## Runtime invariants

- Collision coverage and driving continuity take precedence over cosmetic work. Camera prioritization must retain
  nearby ground and a surrounding reserve for turns. Avoid making collision readiness depend on a satellite photo.
- Respect CPU/RAM budgets, the frame governor, worker ownership, queue limits and cancellation. More worker threads
  or downloads are useful only when measurements show the bottleneck and the game remains responsive.
- Keep terrain profiles, visual roads, collision geometry and props consistent. Retain courtyard holes and all
  multipolygon components through LOD generation and rebases. Replace custom visuals without deleting mapped collision.
- Runtime SIMD must keep portable fallbacks and match scalar output. Do not ship a generic executable that requires
  AVX-512 or build all native code with a machine-specific CPU target.
- Voice is opt-in per multiplayer session; global is separately joined and off by default. Capture is active only
  for granted push-to-talk or an explicit local test. Preserve short leases, focus-loss/release cancellation and bounds.
- Group membership and invitations use authoritative connected server IDs. Never trust a client-supplied identity
  or merge players by display name. Map visibility is an opt-in UI feature, not anonymity from game synchronization.
- `bngApi.engineLua(expression, callback)` requires an expression. Use `socialBridge.mjs` for social-panel calls;
  a raw `local ...; return ...` statement block breaks the callback wrapper. Cover this boundary with Lua execution.
- Keep native audio/network work outside game Lua callbacks. Enumerating devices must not open microphone streams.
- Preserve credential validation, certificate pinning, authenticated peer work and update checksum/repository checks.
  Never log raw voice exceptions that could contain session credentials.

## Development and verification

Use a repository virtual environment with `server/requirements.txt`; Lua tests also need LuaJIT or `lupa`.
Commands below use `python`; activate the correct environment or substitute its executable.

| Change | Relevant starting checks |
| --- | --- |
| Python behavior | `python tools/run_tests.py test_<module> --par 2` |
| Lua behavior | `python tools/run_lua_tests.py` (accepts individual `test_*.lua` names) |
| Social UI/bridge | `node mod/tests/test_social.mjs`, `node tools/check_social_ui.mjs`, Python `test_social_bridge` |
| Native voice | `cargo test --locked --manifest-path native/earthvoice/Cargo.toml`, Python `test_voice` |
| Native terrain/SIMD | `cargo test --locked --manifest-path native/earthfast/Cargo.toml`, Python `test_simd` |
| Packaging | Python `test_packaging`; build and run the packaged `--server --help` when packaging changes |

For UI compilation, install the versions used in `.github/workflows/tests.yml` under the ignored
`build/social-ui-check` prefix. For the full offline Python suite, set `BNGE_TEST_OFFLINE=1` and `BNGE_SKIP_DRIVE=1`,
then run `python tools/run_tests.py --par 2 --timeout 1500`. Preserve the running command/session ID and inspect failures
in `build/test-logs` (or the selected `--logs` directory). Network and long drive cases are separate.

Run checks proportionate to the change. Reproduce behavior bugs with meaningful regression cases; wait for actual
asynchronous conditions rather than arbitrary sleeps. Documentation edits need link/path/content checks, not a new
unit test or a manual full compilation. Do not edit source underneath a long-running test suite.

`python tools/build_native.py` builds terrain and voice modules. On Windows, CMake must be on PATH for bundled Opus.
Tracked terrain binaries are `server/earthfast.pyd` and `server/earthfast.abi3.so`; an API/version change needs compatible
builds for both platforms. `server/fast.py` checks the minimum API version. Voice binaries and `build/` outputs are ignored;
CI builds them from source. Never overwrite a loaded native library in place; use the install helper's replace operation.

## Releases and operations

Every push to `main` starts `.github/workflows/release.yml`; Windows/Linux tests gate builds and publication.
The outputs are a Windows EXE, Linux AppImage, matching BeamMP server ZIP and `SHA256SUMS.txt`.
Use the release skill for version/tag rules, in-flight builds and artifact verification. Report CI as pending until
the relevant run succeeds; a local test EXE is not an official published release.

Use the operations skill only for requested hosting work. Check connected players before an interrupting update,
back up replaced resources outside `Resources`, and preserve `ServerConfig.toml`, its AuthKey and other mods.
Private voice registry/configuration must never be placed in client downloads. Microphone tests require user action.
