#!/usr/bin/env python3
"""Compare portable/AVX2/AVX-512 DEM sampling on this PC, in isolated processes.

    python tools/bench_native.py

Reports kernel time, not game FPS. BNGE_SIMD is read once per process; unsupported
instruction sets safely fall back. Automatic dispatch briefly compares supported kernels.
Each backend must match the NumPy reference bit for bit. Default runs rotate backend
order across three fresh-process rounds to reduce warm-up/load bias.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def run_one(quick=False):
    import numpy as np
    sys.path.insert(0, str(ROOT / 'server'))
    import_start = time.perf_counter()
    import earthfast
    import_ms = (time.perf_counter() - import_start) * 1000
    rng = np.random.default_rng(512)
    grid = rng.normal(200, 100, (514, 514)).astype(np.float32)
    cases = {}
    for n in (64, 256, 512):
        x, y = np.meshgrid(np.linspace(-1, 514, n), np.linspace(514, -1, n))
        x, y = x.ravel(), y.ravel()
        ix, iy = np.clip(np.floor(x).astype(int), 0, 512), np.clip(np.floor(y).astype(int), 0, 512)
        wx, wy = np.clip(x - ix, 0, 1), np.clip(y - iy, 0, 1)
        a, b = grid[iy, ix].astype(float), grid[iy, ix + 1].astype(float)
        c, d = grid[iy + 1, ix].astype(float), grid[iy + 1, ix + 1].astype(float)
        expected = (a * (1 - wx) + b * wx) * (1 - wy) + (c * (1 - wx) + d * wx) * wy
        out = earthfast.bilinear_grid(grid, x, y)
        np.testing.assert_array_equal(out.view(np.uint64), expected.view(np.uint64))
        for _ in range(5):
            earthfast.bilinear_grid(grid, x, y)
        times = []
        for _ in range(8 if quick else 60):
            start = time.perf_counter()
            out = earthfast.bilinear_grid(grid, x, y)
            times.append((time.perf_counter() - start) * 1000)
        cases[str(n)] = dict(median_ms=statistics.median(times),
                             p95_ms=sorted(times)[int(0.95 * (len(times) - 1))],
                             sha256=hashlib.sha256(out.tobytes()).hexdigest())
    return dict(backend=earthfast.SIMD_BACKEND, import_ms=import_ms, cases=cases)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--one', choices=['auto', 'scalar', 'avx2', 'avx512'])
    parser.add_argument('--quick', action='store_true')
    parser.add_argument('--rounds', type=int, default=3, help='fresh-process rounds (default: 3; --quick: 1)')
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error('--rounds must be positive')
    if args.one:
        os.environ['BNGE_SIMD'] = args.one
        print(json.dumps(run_one(args.quick)))
        return
    backends = ('scalar', 'avx2', 'avx512', 'auto')
    runs = {backend: [] for backend in backends}
    for round_index in range(1 if args.quick else args.rounds):
        shift = round_index % len(backends)
        for backend in backends[shift:] + backends[:shift]:
            cmd = [sys.executable, __file__, '--one', backend] + (['--quick'] if args.quick else [])
            runs[backend].append(json.loads(subprocess.check_output(cmd, text=True, timeout=60)))
    results = {}
    for backend, rounds in runs.items():
        result = dict(backend=rounds[0]['backend'], selected_backends=[r['backend'] for r in rounds],
                      import_ms=statistics.median(r['import_ms'] for r in rounds), cases={})
        for size in rounds[0]['cases']:
            cases = [r['cases'][size] for r in rounds]
            assert len({c['sha256'] for c in cases}) == 1, 'terrain heights changed between rounds'
            result['cases'][size] = dict(median_ms=statistics.median(c['median_ms'] for c in cases),
                                        round_medians_ms=[c['median_ms'] for c in cases],
                                        sha256=cases[0]['sha256'])
        results[backend] = result
    for result in results.values():
        for size, case in result['cases'].items():
            assert case['sha256'] == results['scalar']['cases'][size]['sha256'], 'SIMD changed terrain heights'
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
