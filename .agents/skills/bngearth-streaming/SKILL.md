---
name: bngearth-streaming
description: Diagnose and improve BeamNG.Earth loading, camera/driving priorities, CPU and network throughput, memory budgets or native SIMD performance.
---

# Streaming and hardware scaling

Work from a specific delay, drive log or measurable hypothesis. Separate download queue time, HTTP time,
decode/generation, IPC, game imports and collision commits before changing concurrency or languages.

## Code map

- Scheduling/feedback: `server/earthd.py`, `server/streaming.py`, `server/pacer.py`, `server/budget.py`, `server/cpupin.py`.
- Work/data: `server/blockworker.py`, `server/netfetch.py`, `server/dem.py`, `server/imagery.py`, `server/revisit.py`.
- Game import budgets: Lua `core.lua`, `governor.lua`, `streaming.lua`, `farfield.lua`, `follow.lua` in
  `mod/beamng_earth/lua/ge/extensions/beamngEarth/`.
- Kernels: `native/earthfast/src/lib.rs`, `simd.rs`, `gpu.rs`; Python API checks in `server/fast.py`.

## Make the change

1. Establish the slow stage and record hardware, driving speed, cache state, worker limits and graphics/frame cap.
   Prefer a controlled fixture or existing log before a live download benchmark.
2. Preserve driving-corridor priority, camera visibility and warm reserve together. Camera changes may reorder
   visual work, but cannot remove collision under the car or force a blank scene during a normal turn.
3. Keep follow collision blocks independent of slow imagery. Preserve progressive texture replacement and cancellation
   when anchors/sessions change. Cache only complete, compatible results and keep source/material identity intact.
4. Scale optional work down for sustained frame pressure or low RAM, then recover gradually. Keep queues bounded and
   leave CPU time for BeamNG. Increasing network concurrency requires evidence of available decode/generation capacity
   and respect for source backoff/rate behavior.
5. For SIMD, retain feature detection and scalar output parity. Benchmark supported AVX2/AVX-512 paths rather than
   assuming the wider instruction set wins. Match the Rust API/version and both platform binaries when it changes.

## Validate

Use `test_streaming`, `test_budget`, `test_prewarm`, `test_detail`, `test_retex`, `test_simd` and relevant Lua tests.
`python server/tests/bench_loading.py --delay 1 --repeat 3` isolates cosmetic waits with synthetic data.
For coverage changes, run `test_integration_drive.py` filtered to `test_200mph_with_slow_heightmaps` with
`BNGE_SKIP_DRIVE=0` and `BNGE_TEST_OFFLINE=1`. Include jitter, turns/rebases and low-memory behavior when affected.

Report measured stage improvements and remaining bottlenecks. An offline 200 mph scenario supports coverage under
its controlled delays; it is not a guarantee for every live location, network or PC.
