"""Runtime dispatch remains portable and terrain samples remain bit-for-bit identical."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'server'))
import fast


def check_edges():
    def same_bits(actual, expected):
        np.testing.assert_array_equal(actual.view(np.uint64), expected.view(np.uint64))

    def scalar_points(grid, x, y):
        # One-point calls exercise scalar arithmetic independently of selected SIMD.
        return np.array([fast._ef.bilinear_grid(grid, x[i:i+1], y[i:i+1])[0] for i in range(len(x))])

    rng = np.random.default_rng(3)
    grid = rng.normal(size=(21, 19)).astype(np.float32)
    for n in (0, 1, 7, 8, 9, 63, 64, 65, 127, 131):
        x, y = rng.uniform(-30, 40, (2, n))
        same_bits(fast._ef.bilinear_grid(grid, x, y), scalar_points(grid, x, y))
        for view in (grid[::2, ::2], grid[::-2, ::-2], np.asfortranarray(grid)):
            same_bits(fast._ef.bilinear_grid(view, x, y),
                      fast._ef.bilinear_grid(np.ascontiguousarray(view), x, y))
        same_bits(fast._ef.bilinear_grid(grid, x[::-1], y[::-1]),
                  fast._ef.bilinear_grid(grid, x, y)[::-1])
    x, y = rng.uniform(0, 15, (2, 131))
    x[:3] = [np.nan, np.inf, -np.inf]
    y[3:6] = [np.nan, np.inf, -np.inf]
    same_bits(fast._ef.bilinear_grid(grid, x, y), scalar_points(grid, x, y))
    # Numerical equality alone misses signed-zero changes; check the float bits.
    coords = np.tile([-0., 0., -1., 1., 1e-310, -1e-310, 1e30, -1e30, .1, .9], 14)[:131]
    x, y = coords.copy(), np.roll(coords, 3)
    for value in [0., -0., np.nextafter(np.float32(0), np.float32(1)), 32.25, np.nan, np.inf, -np.inf]:
        grid = np.full((2, 2), value, dtype=np.float32)
        same_bits(fast._ef.bilinear_grid(grid, x, y), scalar_points(grid, x, y))


class SimdTest(unittest.TestCase):
    def test_dispatched_backends_against_reference(self):
        results = []
        for backend in ('scalar', 'avx2', 'avx512', 'auto'):
            result = subprocess.run([sys.executable, str(ROOT / 'tools/bench_native.py'), '--one', backend, '--quick'],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            allowed = {'scalar': ['scalar'], 'avx2': ['scalar', 'avx2'],
                       'avx512': ['scalar', 'avx2', 'avx512'], 'auto': ['scalar', 'avx2', 'avx512']}
            self.assertIn(data['backend'], allowed[backend])
            results.append(data)
        for result in results[1:]:
            for size, case in result['cases'].items():
                self.assertEqual(case['sha256'], results[0]['cases'][size]['sha256'])

    def test_tails_strides_nonfinite(self):
        for backend in ('scalar', 'avx2', 'avx512', 'auto'):
            with self.subTest(backend=backend):
                result = subprocess.run([sys.executable, __file__, '--edge-cases'], capture_output=True,
                                        text=True, timeout=60, env={**os.environ, 'BNGE_SIMD': backend})
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_shape_rejected_before_native_gathers(self):
        for grid in (np.zeros((1, 4), np.float32), np.zeros((4, 1), np.float32)):
            with self.assertRaises(ValueError):
                fast._ef.bilinear_grid(grid, np.zeros(64), np.zeros(64))
        with self.assertRaises(ValueError):
            fast._ef.bilinear_grid(np.zeros((2, 2), np.float32), np.zeros(64), np.zeros(63))


if __name__ == '__main__':
    if sys.argv[1:] == ['--edge-cases']:
        check_edges()
    else:
        unittest.main()
