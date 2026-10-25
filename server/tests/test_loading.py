"""Terrain becomes visible before optional imagery analyses finish (no network)."""
import asyncio
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import deshadow
import earthd
import farfield
from test_server import FakeDem, FakeImagery, LAT, LON


class ProgressiveFarTerrainTest(unittest.TestCase):
    def test_analysis_pending_then_texture_refreshed(self):
        with tempfile.TemporaryDirectory() as root:
            s = earthd.Server(root, os.path.join(root, "cache"), imagery=None, near_visual="detail", cells=64)
            s.dem = FakeDem()
            d = s.deshadow = deshadow.Deshadow(os.path.join(root, "cache"), s.dem)

            def pending(rk):
                # A real pending region, with a deadline that would block a normal render.
                # Fail immediately if loading ever tries to wait for this cosmetic work.
                event = Mock(spec=threading.Event)
                event.wait.side_effect = AssertionError("terrain waited for shadow analysis")
                d._events.setdefault(rk, event)
                d._wait_until[rk] = time.monotonic() + 30

            d._enqueue = pending
            s.imagery = FakeImagery(os.path.join(root, "imagery"), chain=("usgs",), deshadow=d)
            d.add_listener(s._ds_listener)
            try:
                a = s.new_anchor(LAT, LON + 1)  # inside USGS coverage, including coarse coverage probes
                tile = farfield.Tile(2, 0, 0)  # source zoom 16: shadow correction applies here

                async def run():
                    first = await s.tile(a, tile)
                    self.assertTrue(first.cm_pending, (s.imagery.loads, d.stats, s.tex_size(tile.level)))
                    self.assertTrue(os.path.exists(os.path.join(s.out_dir, first.texture)))
                    self.assertTrue(s.cm_waiting)
                    # Another correction can trigger a refresh while shadow analysis is still pending.
                    first.stale = True
                    second = await s.tile(a, tile)
                    self.assertEqual(second.rev, 1)
                    self.assertEqual(first.cm_pending, second.cm_pending)
                    for rk in second.cm_pending:
                        d._params[tuple(rk[1:])] = {"ok": False}
                        s._ds_listener(tuple(rk[1:]), True)
                    await asyncio.sleep(0)  # deliver the analysis-completion callbacks
                    self.assertTrue(second.stale)
                    third = await s.tile(a, tile)
                    self.assertEqual(third.rev, 2)
                    self.assertFalse(third.cm_pending)
                    self.assertFalse(s.cm_waiting)
                    self.assertIs(await s.tile(a, tile), third)

                asyncio.run(run())
            finally:
                s.tile_pool.shutdown(wait=True)
                s.hm_pool.shutdown(wait=True)
                s.imagery.close()


if __name__ == "__main__":
    unittest.main()
