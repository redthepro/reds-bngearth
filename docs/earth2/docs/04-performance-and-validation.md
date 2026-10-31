# Performance and validation

## Measure comparable runs

Record commit, BeamNG version, OS, CPU/GPU, RAM/VRAM, resolution, quality settings, vehicle, route, speed, seed, source revisions, and network conditions. Compare identical inputs and settings. Disable overlapping benchmark loads.

Use three cache modes:

- Cold: no prepared artifacts for the route; record download effects separately.
- Warm restart: exit and restart with the same valid artifact cache.
- Steady drive: stream ahead while driving a fixed recorded route.

Record time to safe drivability, main-thread p50/p95/p99/max frame time, hitch counts at declared thresholds, physics-support distance, artifact hit rate, CPU time by stage, bytes downloaded, queue age, cancelled/stale work, RAM/VRAM, and live scene/collision counts.

Do not set arbitrary hardware-independent promises. After phase 01, write explicit budgets based on the target machine and desired frame rate. A 60 Hz target gives a 16.67 ms total frame; it does not grant the mod that entire budget. Proposed initial acceptance: correctness must not regress, and optimization needs a measurable gain without hiding a material regression elsewhere.

## Correctness cases

1. Adjacent cells, corners, repeated requests, and deterministic ownership.
2. Bridges and tunnels: visuals, support, grading, and navigation agree.
3. World rebase: vehicles, cells, landmarks, normals, and navigation remain aligned.
4. Missing DEM, partial sources, no-data raster cells, schema changes, and provider outages.
5. Latitude extremes, antimeridian, hemispheres, metric units, and vertical datums.
6. Interrupted downloads/writes, corrupt cache, expired sources, and upgrade invalidation.
7. Late cancelled jobs, duplicate messages, memory pressure, and slow engine installation.
8. Regional providers disabled: worldwide base still produces useful output.
9. Asset transforms: handedness, scale, orientation, winding, pivot, material mapping.
10. Model removal/license exclusion: procedural fallback works without missing collisions.

## Benchmark locations

The [scenario configuration](../config/benchmark-scenarios.json) gives candidate centers, not surveyed drivable routes. Resolve a safe existing road segment during baseline. Include dense urban, rural/forest, arid, tropical, mountainous, high-latitude, and isolated cases. A synthetic antimeridian fixture covers the seam without pretending a road crosses the ocean.

## Test layers

Run the existing focused Python tests first, plus regression tests for changed geometry, caching, and protocol behavior. Use deterministic tiny fixtures and mocked provider responses in ordinary tests. Keep live provider smoke tests opt-in.

Check compiled kernels with relevant Rust tests only when changed. Use actual BeamNG probes for collision and engine integration; Python tests cannot establish those properties.

Source documentation includes older engine observations. Verify them against the user's actual installed version before assuming multiple terrain physics blocks, background object creation, collision rebuild costs, instancing support, or glTF loading behavior.

If the game is occupied by the other session, complete standalone validation and prepare an explicit in-game test procedure. Report engine checks as pending rather than interrupting that session or inventing results.
