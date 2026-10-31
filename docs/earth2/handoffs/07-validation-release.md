# Phase 07 — Independent validation and local release candidate

Date: 2026-10-26. Status: **local candidate prepared; validation gate not passed.** This checkout is a dirty successor based on pinned source commit `aae4d6126d65a07315d0b28cc4a81dd68d86280a` (`v1.8.2`, `earth2/development`). It was already dirty from prior phases; the phase 07 validation agents made no source edits and no Git mutations were performed.

## Candidate artifact

- Local package: `%LOCALAPPDATA%\\beamng-earth-2\\benchmarks\\07-validation-release\\release-candidate\\BeamNG.Earth-1.8.2.zip`
- Package command from `source`: `python tools/package.py --out <candidate-dir>`
- Package result: 256 files, 13.3 MB.
- SHA-256: `CEC52A590F80E37CE2DE17800AC3723FEB605AB0145FB9901FB24EDFF70FCFC3`.
- Identity record: `BUILD-IDENTITY.json` beside the archive. It labels the build as local and validation-pending, records the pinned base and dirty checkout, and lists known limitations.
- Archive inspection: 256 entries; no secret-named paths and no matches for credential patterns (`sketchfab_token`, bearer authorization, or signed access-token query fields). This is a narrow archive scan, not a general source-code secret audit.
- No install, publication, upload, GitHub push, original checkout access, or live game operation occurred.

## Verification

Environment: dedicated Python 3.12.14 at `%LOCALAPPDATA%\\beamng-earth-2\\venvs\\py312\\Scripts\\python.exe`; NumPy 2.5.4. `python -m pip check` passed.

- `python tools/run_tests.py --par 2 --timeout 1500 --logs %LOCALAPPDATA%\\beamng-earth-2\\benchmarks\\07-validation-release\\python` — completed in 950 seconds; 122 modules, 117 OK and 5 reported not OK.
- `python -m unittest server.tests.test_workcontrol -v` — 5/5 passed. This resolved one runner discovery/import-layout failure; the module-import form supplies the source package root.
- Sequential reruns with `python -m unittest discover -s server/tests -p <module>.py -v` reproduced three failing modules:
  - `test_cli_default`: 1/2 failed because the golden M1 output comparison includes the added internal `_input_receipt` field.
  - `test_detail`: 2/7 timed out waiting for `terrain_ready` (60-second and 10-second waits).
  - `test_retex`: 2/21 failed to emit expected retexture messages for region-ready-during-render and retransmit-to-another-viewer cases.
- `test_voice`: one codec test could not import `earthvoice`; its native extension is absent from the dedicated environment. No toolchain installation or native build was attempted. Other voice tests passed.
- `python tools/run_lua_tests.py` — passed, 65/65 Lua modules. The keybind check only saw the package map because BeamNG was not found via `BEAMNG_DIR`.
- Python full and sequential logs, plus Lua output, are under `%LOCALAPPDATA%\\beamng-earth-2\\benchmarks\\07-validation-release`.

## Independent reviews

- Correctness review (`/root/correctness_review`, `earth_reviewer`, GPT-6 Luna / medium, read-only): no demonstrated geometry, cache invalidation, collision/navigation, or streaming lifecycle regression in inspected slices. Focused Python checks passed under Python 3.12: streaming lifecycle 25/25, world plan 8/8, navgraph 10/10, sidewalk collision 8/8. The review reaffirmed pending fact-halo / persistent Grader-plan work from phase 02, lack of engine validation from phase 03, no approved asset from phase 05, and phase 06's standalone resolver gap.
- Provenance review (`/root/provenance_review`, `earth_explorer`, GPT-6 Luna / medium, read-only): catalog admission, license evidence, attribution generation, archive bounds and fallback controls are present; no approved runtime asset exists, so model attribution is not complete for a release catalog. The review did not find a serialized credential in its targeted checks.
- Release validation (`/root/release_validation`, `earth_validator`, GPT-6 Luna / medium): ran the Python/Lua suites and generated test reports/logs under the dedicated state directory. Source was not edited.
- Lead `/root`: built and hash-identified the local archive, scanned its archive entries, reconciled handoff indexing, and recorded the result here.

## Measurements and pending gates

No isolated BeamNG environment or agreed drivable routes were available. Cold, warm-restart and steady-drive runs; identical-route comparison to phase 01; medians/tail latency; memory/frame-time comparison; visual captures; collision/drivability; and current-version engine checks remain unperformed. The phase 01 fixture measurements are not comparable to live engine performance. No improvement or production-readiness claim is made.

The release gate remains **pending** because three Python modules have reproduced failures, the native voice codec test is blocked by the missing extension, there is no approved prepared model, phase 06 placements are not production-wired or cached with the world plan, and engine/performance validation is absent. Existing procedural fallbacks remain required.

## Next action

Resolve the three reproduced Python failures with regression tests, or establish that any affected fixture is stale and update its contract without weakening coverage. Then rebuild/hash the candidate and rerun affected tests. Complete phase 06 production integration and obtain an approved asset before visual claims. Finally run the isolated BeamNG procedure against the identical phase 01 routes/settings, record machine/game/cache state, and only then evaluate the release gate.
