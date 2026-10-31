# Earth 2 integration candidate

This candidate integrates the isolated Earth 2 development snapshot with fork main `320175c3b6db5fc8452a7a2fce5f105e48a0e384` (1.8.4). The original Earth 2 base is `aae4d6126d65a07315d0b28cc4a81dd68d86280a`; preservation commit `9293cc4` retains all prior source work. Both histories are preserved by a merge.

## Testing the candidate

Use the GitHub prerelease artifacts for `1.8.5-rc.1`; see the repository's `docs/QUICKSTART.md` for normal installation. The installer writes the selected BeamNG user directory. Choose a separate test user directory when protecting an existing installation. No installed game files or active game session were changed during integration.

Shared terrain preparation is enabled. `BNGE_SHARED_TERRAIN=off` selects the retained legacy preparation path for diagnosis. Persistent compiled-terrain reuse is opt-in with `BNGE_COMPILED_TERRAIN=1`; the compiled cache is bounded and its bytes are checksummed. It does not persist every Grader decision, and the fact halo remains incomplete. No engine performance or drivability improvement is established by Python tests.

The semantic data adapters consume prepared evidence where available and retain worldwide fallbacks. The asset preparation/acquisition tools run offline from gameplay. The deterministic semantic asset resolver is development groundwork: it has no production renderer caller and no newly prepared Sketchfab model is runtime-approved. Do not expect a new Sketchfab scene from this candidate.

## Integration decisions

- Retain main's mapped signals, railway meshes, gas station pads/canopy exclusions, graded building placement, warm data fetches, shared cellfacts and revisit cache helpers.
- Carry generation/worker ownership, collision support and stale activation checks into those paths. Bump combined road-layer schema to 7 to invalidate earlier normalized caches.
- Carry fuel pads through shared terrain masks and upstream `ground_at` through shared/provisional heightmaps.
- Permit terrain preparation before anchor commitment while suppressing responses after an anchor is retired; preserve request-version cancellation checks.
- Update stale fixtures to register tile completions, exclude internal receipt metadata from the legacy terrain comparison, and initialize new upstream fields in fakes. Do not loosen timeouts or retexture assertions.

## Validation record

The archived phase 00–07 handoffs describe the earlier snapshot, including its failed checks. They are historical evidence. The integration validation results are recorded below when complete; local native audio and engine validation are distinct from source tests. CI builds the native voice extension before running its tests. GitHub Actions tests gate executable builds and prerelease publication.

## Rollback

The pre-integration source work is preserved in `9293cc4`; upstream main at integration is `320175c`. Use a separate checkout of either revision for comparison. For an installed candidate, close its game/server and restore the previously saved mod and user-directory configuration using the installer's documented paths. Do not overwrite an active game session or delete shared caches to roll back a source experiment.

Completed local validation: 130 Python modules / 1,835 tests, 66 Lua modules, standalone Node math/social checks, compiled/rendered Vue UI and the separate synthetic 200mph delayed-terrain test passed. Native voice is built/tested in CI. Full commands and limitations: [integration handoff](handoffs/08-upstream-integration.md).
