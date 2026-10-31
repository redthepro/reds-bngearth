"""Deterministic admission, publication and protocol lifecycle regressions; no network/engine."""
import asyncio
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor, CancelledError
from pathlib import Path
import sys
import threading
import time
import tempfile
import os
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import earthd
import farfield
import streaming
import terrainprep
import workcontrol
import geodesy
import numpy as np
import roads


class Writer:
    def __init__(self, buffered=0):
        self.closed, self.frames = False, []
        self.transport = SimpleNamespace(get_write_buffer_size=lambda: buffered)
    def is_closing(self): return self.closed
    def close(self): self.closed = True
    def write(self, value): self.frames.append(value)


def server():
    s = earthd.Server.__new__(earthd.Server)
    s.dem = SimpleNamespace(invalidation_epoch=0)
    s.mapdata = None
    s.hm, s.hm_inflight, s._plan_requests = OrderedDict(), {}, {}
    s._plan_anchor = lambda a: a
    s.evict_heightmaps = lambda **kw: None
    s.hm_pool = ThreadPoolExecutor(2)
    s.anchors = {}
    return s


def info(value):
    return dict(value=value, zoom=1, seconds=0, stages={}, zBase=0, maxHeight=1, q=1)


class AsyncLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_saturated_prefetch_cannot_consume_demand_admission(self):
        s, entered, finish = server(), threading.Event(), threading.Event()
        self.addCleanup(s.hm_pool.shutdown, wait=True)
        self.addCleanup(finish.set)
        a = SimpleNamespace(id=1)
        s.anchors[1] = a
        def build(anchor, x, y, progress, lane, *args):
            if lane == earthd.LANE_PREFETCH:
                entered.set()
                finish.wait(3)
            return info(x)
        s.make_heightmap = build
        speculative = asyncio.create_task(s.terrain(a, 0, 0, lane=earthd.LANE_PREFETCH))
        while not entered.is_set():
            await asyncio.sleep(0)
        try:
            with self.assertRaisesRegex(RuntimeError, 'busy; retry'):
                await s.terrain(a, 2, 0, lane=earthd.LANE_PREFETCH)
            self.assertEqual((await asyncio.wait_for(s.terrain(a, 1, 0), 2))['value'], 1)
            self.assertFalse(speculative.done())
        finally:
            finish.set()
            await speculative
        self.assertEqual(s.prefetch_budget.snapshot()['critical']['jobs'], 0)
        self.assertEqual(s.work_budget.snapshot()['critical']['jobs'], 0)

    async def test_slow_photo_saturation_cannot_queue_more_or_hold_terrain(self):
        s, finish = server(), threading.Event()
        self.addCleanup(s.hm_pool.shutdown, wait=True)
        s.base_pool, s.urgent_base_pool = ThreadPoolExecutor(2), ThreadPoolExecutor(2)
        self.addCleanup(s.base_pool.shutdown, wait=True)
        self.addCleanup(s.urgent_base_pool.shutdown, wait=True)
        self.addCleanup(finish.set)
        def photo():
            finish.wait(3)
            return None
        held = [s.photo_submit(urgent, index, photo) for urgent in (False, True) for index in (1, 2)]
        self.assertIsNone(s.photo_submit(True, 3, photo))
        self.assertIsNone(s.photo_submit(False, 3, photo))
        self.assertEqual(s.photo_budgets['urgent'].snapshot()['critical']['jobs'], 2)
        self.assertEqual(s.photo_budgets['optional'].snapshot()['critical']['jobs'], 2)
        a = SimpleNamespace(id=1)
        s.anchors[1] = a
        def terrain(*args):
            base = s.photo_submit(True, 'denied', photo)
            return dict(info('collision-ready'), base=base)
        s.make_heightmap = terrain
        result = await s.terrain(a, 0, 0)
        self.assertEqual(result['value'], 'collision-ready')
        self.assertIsNone(result['base'])
        self.assertFalse(finish.is_set(), 'terrain completed while admitted photos remained blocked')
        finish.set()
        await asyncio.gather(*(asyncio.wrap_future(f) for f in held))
        self.assertEqual(s.photo_inflight, {})

    async def test_roads_publication_waits_for_matching_committed_terrain(self):
        tr, sent = roads.Tracker(), []
        c = SimpleNamespace(active=1, active_receipt='fixture:1:1', ensure_plan_receipt=lambda: 'fixture:1:2',
                            send=sent.append, s=SimpleNamespace(out_dir=None))
        tr.on_pose(c, 1, 0, 0, asyncio.get_running_loop(), lambda *a: ('/road', 1))
        self.assertIsNone(tr.pool, 'new support work waits for matching terrain commit')
        key = (0, 0)
        def completion():
            f = asyncio.get_running_loop().create_future()
            f._plan_receipt = 'fixture:1:2'
            f.set_result(('/road', 1))
            tr.aid, tr.building, tr.futs = 1, {key}, {key: f}
            tr._built(c, 1, key, f)
        completion()
        self.assertEqual(sent, [])
        self.assertEqual(tr.sent, set())
        c.active_receipt = 'fixture:1:2'
        completion()
        self.assertEqual(sent[0]['planReceipt'], c.active_receipt)

    async def test_exact_request_replay_coalesces_and_replays_completed_reply(self):
        s, entered, finish, calls = server(), threading.Event(), threading.Event(), []
        self.addCleanup(s.hm_pool.shutdown, wait=True)
        a = SimpleNamespace(id=1)
        s.anchors[1] = a
        def build(*args):
            calls.append(1)
            entered.set()
            finish.wait(3)
            return info(1)
        s.make_heightmap = build
        c = earthd.Connection.__new__(earthd.Connection)
        c.tasks, c.writer = set(), Writer()
        async def request(msg):
            await s.terrain(a, 0, 0)
            c.send(dict(type='terrain_ready', requestId=msg['requestId'], anchorId=1))
        msg = dict(type='prepare_terrain', requestId=7, anchorId=1, cx=0, cy=0)
        first = c.replace_request('terrain', msg, request)
        while not entered.is_set(): await asyncio.sleep(0)
        self.assertIs(c.replace_request('terrain', dict(msg), request), first)
        self.assertFalse(s.hm_inflight[(1, 0, 0)]._lease.cancelled)
        finish.set()
        await first
        c.replace_request('terrain', dict(msg), request)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(c.writer.frames), 2)
        self.assertEqual(c.writer.frames[0], c.writer.frames[1])

    async def test_anchor_final_await_cannot_relabel_old_payload_receipt(self):
        epoch, built = [0], []
        a = SimpleNamespace(id=1)
        s = SimpleNamespace(anchors={}, surface=None, grading=False, hm_sent=deque(), sid='fixture',
                            vfs_dir='/fixture', layout=SimpleNamespace(block=512),
                            dem=SimpleNamespace(stats_snapshot=lambda: {}))
        s.new_anchor, s.snap = lambda lat, lon: a, lambda x: x
        s.input_receipt = lambda: {'epoch': epoch[0]}
        s.hm_vfs = lambda key: '/fixture/old.png'
        async def terrain(*args):
            built.append(epoch[0])
            return dict(info(epoch[0]), groundZ=0, _input_receipt={'epoch': epoch[0]},
                        _heightmap_name=f'epoch{epoch[0]}.png')
        s.terrain = terrain
        def ground_z(*args):
            epoch[0] = 1
            return 0
        s.ground_z_at = ground_z
        c = earthd.Connection.__new__(earthd.Connection)
        c.s, c.active, c.lifecycle, c.prepared = s, None, True, {}
        c.writer, c.wake = Writer(), asyncio.Event()
        c.status, c._progress = lambda *args: None, lambda: None
        sent = []
        c.send = sent.append
        with ThreadPoolExecutor(1) as s.ui_pool:
            await c.prepare_anchor(dict(requestId=1, lat=0, lon=0, px=0, py=0))
        ready = [m for m in sent if m['type'] == 'anchor_ready']
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]['terrain']['heightmap'], '/fixture/epoch1.png')
        self.assertEqual(c.plan_receipts[1][0], {'epoch': 1})
        self.assertEqual(built, [0, 1])

    async def test_source_invalidated_tile_rebuilds_geometry_instead_of_retexturing(self):
        s = server()
        self.addCleanup(s.hm_pool.shutdown, wait=True)
        s.tile_pool, s.tiles, s.tile_inflight = s.hm_pool, OrderedDict(), {}
        s.dem.invalidation_epoch = 1
        s.airports = s.sea = s.far_pool = s.imagery = s.colormatch = s.deshadow = s.credits = None
        s.grader_for, s.anchor_q, s.tex_size = lambda a: None, lambda a: 1 / 64, lambda level: 0
        s.tile_name, s.near_visual, s.far_format = lambda *args: 'geometry.cdae', 'mesh', 'cdae'
        s.out_dir, s.layout = '.', SimpleNamespace()
        s.build_stats, s.level_stats, s.tile_cache = deque(), {}, 8
        s.matslots = SimpleNamespace(short=lambda: False)
        tile = farfield.Tile(0, 0, 0)
        a = SimpleNamespace(id=1, zooms=[1], frame=None)
        old = earthd.TileInfo([0, 0, 0], 0, None, 0, np.zeros((2, 2)), 100, 0)
        old._input_receipt = dict(s.input_receipt(), dem_epoch=0)
        s.tiles[(1, tile.key)] = old
        s.retexture = mock.Mock(side_effect=AssertionError('old geometry cannot be reused'))
        geometry = SimpleNamespace(pos=[0, 0, 1], err=0, sink=None, skirt=0, grid=np.ones((2, 2)))
        with mock.patch.dict(os.environ, BNGE_COMPILED_TERRAIN='0'), \
             mock.patch.object(farfield, 'build_tile', return_value=geometry) as build, \
             mock.patch.object(farfield, 'write_built', return_value=100):
            result = await s.tile(a, tile)
        build.assert_called_once()
        s.retexture.assert_not_called()
        np.testing.assert_array_equal(result.grid, np.ones((2, 2)))
        self.assertEqual(result._input_receipt, s.input_receipt())

    async def test_cancelled_consumer_keeps_running_reservation_until_callable_stops(self):
        s, entered, finish = server(), threading.Event(), threading.Event()
        self.addCleanup(s.hm_pool.shutdown, wait=True)
        lease = s._admission().try_acquire('run', 'visual', 100)
        def build():
            entered.set()
            finish.wait(3)
            return 7
        future = s._submit_work(s.hm_pool, lease, build)
        while not entered.is_set(): await asyncio.sleep(0)
        future.cancel()
        self.assertEqual(s.work_budget.snapshot()['visual']['jobs'], 1)
        lease.cancel()
        finish.set()
        while not lease.finished: await asyncio.sleep(0)
        self.assertEqual(s.work_budget.snapshot()['visual']['bytes'], 0)

    async def test_exception_releases_reservation(self):
        s = server()
        self.addCleanup(s.hm_pool.shutdown, wait=True)
        lease = s._admission().try_acquire('bad', 'visual', 100)
        def fail(): raise ValueError('fixture')
        with self.assertRaises(ValueError): await s._submit_work(s.hm_pool, lease, fail)
        self.assertTrue(lease.finished)

    async def test_shared_tile_consumers_do_not_cancel_each_other(self):
        s, finish, calls = server(), threading.Event(), []
        self.addCleanup(s.hm_pool.shutdown, wait=True)
        s.tile_pool, s.tiles, s.tile_inflight = s.hm_pool, OrderedDict(), {}
        s._tile_done = lambda key, fut: s.tile_inflight.pop(key, None)
        def build(*args):
            calls.append(args)
            finish.wait(3)
            return 'shared'
        s.make_tile = build
        a, tile = SimpleNamespace(id=1), farfield.Tile(0, 0, 0)
        first, second = s.tile(a, tile), s.tile(a, tile)
        first.cancel()
        finish.set()
        self.assertEqual(await second, 'shared')
        self.assertEqual(len(calls), 1)
        self.assertEqual(s.work_budget.snapshot()['visual']['jobs'], 0)

    async def test_source_change_during_build_cannot_return_old_payload(self):
        s, entered, finish, calls = server(), threading.Event(), threading.Event(), []
        self.addCleanup(s.hm_pool.shutdown, wait=True)
        a = SimpleNamespace(id=1)
        s.anchors[1] = a
        def build(*args):
            epoch = s.dem.invalidation_epoch
            calls.append(epoch)
            if epoch == 0:
                entered.set()
                finish.wait(3)
            return info(epoch)
        s.make_heightmap = build
        task = asyncio.create_task(s.terrain(a, 0, 0))
        while not entered.is_set(): await asyncio.sleep(0)
        s.dem.invalidation_epoch = 1
        finish.set()
        result = await task
        self.assertEqual(result['value'], 1)
        self.assertEqual(calls, [0, 1])
        self.assertEqual(s.hm[(1, 0, 0)]['value'], 1)

    async def test_generation_change_without_input_epoch_change_is_rechecked(self):
        s, entered, finish = server(), threading.Event(), threading.Event()
        self.addCleanup(s.hm_pool.shutdown, wait=True)
        source = SimpleNamespace(id=1)
        current = [SimpleNamespace(id=1, _world_generation=1, _world_receipt={'g': 1})]
        s._plan_anchor = lambda a: current[0]
        s.anchors[1] = source
        def build(anchor, *args):
            if anchor._world_generation == 1:
                entered.set()
                finish.wait(3)
            return info(anchor._world_generation)
        s.make_heightmap = build
        task = asyncio.create_task(s.terrain(source, 0, 0))
        while not entered.is_set(): await asyncio.sleep(0)
        current[0] = SimpleNamespace(id=1, _world_generation=2, _world_receipt={'g': 2})
        finish.set()
        self.assertEqual((await task)['value'], 2)

    async def test_full_critical_budget_is_terminal_busy_and_starts_no_job(self):
        s = server()
        self.addCleanup(s.hm_pool.shutdown, wait=True)
        s.work_budget = workcontrol.Budget(critical_jobs=1, critical_bytes=32 << 20)
        lease = s.work_budget.try_acquire('held', 'critical', 32 << 20)
        with self.assertRaisesRegex(RuntimeError, 'busy; retry'):
            await s.terrain(SimpleNamespace(id=1), 0, 0)
        self.assertEqual(s.hm_inflight, {})
        lease.finish()

    async def test_repeated_partial_input_invalidation_is_bounded_and_never_published(self):
        s, calls = server(), []
        self.addCleanup(s.hm_pool.shutdown, wait=True)
        a = SimpleNamespace(id=1)
        s.anchors[1] = a
        def changing(*args):
            calls.append(1)
            s.dem.invalidation_epoch += 1
            return info('partial')
        s.make_heightmap = changing
        with self.assertRaisesRegex(RuntimeError, 'inputs changed repeatedly; retry'):
            await s.terrain(a, 0, 0)
        self.assertEqual(len(calls), 3)
        self.assertEqual(s.hm, {})


