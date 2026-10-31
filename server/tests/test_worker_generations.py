"""Worker jobs bind one decision generation across DEM/source upgrades."""
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
import blockworker
import heightmap
import worldplan


class WorkerGenerationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="worker-generation-test-")
        self.addCleanup(self.temp.cleanup)
        state = blockworker._State.__new__(blockworker._State)
        state.cache_dir = self.temp.name
        state.dem = SimpleNamespace(invalidation_epoch=0, worldplan_revision="partial",
                                    sample=lambda la, lo, *args: np.full(np.shape(la), 10.0))
        state.md = SimpleNamespace(release="fixture-v1", worldplan_revision="roads-v1",
                                   cell=lambda *args, **kwargs: SimpleNamespace(layers={"roads": None}))
        state.grading, state.widths, state.surface, state.airports, state.sea = True, None, None, None, None
        state.graders, state.chunks, state.bridges, state.tunnels = {}, {}, {}, {}
        state.lock, state.imagery = threading.Lock(), None
        self.state = state
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(blockworker, "_s", state).start()
        mock.patch.dict(os.environ, BNGE_SHARED_TERRAIN="on", BNGE_COMPILED_TERRAIN="1").start()

    def test_upgrade_replaces_entire_bundle_without_mutating_old_holders(self):
        with blockworker._generation_scope(5, 40.0, -74.0) as old:
            grader, chunks = old.anchor(5, 40.0, -74.0)
            grader.cells["old-cell"] = []
            chunks.put((0, 0), np.zeros((heightmap.CHUNK, heightmap.CHUNK)))
            old.bridges[5], old.tunnels[5] = "old bridge", "old tunnel"
            self.assertIsNone(grader.shared, "uncertain decisions must not enter the per-run disk cache")
        self.state.dem.invalidation_epoch = 1
        self.state.dem.worldplan_revision = "complete-v2"
        with blockworker._generation_scope(5, 40.0, -74.0) as current:
            fresh_grader, fresh_chunks = current.anchor(5, 40.0, -74.0)
            self.assertIsNot(current, old)
            self.assertIsNot(fresh_grader, grader)
            self.assertIsNot(fresh_chunks, chunks)
            self.assertFalse(current.bridges)
            self.assertFalse(current.tunnels)
            self.assertNotIn("old-cell", fresh_grader.cells)
            self.assertIsNone(fresh_chunks.get((0, 0)))
        # A late completion owns its former dictionaries, never the new bundle.
        old.bridges[5] = "late old completion"
        old.graders[5].cells["late"] = []
        with blockworker._generation_scope(5, 40.0, -74.0) as again:
            self.assertIs(again, current)
            self.assertFalse(again.bridges)
            self.assertNotIn("late", again.graders[5].cells)

    def test_nested_scope_keeps_old_generation_until_job_ends(self):
        with blockworker._generation_scope(3, 40.0, -74.0) as old:
            self.state.dem.invalidation_epoch = 8
            with blockworker._generation_scope(3, 40.0, -74.0) as nested:
                self.assertIs(nested, old)
                self.assertIs(blockworker._state(), old)
        with blockworker._generation_scope(3, 40.0, -74.0) as upgraded:
            self.assertIsNot(upgraded, old)
        self.assertIs(blockworker._state(), self.state)

    def test_explicit_source_revision_and_settings_change_generation(self):
        with blockworker._generation_scope(1, 40.0, -74.0) as first:
            pass
        self.state.md.worldplan_revision = "roads-v2"
        with blockworker._generation_scope(1, 40.0, -74.0) as second:
            pass
        with mock.patch.object(blockworker.gradingmod, "ROAD_LIFT", 0.08):
            with blockworker._generation_scope(1, 40.0, -74.0) as third:
                pass
        self.assertEqual(len({id(first), id(second), id(third)}), 3)

    def test_geometric_job_wrapper_binds_once_and_resets_after_error(self):
        @blockworker._generation_job
        def job(aid, lat, lon):
            got = blockworker._state()
            self.state.dem.invalidation_epoch += 1
            with blockworker._generation_scope(aid, lat, lon):
                self.assertIs(blockworker._state(), got)
            raise ValueError("failed job")
        with self.assertRaisesRegex(ValueError, "failed job"):
            job(1, 40.0, -74.0)
        self.assertIs(blockworker._state(), self.state)

    def test_disabled_switch_and_simple_fixture_state_preserve_legacy(self):
        with mock.patch.dict(os.environ, BNGE_COMPILED_TERRAIN="0"):
            with blockworker._generation_scope(1, 40.0, -74.0) as state:
                self.assertIs(state, self.state)
        fake = SimpleNamespace(anchor=lambda *args: (None, None))
        with mock.patch.object(blockworker, "_s", fake):
            with blockworker._generation_scope(1, 40.0, -74.0) as state:
                self.assertIs(state, fake)

    def test_same_anchor_upgraded_dem_rebuilds_chunked_terrain_bytes(self):
        root = Path(self.temp.name)
        old_out, new_out = root / "old-job", root / "new-job"
        old_out.mkdir()
        new_out.mkdir()
        old_info = blockworker.build(9, 40.0, -74.0, 15, 1 / 64, 0, 0, 128,
                                     str(old_out), "height.png", "terrain.ter")
        with blockworker._generation_scope(9, 40.0, -74.0) as old:
            old_grader, old_chunks = old.anchor(9, 40.0, -74.0)
            self.assertTrue(old_chunks.chunks)
        self.state.dem.invalidation_epoch = 1
        self.state.dem.worldplan_revision = "upgraded-v2"
        self.state.dem.sample = lambda la, lo, *args: np.full(np.shape(la), 20.0)
        new_info = blockworker.build(9, 40.0, -74.0, 15, 1 / 64, 0, 0, 128,
                                     str(new_out), "height.png", "terrain.ter")
        self.assertNotEqual((old_out / "height.png").read_bytes(), (new_out / "height.png").read_bytes())
        self.assertNotEqual(old_info["compiled"]["key"], new_info["compiled"]["key"])
        self.assertGreater(new_info["groundZ"] - old_info["groundZ"], 9.9)
        with blockworker._generation_scope(9, 40.0, -74.0) as current:
            self.assertIsNot(current.graders[9], old_grader)
            self.assertIsNot(current.chunks[9], old_chunks)

    def test_worker_quality_change_during_build_is_reported_to_parent(self):
        root = Path(self.temp.name)
        output = root / "changed-during-job"
        output.mkdir()
        def sample(la, lo, *args):
            self.state.dem.invalidation_epoch = 1
            return np.full(np.shape(la), 20.0)
        self.state.dem.sample = sample
        info = blockworker.build(9, 40.0, -74.0, 15, 1 / 64, 0, 0, 128,
                                  str(output), "height.png", "terrain.ter")
        self.assertTrue(info["_worker_epoch_changed"])
        self.assertGreater(info["_worker_generation"], 0)


if __name__ == "__main__":
    unittest.main()
