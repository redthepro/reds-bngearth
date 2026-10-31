"""Deterministic admission/cancellation invariants; no network or engine."""
import concurrent.futures
import threading
import unittest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workcontrol import Budget


class WorkControlTest(unittest.TestCase):
    def test_optional_saturation_preserves_critical(self):
        budget = Budget(visual_jobs=2, visual_bytes=10,
                        critical_jobs=1, critical_bytes=5)
        a = budget.try_acquire('a', 'visual', 6)
        self.assertIsNone(budget.try_acquire('b', 'visual', 5))
        b = budget.try_acquire('b', 'visual', 4)
        self.assertIsNone(budget.try_acquire('c', 'visual', 1))
        critical = budget.try_acquire('collision', 'critical', 5)
        self.assertIsNotNone(critical)
        for lease in (a, b, critical):
            lease.finish()
        self.assertEqual(budget.snapshot()['visual']['bytes'], 0)

    def test_cancel_does_not_release_until_worker_stops(self):
        budget = Budget(visual_jobs=1, visual_bytes=10)
        lease = budget.try_acquire('a', 'visual', 10)
        entered, resume = threading.Event(), threading.Event()

        def worker():
            try:
                entered.set()
                resume.wait(3)
                lease.checkpoint()
            finally:
                lease.finish()

        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            future = pool.submit(worker)
            self.assertTrue(entered.wait(3))
            lease.cancel()
            self.assertFalse(future.cancel())
            self.assertIsNone(budget.try_acquire('b', 'visual', 1))
            resume.set()
            with self.assertRaises(concurrent.futures.CancelledError):
                future.result(3)
        self.assertTrue(lease.finished)
        self.assertIsNotNone(budget.try_acquire('b', 'visual', 10))

    def test_dedup_and_finish_idempotence(self):
        budget = Budget()
        lease = budget.try_acquire(('anchor', 'tile'), 'visual', 9)
        self.assertIsNone(budget.try_acquire(('anchor', 'tile'), 'critical', 9))
        lease.finish()
        lease.finish()
        replacement = budget.try_acquire(('anchor', 'tile'), 'visual', 9)
        lease.finish()  # old handle cannot release the replacement
        self.assertEqual(budget.snapshot()['visual']['jobs'], 1)
        replacement.finish()

    def test_atomic_capacity_under_contention(self):
        budget = Budget(visual_jobs=3, visual_bytes=30)
        start = threading.Barrier(16)
        leases = []
        lock = threading.Lock()

        def acquire(i):
            start.wait(3)
            lease = budget.try_acquire(i, 'visual', 10)
            if lease:
                with lock:
                    leases.append(lease)

        with concurrent.futures.ThreadPoolExecutor(16) as pool:
            list(pool.map(acquire, range(16)))
        self.assertEqual(len(leases), 3)
        self.assertEqual(budget.snapshot()['visual']['bytes'], 30)
        for lease in leases:
            lease.finish()

    def test_invalid_capacities_and_sizes(self):
        for value in (True, 0, -1, 1.5):
            with self.assertRaises(ValueError):
                Budget(visual_bytes=value)
            with self.assertRaises(ValueError):
                Budget().try_acquire('x', 'visual', value)
        with self.assertRaises(ValueError):
            Budget().try_acquire('x', 'unknown', 1)


if __name__ == '__main__':
    unittest.main()
