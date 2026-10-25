"""Offline loading-policy benchmark with a controlled cosmetic delay (not a gameplay FPS benchmark).

    python server/tests/bench_loading.py --delay 1 --repeat 3

Compares the former blocking policy with progressive loading, using the same synthetic terrain and imagery.
The shadow-analysis result stays pending; only its wait expires. No live map data or game files are touched.
"""
import argparse
import concurrent.futures
import contextlib
import hashlib
import os
import statistics
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import deshadow
import earthd
import farfield
from test_server import FakeDem, FakeImagery, LAT, LON


def measure(delay, progressive):
    with tempfile.TemporaryDirectory() as root:
        s = earthd.Server(root, os.path.join(root, "cache"), imagery=None, near_visual="detail", cells=64)
        s.dem = FakeDem()
        d = s.deshadow = deshadow.Deshadow(os.path.join(root, "cache"), s.dem, wait_s=delay)
        d._enqueue = lambda rk: d._events.setdefault(rk, threading.Event())
        if not progressive:
            d.no_wait = contextlib.nullcontext  # previous far-tile rendering policy
        s.imagery = FakeImagery(os.path.join(root, "imagery"), chain=("usgs",), deshadow=d)
        try:
            a = s.new_anchor(LAT, LON + 1)
            t0 = time.perf_counter()
            tile = s.make_tile(a, farfield.Tile(2, 0, 0))
            far_ms = (time.perf_counter() - t0) * 1000
            with open(os.path.join(s.out_dir, tile.texture), "rb") as f:
                texture_hash = hashlib.sha256(f.read()).hexdigest()
            photo = concurrent.futures.Future()
            timer = threading.Timer(delay, photo.set_result, args=((None, [], delay),))
            timer.start()
            try:
                info = {"stages": {}}
                t0 = time.perf_counter()
                s._finish_heightmap(a, 0, 0, info, t0, photo, defer_base=progressive)
                block_ms = (time.perf_counter() - t0) * 1000
            finally:
                timer.join()
            return far_ms, block_ms, texture_hash
        finally:
            s.tile_pool.shutdown(wait=True)
            s.hm_pool.shutdown(wait=True)
            s.imagery.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--repeat", type=int, default=3)
    a = ap.parse_args()
    if a.delay <= 0 or a.repeat < 1:
        ap.error("delay and repeat must be positive")
    hashes = set()
    for progressive in (False, True):
        rows = [measure(a.delay, progressive) for _ in range(a.repeat)]
        hashes.update(r[2] for r in rows)
        print(f"{'progressive' if progressive else 'blocking':12} "
              f"far tile {statistics.median(r[0] for r in rows):.1f} ms; "
              f"block photo wait {statistics.median(r[1] for r in rows):.1f} ms")
    if len(hashes) != 1:
        raise AssertionError("loading policies produced different textures")
    print("Terrain texture bytes identical; synthetic delays only, not an end-to-end speedup estimate.")


if __name__ == "__main__":
    main()
