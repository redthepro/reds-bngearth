"""Current callers bind generations and reject stale cache publication, offline."""
import asyncio
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import itertools
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import earthd
import geodesy
import heightmap
import grading
import navgraph
import worldplan
from test_dem_provisional import ProvisionalDem
from test_grading import FakeMD
from test_roads import FRAME, LAT, LON, features, flat, rules


class CallerGenerationsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.server = s = earthd.Server.__new__(earthd.Server)
        s.out_dir, s.cache_dir, s.vfs_dir = str(root / 'out'), str(root / 'cache'), '/temp/unit'
        Path(s.out_dir).mkdir()
        s.layout = SimpleNamespace(block=128)
        s.mapdata = s.surface = s.road_widths = s.sea = s.airports = s.imagery = None
        s.grading = False
        s.grader_lock = threading.Lock()
        s._plan_generations = worldplan.GenerationStore()
        s._plan_requests = {}
        s._plan_active, s._plan_activity_lock = {}, threading.Lock()
        s._plan_generation_anchors = set()
        s.hm, s.hm_inflight = OrderedDict(), {}
        s.hm_stage = {}
        s.hm_here_lock, s.hm_here = threading.Lock(), 0
        s.hm_pool = ThreadPoolExecutor(2)
        self.addCleanup(s.hm_pool.shutdown, wait=True)
        s.block_pools = None
        s.near_visual = 'mesh'
        s.ter_serial = itertools.count(1)
        s.evict_heightmaps = lambda **kw: None
        s.connections = []
        s.dem = SimpleNamespace(invalidation_epoch=0, sample=lambda la, lo, z, progress=None:
                                np.full(np.shape(la), 10.0))
        self.anchor = earthd.Anchor(1, LAT, LON, geodesy.EnuFrame(LAT, LON), q=1 / 64)
        self.anchor.zooms = {0: 12}
        s.anchors = {1: self.anchor}
        self.flags = mock.patch.dict(os.environ, BNGE_SHARED_TERRAIN='on', BNGE_COMPILED_TERRAIN='1')
        self.flags.start()
        self.addCleanup(self.flags.stop)

    def test_atomic_bundle_replacement_does_not_reset_old_holders(self):
        old = self.server._plan_anchor(self.anchor)
        old.grader, old.tunnels, old.bridges = object(), object(), object()
        old.chunks.sentinel = 'old'
        self.server.dem.invalidation_epoch += 1
        new = self.server._plan_anchor(self.anchor)
        self.assertNotEqual(old._world_generation, new._world_generation)
        self.assertIsNone(new.grader)
        self.assertIsNone(new.tunnels)
        self.assertIsNone(new.bridges)
        self.assertIsNot(old.chunks, new.chunks)
        self.assertEqual(old.chunks.sentinel, 'old')
        self.assertIs(self.server._plan_anchor(old), old, 'bound in-flight job must keep its snapshot')
        self.assertEqual(self.server.anchor_q(old), self.server.anchor_q(new))

    def test_same_anchor_partial_dem_upgrade_rebuilds_prepared_and_memory_cache(self):
        provider = ProvisionalDem(self.temp.name)
        for pool in (provider.pool, provider.urgent_pool, provider.probe_pool):
            self.addCleanup(pool.shutdown, wait=True)
        def sample(la, lo, zoom, progress=None):
            tile = provider.tiles([(1, 1, 0)])[(1, 1, 0)]
            return np.full(np.shape(la), float(tile[0, 0]))
        provider.sample = sample
        self.server.dem = provider
        async def run():
            first = await self.server.terrain(self.anchor, 0, 0)
            second = await self.server.terrain(self.anchor, 0, 0)
            third = await self.server.terrain(self.anchor, 0, 0)
            return first, second, third
        first, second, third = asyncio.run(run())
        # A changed input may finish obsolete work, but that payload must not escape its waiter.
        current = self.server._plan_anchor(self.anchor)
        self.assertEqual(first['_world_generation'], current._world_generation)
        self.assertEqual(first['_world_generation'], second['_world_generation'])
        self.assertEqual(first['_heightmap_name'], second['_heightmap_name'])
        self.assertEqual((Path(self.server.out_dir) / first['_heightmap_name']).read_bytes(),
                         (Path(self.server.out_dir) / second['_heightmap_name']).read_bytes())
        self.assertEqual(second['groundZ'], first['groundZ'])
        self.assertEqual(provider.calls[(1, 1, 0)], 2, 'one bounded upgrade, then reuse')
        self.assertEqual(second['groundZ'], third['groundZ'])
        self.assertEqual(self.server.hm[(1, 0, 0)]['_world_generation'], third['_world_generation'])

    def test_late_completion_cannot_replace_newer_heightmap_or_its_files(self):
        started, release = threading.Event(), threading.Event()
        def build(anchor, cx, cy, *args):
            generation = anchor._world_generation
            if generation == 1:
                started.set()
                release.wait(3)
            name = self.server.hm_name((1, cx, cy), anchor)
            (Path(self.server.out_dir) / name).write_text(str(generation))
            return {'_world_generation': generation, '_heightmap_name': name,
                    'zoom': 12, 'seconds': 0, 'stages': {}, 'zBase': 0, 'maxHeight': 1024, 'q': 1 / 64}
        self.server.make_heightmap = build
        async def run():
            old = asyncio.create_task(self.server.terrain(self.anchor, 0, 0))
            await asyncio.to_thread(started.wait, 2)
            self.server.dem.invalidation_epoch += 1
            new = await self.server.terrain(self.anchor, 0, 0)
            release.set()
            older_request_result = await old
            return new, older_request_result
        try:
            new, old = asyncio.run(run())
        finally:
            release.set()
        self.assertEqual(new['_world_generation'], 2)
        self.assertEqual(old['_world_generation'], 2, 'superseded waiter must consume the current result')
        self.assertEqual(self.server.hm[(1, 0, 0)]['_world_generation'], 2)
        self.assertEqual((Path(self.server.out_dir) / new['_heightmap_name']).read_text(), '2')

    def test_changed_grading_settings_replace_cached_block_and_unchanged_reuses(self):
        self.server.grading = True
        self.server.mapdata = FakeMD(features([[(-300, 0), (300, 0)]], ['residential']))
        async def run():
            first = await self.server.terrain(self.anchor, 0, 0)
            cached = await self.server.terrain(self.anchor, 0, 0)
            with mock.patch.object(grading, 'ROAD_LIFT', grading.ROAD_LIFT + .25):
                changed = await self.server.terrain(self.anchor, 0, 0)
            return first, cached, changed
        first, cached, changed = asyncio.run(run())
        self.assertIs(first, cached)
        self.assertNotEqual(first['_world_generation'], changed['_world_generation'])
        self.assertNotEqual(first['groundZ'], changed['groundZ'])

    def test_anchor_retirement_protects_active_jobs_then_cleans_generation_files(self):
        s = self.server
        s.horizon_grids, s.horizon_failed, s.tiles, s.degraded = {}, {}, {}, set()
        s.tex_rev, s.retex_todo, s.cm_waiting, s.tile_inflight = {}, set(), {}, {}
        s.matslots = SimpleNamespace(holders=lambda: [])
        s.hm_committed, s.hm_sent = [], []
        s.cdae_dir = str(Path(self.temp.name) / 'cdae')
        doomed = []
        s.queue_delete = lambda paths: doomed.extend(paths)
        anchor = s._plan_anchor(self.anchor)
        directory, _ = s._plan_output(anchor)
        product = Path(directory) / 'road_1_0_0.cdae'
        product.write_bytes(b'fixture')
        s._plan_active[1] = 1
        s.prune(2)
        self.assertIn(1, s.anchors)
        self.assertEqual(doomed, [])
        s._plan_active.clear()
        s.connections = [SimpleNamespace(held={1})]
        s.prune(2)
        self.assertIn(1, s.anchors)
        s.connections = []
        s.hm_committed = [(1, 0, 0)]
        s.prune(2)
        self.assertIn(1, s.anchors, 'key-only commit cannot release an older generation safely')
        s.hm_committed.clear()
        s.prune(2)
        self.assertNotIn(1, s.anchors)
        self.assertIn(str(product), doomed)
        self.assertIn(directory, doomed)


