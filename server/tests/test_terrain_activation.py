"""Terrain preparation may precede activation; retired frames cannot publish."""
import asyncio
from collections import deque
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import earthd


class TerrainActivationTest(unittest.IsolatedAsyncioTestCase):
    async def run_request(self, active, activate_during_build=None):
        entered, finish = asyncio.Event(), asyncio.Event()
        async def terrain(*args):
            entered.set()
            await finish.wait()
            return {}
        c = earthd.Connection.__new__(earthd.Connection)
        c.s = SimpleNamespace(anchors={2: SimpleNamespace(id=2)}, snap=lambda v: v,
                              layout=SimpleNamespace(block_at=lambda x, y: (x, y)),
                              terrain=terrain, hm_sent=deque())
        c.active, c.block, c.terrain_wait = active, (0, 0), 0
        c._new_request = lambda *a: 1
        c._current_request = lambda *a: True
        c.terrain_msg = lambda *a: {}
        sent = []
        c.send = sent.append
        task = asyncio.create_task(c.prepare_terrain(dict(requestId=1, anchorId=2, cx=0, cy=0)))
        await entered.wait()
        if activate_during_build is not None:
            c.active = activate_during_build
        finish.set()
        await task
        self.assertEqual(c.terrain_wait, 0)
        return sent

    async def test_preparation_before_first_activation(self):
        self.assertEqual((await self.run_request(None))[0]['type'], 'terrain_ready')

    async def test_preparation_for_next_anchor(self):
        self.assertEqual((await self.run_request(1))[0]['type'], 'terrain_ready')

    async def test_anchor_retired_during_build_is_suppressed(self):
        self.assertEqual(await self.run_request(1, activate_during_build=3), [])
