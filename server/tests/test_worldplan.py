"""Canonical facts, anchored inputs, explicit uncertainty and immutable generations."""
import copy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import geodesy
import mapcells
import worldplan
from test_roads import features


class WorldPlanTest(unittest.TestCase):
    def setUp(self):
        self.frame = geodesy.EnuFrame(39.2834, -76.5934)
        self.key = mapcells.cell_of(39.2834, -76.5934)
        self.roads = features([[(-100, 0), (100, 0)]], ["residential"])
        self.roads.attrs['width'] = np.array([np.nan])
        self.cell = SimpleNamespace(layers={'roads': self.roads}, versions={'roads': 4},
                                    provenance=[{'license': 'fixture', 'timestamp': '2026-01-01'}],
                                    digest=lambda: 'a' * 64)
        self.calls = []
        def cell(key, **kw):
            self.calls.append(key)
            return self.cell
        self.md = SimpleNamespace(cell=cell)

    def descriptor(self, frame=None, context=None, **kw):
        return worldplan.build_descriptor(frame or self.frame, 0, 0, 4, 1 / 64,
            {'qgrid': kw.pop('qgrid', np.zeros((4, 4), np.uint16)), 'mask_idx': None},
            context=context, settings=kw)

    def test_missing_facts_preserve_geometry_provenance_and_missing_semantics(self):
        context = worldplan.capture_context(mapdata=self.md, dependency_keys=[self.key])
        self.assertEqual(context['errors'], [])
        fact = context['facts'][0]
        self.assertEqual(fact['roads']['attr.width']['data'], [{'missing': 'nan'}])
        self.assertEqual(fact['roads']['lonlat']['shape'], [2, 2])
        self.assertEqual(fact['provenance'], self.cell.provenance)
        self.assertEqual(fact['source_digest'], 'a' * 64)
        worldplan.canonical_bytes(context)  # JSON contains no non-standard NaN.

    def test_request_identity_does_not_depend_on_anchor_history(self):
        grader = SimpleNamespace(md=self.md, cells={str(i): [] for i in range(1000)})
        context = worldplan.capture_context(grader, dependency_keys=[self.key])
        self.assertEqual(self.calls, [self.key])
        self.calls.clear()
        worldplan.capture_context(grader)
        self.assertEqual(self.calls, [])
        self.assertEqual(len(context['facts']), 1)

    def test_budget_does_not_claim_truncated_halo_complete(self):
        keys = [mapcells.CellKey(14, i, 1) for i in range(worldplan.MAX_FACT_CELLS + 1)]
        context = worldplan.capture_context(mapdata=self.md, dependency_keys=keys)
        self.assertFalse(context['dependency_halo']['closed'])
        self.assertTrue(context['errors'])
        self.assertEqual(self.calls, [])

    def test_anchor_change_reuses_fact_identity_but_not_prepared_transform(self):
        context = worldplan.capture_context(mapdata=self.md, dependency_keys=[self.key])
        old = self.descriptor(context=context)
        new = self.descriptor(frame=geodesy.EnuFrame(39.29, -76.59), context=context)
        self.assertEqual(old['canonical_id'], new['canonical_id'])
        self.assertNotEqual(old['prepared_id'], new['prepared_id'])
        self.assertFalse(new['decision_plan_persisted'])

    def test_neighbor_revision_provenance_and_settings_invalidate(self):
        context = worldplan.capture_context(mapdata=self.md, dependency_keys=[self.key])
        old = self.descriptor(context=context)
        upgraded = copy.deepcopy(context)
        upgraded['facts'][0]['versions']['roads'] += 1
        self.assertNotEqual(old['canonical_id'], self.descriptor(context=upgraded)['canonical_id'])
        upgraded = copy.deepcopy(context)
        upgraded['facts'][0]['provenance'][0]['license'] = 'changed'
        self.assertNotEqual(old['canonical_id'], self.descriptor(context=upgraded)['canonical_id'])
        self.assertNotEqual(old['prepared_id'], self.descriptor(context=context, lod='changed')['prepared_id'])
        self.assertNotEqual(old['prepared_id'], self.descriptor(context=context, qgrid=np.ones((4, 4), np.uint16))['prepared_id'])

    def test_incomplete_or_unknown_is_never_a_final_decision(self):
        descriptor = self.descriptor()
        self.assertEqual(descriptor['input_quality'], 'provisional')
        self.assertFalse(descriptor['decision_plan_persisted'])
        self.assertEqual(descriptor['canonical']['support']['bridge_navigation'], 'unknown')
        with self.assertRaises(ValueError):
            self.descriptor(qgrid=np.full((4, 4), np.nan))

    def test_late_old_work_cannot_repopulate_new_generation(self):
        store = worldplan.GenerationStore()
        def factory(serial):
            return SimpleNamespace(serial=serial, plans={}, chunks={}, tunnels={}, bridges={})
        old = store.acquire('anchor', {'dem_epoch': 1}, factory)
        new = store.acquire('anchor', {'dem_epoch': 2}, factory)
        old.plans['late'] = 'old-data'
        self.assertIs(store.acquire('anchor', {'dem_epoch': 2}, factory), new)
        self.assertNotEqual(old.serial, new.serial)
        for name in ('plans', 'chunks', 'tunnels', 'bridges'):
            self.assertIsNot(getattr(old, name), getattr(new, name))
        self.assertEqual(new.plans, {})

    def test_algorithm_fingerprint_changes_compiled_identity(self):
        before = self.descriptor()
        identity = copy.deepcopy(worldplan.compiler_identity())
        identity['files']['grading.py'] = 'b' * 64
        with mock.patch.object(worldplan, 'compiler_identity', return_value=identity):
            after = self.descriptor()
        self.assertNotEqual(before['prepared_id'], after['prepared_id'])
        for name in ('grading.py', 'dem.py', 'roads.py', 'geodesy.py', 'airportgrade.py', 'seacarve.py'):
            self.assertIn(name, before['prepared']['compiler']['files'])


if __name__ == '__main__':
    unittest.main()