class BridgeSupportTest(unittest.TestCase):
    def test_disconnected_deck_fragments_cannot_link_ground_level_gaps(self):
        f = features([[(0, 0), (200, 0)]], ['primary'], levels=[(0, 1.0)], flags=[(0, 'bridge')])
        fragments = {'id000': [(np.array([[centre - 1., 0], [centre + 1., 0]]), np.array([20., 20.]))
                               for centre in (0., 100., 200.)]}
        graph = navgraph.build(FRAME, [f], flat, (0, 0), decks=fragments, require_bridge_support=True)
        self.assertEqual(graph['edges'], [])
    def test_missing_deck_is_not_replaced_by_ground_navigation(self):
        f = features([[(0, 0), (200, 0)]], ['primary'], levels=[(0, 1.0)], flags=[(0, 'bridge')])
        legacy = navgraph.build(FRAME, [f], flat, (0, 0))
        guarded = navgraph.build(FRAME, [f], flat, (0, 0), require_bridge_support=True)
        self.assertTrue(legacy['edges'])
        self.assertEqual(guarded['edges'], [])

    def test_supported_bridge_keeps_its_deck_in_guarded_graph(self):
        f = features([[(0, 0), (200, 0)]], ['primary'], levels=[(0, 1.0)], flags=[(0, 'bridge')])
        f.rules['connectors'] = rules([(0, 'A', 0, 0), (0, 'B', 1, 1)])
        deck = {'id000': [(np.array([[-1., 0], [201., 0]]), np.array([20., 20.]))]}
        graph = navgraph.build(FRAME, [f], flat, (0, 0), decks=deck, require_bridge_support=True)
        self.assertTrue(graph['edges'])
        self.assertTrue(all(abs(point[2] - 20) < .01 for point in graph['nodes'].values()))


if __name__ == '__main__':
    unittest.main()
