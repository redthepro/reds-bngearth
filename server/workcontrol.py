"""Nonblocking admission reservations, independent of executor cancellation.

Bytes are conservative work estimates, not measurements of process RSS.  The
owner must finish a lease only after its underlying callable has stopped.
"""
from concurrent.futures import CancelledError
from threading import RLock


class Lease:
    def __init__(self, budget, key, lane, size):
        self._budget = budget
        self.key, self.lane, self.estimated_bytes = key, lane, size
        self._cancelled = False
        self._finished = False

    @property
    def cancelled(self):
        with self._budget._lock:
            return self._cancelled

    @property
    def finished(self):
        with self._budget._lock:
            return self._finished

    def cancel(self):
        # Signalling cancellation does not free the running worker's memory.
        with self._budget._lock:
            self._cancelled = True

    def checkpoint(self):
        if self.cancelled:
            raise CancelledError()

    def finish(self):
        with self._budget._lock:
            if self._finished:
                return
            self._finished = True
            self._budget._leases.pop(self.key, None)
            self._budget._jobs[self.lane] -= 1
            self._budget._bytes[self.lane] -= self.estimated_bytes


class Budget:
    """Separate critical/visual capacities: optional saturation cannot borrow
    critical reservations. Existing executor pools still own scheduling.
    """
    def __init__(self, visual_jobs=8, visual_bytes=128 * 1024**2,
                 critical_jobs=4, critical_bytes=64 * 1024**2):
        values = (visual_jobs, visual_bytes, critical_jobs, critical_bytes)
        if any(type(v) is not int or v <= 0 for v in values):
            raise ValueError('admission capacities must be positive integers')
        self._limits = {'visual': (visual_jobs, visual_bytes),
                        'critical': (critical_jobs, critical_bytes)}
        self._lock = RLock()
        self._leases = {}
        self._jobs = dict.fromkeys(self._limits, 0)
        self._bytes = dict.fromkeys(self._limits, 0)

    def try_acquire(self, key, lane, estimated_bytes):
        if lane not in self._limits:
            raise ValueError('unknown admission lane')
        if type(estimated_bytes) is not int or estimated_bytes <= 0:
            raise ValueError('estimated_bytes must be a positive integer')
        with self._lock:
            count_limit, byte_limit = self._limits[lane]
            if (key in self._leases or self._jobs[lane] >= count_limit or
                    estimated_bytes > byte_limit - self._bytes[lane]):
                return None
            lease = Lease(self, key, lane, estimated_bytes)
            self._leases[key] = lease
            self._jobs[lane] += 1
            self._bytes[lane] += estimated_bytes
            return lease

    def snapshot(self):
        with self._lock:
            return {lane: {'jobs': self._jobs[lane], 'bytes': self._bytes[lane],
                           'max_jobs': limits[0], 'max_bytes': limits[1]}
                    for lane, limits in self._limits.items()}
