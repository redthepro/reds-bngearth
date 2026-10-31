# 08 — Upstream synchronization and test prerelease

Date: 2026-10-30. User explicitly authorized upstream synchronization, packaging, committing to GitHub main and starting Actions. Scope: isolated `source` clone only; original checkout/game installations were not operated on.

## Build history and version

- Original base: `aae4d6126d65a07315d0b28cc4a81dd68d86280a` (1.8.2).
- Preservation commit: `9293cc4` stores all 57 prior source paths before integration.
- Upstream fork main fetched and merged: `320175c3b6db5fc8452a7a2fce5f105e48a0e384` (1.8.4). A second fetch before delivery confirmed the same main revision.
- Delivery version: `1.8.5-rc.1` with matching changelog, to create a test prerelease through the existing main-push workflow.
- Merge preserves both histories; no force push or history rewrite. The final commit is identified by the GitHub main history and the local release BUILD-IDENTITY.json.

## Review and fixes

Reviewed terrain/cache/streaming, asset/provenance adapters, upstream overlap, workflow and packaging. Six conflicted shared files were reconciled. Retained upstream gas stations/canopy exclusion/pad masks, rail meshes, mapped signals, graded building placement, warm requests, cellfacts and far-geometry revisit helpers. Combined road schema version is 7.

Corrected a real terrain reply bug: preparation before anchor activation must return its response; completion for a retired anchor is suppressed. Added three activation tests. Updated retexture fixtures to register current in-flight futures and the legacy CLI fixture to exclude private input-receipt metadata. Updated test fakes for new upstream fields and corrected workcontrol discovery imports. Assertions/timeouts were not weakened.

Focused tests exposed and resolved integration issues: dropped worker far-cache helper definitions; missing OSM math import; optional OSM name-column handling; worker road-tile paths retaining obsolete building/sign/rail behavior; and stale fakes. Broad validation then ran on the stable corrected source.

## Validation

Dedicated Python 3.12.14 under `%LOCALAPPDATA%\beamng-earth-2\venvs\py312`. Environment: `BNGE_TEST_OFFLINE=1`, `BNGE_SKIP_DRIVE=1`, dedicated TEMP/TMP. Logs: `%LOCALAPPDATA%\beamng-earth-2\benchmarks\08-upstream-integration`.

- `python tools/run_tests.py <all discovered modules except test_voice> --par 2 --timeout 1500 --logs <state>/python-modules`: **130 modules OK, 0 failed; 1,835 tests**, 768 seconds. The frozen integrated working tree was tested, based on preservation HEAD 9293cc4 with the upstream merge and fixes applied; the preservation commit alone is not the tested tree. Packaging tests 2/2 included. Native voice extension is absent locally; unchanged voice paths are validated by CI after its native build.
- `BNGE_SKIP_DRIVE=0 python -m unittest discover -s server/tests -p test_integration_drive.py -k test_200mph_with_slow_heightmaps -v`: **1/1 passed**, 40.544 seconds. Synthetic 89.408 m/s client, delayed heightmaps, 8 commits and 1 rebase. This is a simulated protocol/client check, not BeamNG engine validation.
- `python tools/run_lua_tests.py`: **66/66 modules passed**.
- `node server/tests/test_mapmath.mjs`, `node mod/tests/test_mapmath.mjs`, `node mod/tests/test_social.mjs`: passed.
- `node tools/check_social_ui.mjs`: passed after installing CI-pinned Vue/compiler/server-renderer 3.5.22 in the isolated state directory (linked through ignored build/social-ui-check). Social/settings/map templates compiled; social panel rendered with escaped player names.
- `python -m compileall -q server tools`: passed.
- `git diff --check` and staged diff check: passed (line-ending normalization warnings only).
- Credential-pattern scan of source/docs: zero matches. ZIP integrity/path/credential-pattern scans passed.

## Local packages

`python tools/package.py --out <state>/release --beammp` produced:

- `BeamNG.Earth-1.8.5-rc.1.zip`: 298 files, 15,219,127 bytes, SHA256 `829a23ff17d9392e7fa6875261fc61c1c0a448ac292b61308262f1aa9eeadcba`.
- `BeamNG.Earth-BeamMP-Server-1.8.5-rc.1.zip`: 9,587,391 bytes, SHA256 `1583fb1cc7d5ed96d9406eeb79b3b8ee6bed2a7dd799de9882a963a64aed82b6`.

These are local source/server packages. Actions builds the Windows EXE/Linux AppImage and matching server package; publication remains gated by its checks.

## Remaining limitations

No live engine, visual capture, drivability, or same-route frame-time comparison was performed. Fact-halo and persisted Grader decision-plan work remains incomplete. Semantic asset resolver has no production caller; no new prepared Sketchfab model is approved. Historical handoffs 00–07 are archived here and are superseded for local regression status by this integration record. Experimental compiled-terrain reuse remains opt-in.

## Agent ledger

- `/root/successor_code_review`: earth_reviewer, Luna/medium; read-only original and post-merge correctness review, no new demonstrated source defect; reaffirmed earlier failures and pending engine scope.
- `/root/successor_assets_review`: earth_explorer, Luna/medium; read-only asset/data/provenance audit, no new security defect; identified production-integration/approved-asset gaps retained in documentation.
- `/root/merge_geometry`: earth_specialist, Luna/medium; owned blockworker/navgraph/osmtags/mapdata/roads conflict resolution; no Git mutations. Lead tests and follow-up review found omissions, which lead corrected and revalidated.
- `/root/integration_validation`: earth_validator, Luna/medium; owns reports/logs only; independently ran broad suite, synthetic drive and Lua/node checks.
- Lead `/root`: GPT-6.1 Sol; owns integration, shared terrainprep, fixtures/new activation tests, Git operations, package verification and delivery.

## Rollback and next step

Both pre-integration revisions remain in history. Use separate checkouts for comparisons; do not reset the original active checkout. See ../INTEGRATION.md for candidate setup and limitations. Follow Actions for the pushed main commit, then test the prerelease in an isolated BeamNG user directory and record real engine results before claiming production readiness.