class LifecycleTest(unittest.TestCase):
    def test_photo_submission_failure_and_queued_cancel_release_only_their_lease(self):
        s, finish = server(), threading.Event()
        self.addCleanup(s.hm_pool.shutdown, wait=True)
        s.base_pool = s.urgent_base_pool = ThreadPoolExecutor(1)
        self.addCleanup(s.base_pool.shutdown, wait=True)
        self.addCleanup(finish.set)
        first = s.photo_submit(True, 1, lambda: finish.wait(3))
        second = s.photo_submit(True, 2, lambda: None)
        self.assertTrue(second.cancel())
        self.assertEqual(s.photo_budgets['urgent'].snapshot()['critical']['jobs'], 1)
        def fail(*args): raise RuntimeError('submit failed')
        s.urgent_base_pool = SimpleNamespace(submit=fail)
        with self.assertRaisesRegex(RuntimeError, 'submit failed'):
            s.photo_submit(True, 3, lambda: None)
        self.assertEqual(s.photo_budgets['urgent'].snapshot()['critical']['jobs'], 1)
        finish.set()
        first.result()
        self.assertEqual(s.photo_budgets['urgent'].snapshot()['critical']['jobs'], 0)

    def test_delayed_old_anchor_commit_cannot_roll_coordinate_frame_back(self):
        c = earthd.Connection.__new__(earthd.Connection)
        c.active = 2
        c.commit_anchor(1)
        self.assertEqual(c.active, 2)

    def pressure_connection(self):
        c = earthd.Connection.__new__(earthd.Connection)
        c.lifecycle, c.stream_seq, c.received_seq = True, 2, 0
        c.stream_pending = {1: (100, 5), 2: (100, 5)}
        c.stream_pressure, c.stream_pressure_at = None, -float('inf')
        return c

    def test_receipt_ack_preserves_client_install_pressure_and_expiry_closes_optional_admission(self):
        c = self.pressure_connection()
        c._stream_feedback(dict(feedbackSeq=2, receivedSeq=2, pendingBytes=streaming.PENDING_BYTES, installMs=10))
        self.assertEqual(c.stream_pending, {})
        self.assertFalse(c._visual_room(1, 0))
        c._stream_feedback(dict(feedbackSeq=1, receivedSeq=1, pendingBytes=0, installMs=0))
        self.assertFalse(c._visual_room(1, 0))  # regressing feedback cannot reopen production
        c._stream_feedback(dict(feedbackSeq=2, receivedSeq=2, pendingBytes=0, installMs=0))
        self.assertFalse(c._visual_room(1, 0))  # duplicate watermark/report cannot erase newer pressure
        c.stream_pressure_at = time.monotonic() - streaming.FEEDBACK_TTL - 1
        self.assertFalse(c._visual_room())

    def test_lost_ack_counts_transmitted_bytes_and_reconnect_starts_closed(self):
        c = self.pressure_connection()
        self.assertFalse(c._visual_room())
        c._stream_feedback(dict(feedbackSeq=1, receivedSeq=0, pendingBytes=0, installMs=0))
        self.assertFalse(c._visual_room(streaming.PENDING_BYTES - 199, 0))
        for value in ({'receivedSeq': True}, dict(receivedSeq=3, pendingBytes=0, installMs=0),
                      dict(receivedSeq=1, pendingBytes=float('nan'), installMs=0)):
            c._stream_feedback(value)
        self.assertEqual(c.received_seq, 0)

    def test_global_visual_and_support_pressure_cannot_borrow_terrain_reservation(self):
        s = server()
        self.addCleanup(s.hm_pool.shutdown, wait=True)
        visual = [s._admission().try_acquire(i, 'visual', streaming.JOB_BYTES) for i in range(8)]
        self.assertIsNone(s._admission().try_acquire('ninth', 'visual', 1))
        terrain = s._admission().try_acquire('terrain', 'critical', streaming.CRITICAL_BYTES)
        self.assertIsNotNone(terrain)
        for lease in visual + [terrain]: lease.finish()

    def test_shared_cancellation_only_after_last_consumer_retires(self):
        s = server()
        self.addCleanup(s.hm_pool.shutdown, wait=True)
        lease = s._admission().try_acquire('tile', 'visual', 100)
        s.tile_leases = {(1, 'k'): lease}
        c1, c2 = SimpleNamespace(active=1, desired={}), SimpleNamespace(active=1, desired={'k': True})
        s.connections = [c1, c2]
        s.cancel_unwanted_tiles()
        self.assertFalse(lease.cancelled)
        c2.desired = {}
        s.cancel_unwanted_tiles()
        self.assertTrue(lease.cancelled)
        with self.assertRaises(CancelledError): lease.checkpoint()
        self.assertEqual(s.work_budget.snapshot()['visual']['jobs'], 1)
        lease.finish()

    def test_checkpoint_prevents_expensive_terrain_stage(self):
        calls = []
        def cancelled(): raise CancelledError()
        with self.assertRaises(CancelledError):
            terrainprep.prepare(None, 0, 0, 128, 1, '.', 'not-written.png', None,
                                sample=lambda *args: calls.append(args), checkpoint=cancelled)
        self.assertEqual(calls, [])

    def test_cancel_after_height_sampling_skips_ter_stage(self):
        checks, samples = [], []
        def checkpoint():
            checks.append(1)
            if len(checks) == 2: raise CancelledError()
        def sample(la, lo):
            samples.append(1)
            return np.zeros(np.shape(la))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(CancelledError):
                terrainprep.prepare(geodesy.EnuFrame(30, 40), 0, 0, 16, 1 / 64, directory,
                                    'prepared.png', 'not-written.ter', sample=sample, checkpoint=checkpoint)
            self.assertTrue(samples)
            self.assertFalse((Path(directory) / 'not-written.ter').exists())

    def test_oversized_frame_and_slow_transport_close_without_write(self):
        for buffered, value in ((streaming.PENDING_BYTES, 'small'), (0, 'x' * earthd.protocol.MAX_FRAME)):
            c = earthd.Connection.__new__(earthd.Connection)
            c.writer = Writer(buffered)
            self.assertFalse(c.send({'type': 'fixture', 'value': value}))
            self.assertTrue(c.writer.closed)
            self.assertEqual(c.writer.frames, [])

    def test_duplicate_delayed_commit_cannot_activate_replaced_preparation(self):
        c = earthd.Connection.__new__(earthd.Connection)
        c.lifecycle, c.active, c.active_receipt = True, 1, 'old'
        c.prepared = {(1, 0, 0): ('receipt/2', 'receipt')}
        c.ensure_plan_receipt = lambda aid=None: 'receipt'
        c.s = SimpleNamespace(snap=lambda v: v, layout=SimpleNamespace(block_at=lambda x, y: (x, y)))
        c.block = (0, 0)
        c.commit_terrain(dict(anchorId=1, cx=0, cy=0, activationId='receipt/1', planReceipt='receipt'))
        self.assertEqual(c.active_receipt, 'old')
        c.commit_terrain(dict(anchorId=1, cx=0, cy=0, activationId='receipt/2', planReceipt='receipt'))
        self.assertEqual(c.active_receipt, 'receipt')

    def test_old_anchor_terrain_ack_cannot_mutate_active_receipt(self):
        c = earthd.Connection.__new__(earthd.Connection)
        c.lifecycle, c.active, c.active_receipt = True, 2, 'session:2:2'
        c.prepared = {(1, 0, 0): ('session:1:1/1', 'session:1:1')}
        c.ensure_plan_receipt = lambda aid=None: 'session:1:1'
        c.s = SimpleNamespace(snap=lambda v: v, anchors={1: True})
        c.commit_terrain(dict(anchorId=1, cx=0, cy=0,
                             activationId='session:1:1/1', planReceipt='session:1:1'))
        self.assertEqual((c.active, c.active_receipt), (2, 'session:2:2'))

    def test_old_different_block_ack_cannot_roll_back_newer_commit(self):
        c = earthd.Connection.__new__(earthd.Connection)
        c.lifecycle, c.active, c.active_receipt = True, 1, 'receipt'
        c.prepared = {(1, 0, 0): ('receipt/1', 'receipt'), (1, 1, 0): ('receipt/2', 'receipt')}
        c.ensure_plan_receipt = lambda aid=None: 'receipt'
        c.s = SimpleNamespace(snap=lambda v: v, layout=SimpleNamespace(block_at=lambda x, y: (x, y)))
        c.block = (0, 0)
        c._set_block = lambda aid, x, y: setattr(c, 'block', (x, y))
        updates = []
        c.update_far = lambda: updates.append(c.block)
        for x, serial in ((1, 2), (0, 1), (1, 2)):
            c.commit_terrain(dict(anchorId=1, cx=x, cy=0,
                                 activationId=f'receipt/{serial}', planReceipt='receipt'))
        self.assertEqual(c.block, (1, 0))
        self.assertEqual(c.committed_activation_serial, 2)
        self.assertEqual(updates, [(1, 0)])


if __name__ == '__main__': unittest.main()
