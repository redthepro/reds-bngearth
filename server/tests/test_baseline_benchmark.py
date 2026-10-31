"""Offline benchmark contracts: fresh processes, cache reuse and unchanged artifacts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / 'tools' / 'profile_build.py'
STAGES = {
    'source_fetch', 'normalization', 'terrain_planning', 'road_planning',
    'compilation', 'serialization', 'queue_wait', 'engine_installation', 'activation',
}


class SyntheticBaselineTest(unittest.TestCase):
    """One cold/warm pair is shared across assertions; no timing thresholds in CI."""

    @classmethod
    def invoke(cls, cache, output, mode='cold', timings='1'):
        result_path = output / 'result.json'
        argv = [sys.executable, str(CLI), '--synthetic', '--cache-mode', mode,
                '--cache-dir', str(cache), '--output-dir', str(output), '--json', str(result_path)]
        env = os.environ.copy()
        env.update(BNGE_TIMINGS=timings, BNGE_TEST_OFFLINE='1', BNGE_SKIP_DRIVE='1',
                   PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run(argv, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, encoding='utf-8',
                                errors='replace', timeout=90)
        data = json.loads(result_path.read_text(encoding='utf-8')) if result_path.exists() else None
        return result, data

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='earth2-baseline-test-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.cache = cls.root / 'cache'
        cls.cold_process, cls.cold = cls.invoke(cls.cache, cls.root / 'cold')
        if cls.cold_process.returncode:
            raise AssertionError('cold benchmark failed:\n' + cls.cold_process.stdout)
        cls.warm_process, cls.warm = cls.invoke(cls.cache, cls.root / 'warm', 'warm_restart')
        if cls.warm_process.returncode:
            raise AssertionError('warm benchmark failed:\n' + cls.warm_process.stdout)

    def test_schema_has_explicit_measurement_boundaries(self):
        for data in (self.cold, self.warm):
            self.assertEqual(data['schema_version'], 1)
            self.assertTrue(data['success'])
            self.assertEqual(set(data['jobs']), STAGES)
            self.assertTrue(data['input']['synthetic'])
            self.assertTrue(data['input']['fixture_sha256'])
            self.assertTrue(data['metadata']['processing_sha256'])
            self.assertEqual(len(data['metadata']['source_commit']), 40)
            self.assertGreater(data['metadata']['pid'], 0)
            for stage in ('engine_installation', 'activation'):
                self.assertIsNone(data['jobs'][stage]['wall_s'])
                self.assertIsNone(data['jobs'][stage]['process_cpu_s'])
                self.assertTrue(data['jobs'][stage]['reason'])

    def test_fresh_process_restart_preserves_outputs(self):
        self.assertNotEqual(self.cold['metadata']['pid'], self.warm['metadata']['pid'])
        for key in ('fixture_revision', 'fixture_sha256', 'source_release', 'source_versions', 'cell'):
            self.assertEqual(self.cold['input'][key], self.warm['input'][key])
        self.assertEqual(self.cold['cache']['normalized_digest'], self.warm['cache']['normalized_digest'])
        self.assertEqual(self.cold['outputs']['sha256'], self.warm['outputs']['sha256'])
        cold_files = [(f['bytes'], f['sha256']) for f in self.cold['outputs']['files']]
        warm_files = [(f['bytes'], f['sha256']) for f in self.warm['outputs']['files']]
        self.assertEqual(cold_files, warm_files)
        self.assertGreater(self.cold['outputs']['bytes'], 0)
        self.assertTrue(self.warm['cache']['baseline_digest_match'])
        self.assertNotEqual(self.cold['cache']['plan_run'], self.warm['cache']['plan_run'])

    def test_warm_restart_has_real_cache_hit(self):
        self.assertGreater(self.cold['cache']['transport_requests'], 0)
        self.assertEqual(self.warm['cache']['transport_requests'], 0)
        self.assertFalse(self.cold['cache']['layer_stats']['roads']['hit'])
        self.assertTrue(self.warm['cache']['layer_stats']['roads']['hit'])

    def test_instrumentation_switch_preserves_artifacts(self):
        process, data = self.invoke(self.cache, self.root / 'disabled', 'warm_restart', '0')
        self.assertEqual(process.returncode, 0, process.stdout)
        self.assertFalse(data['metadata']['timings_enabled'])
        self.assertEqual(self.cold['outputs']['sha256'], data['outputs']['sha256'])

    def test_cold_refuses_existing_cache_without_deletion(self):
        cache = self.root / 'nonempty'
        cache.mkdir()
        sentinel = cache / 'keep.txt'
        sentinel.write_text('preserve me', encoding='utf-8')
        process, _ = self.invoke(cache, self.root / 'refused')
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'preserve me')

    def test_warm_refuses_missing_baseline(self):
        process, _ = self.invoke(self.root / 'missing', self.root / 'no-baseline', 'warm_restart')
        self.assertNotEqual(process.returncode, 0)

    def test_refuses_overlapping_cache_and_output(self):
        cache = self.root / 'overlap'
        process, _ = self.invoke(cache, cache / 'output')
        self.assertNotEqual(process.returncode, 0)


class WorkerAccountingTest(unittest.TestCase):
    def test_opt_in_preserves_results_errors_and_existing_cpu_accounting(self):
        script = r'''
import json, sys
sys.path.insert(0, 'server')
import blockworker
@blockworker._accounted
def ok():
    return {'value': 7, 'stages': {'heights': 0.25, 'ignored': 3}}
@blockworker._accounted
def bad():
    raise ValueError('same error')
blockworker.timing_snapshot(reset=True)
assert ok()['value'] == 7
try:
    bad()
except ValueError as exc:
    assert str(exc) == 'same error'
else:
    raise AssertionError('exception swallowed')
print(json.dumps({'snapshot': blockworker.timing_snapshot(), 'cpu_counts': {k: v[0] for k, v in blockworker._cpu.items()}}))
'''
        for mode in ('0', '1'):
            env = os.environ.copy()
            env.update(BNGE_TIMINGS=mode, PYTHONDONTWRITEBYTECODE='1')
            process = subprocess.run([sys.executable, '-c', script], cwd=ROOT, env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     encoding='utf-8', errors='replace', timeout=30)
            self.assertEqual(process.returncode, 0, process.stdout)
            data = json.loads(process.stdout)
            self.assertEqual(data['cpu_counts'], {'ok': 1, 'bad': 1})
            snapshot = data['snapshot']
            self.assertEqual(snapshot['enabled'], mode == '1')
            if mode == '0':
                self.assertEqual(snapshot['jobs'], {})
            else:
                self.assertEqual(snapshot['jobs']['ok']['count'], 1)
                self.assertEqual(snapshot['jobs']['bad']['errors'], 1)
                self.assertEqual(snapshot['jobs']['ok']['stages'], {'heights': 0.25})
                self.assertEqual(set(snapshot['jobs']), {'ok', 'bad'})


if __name__ == '__main__':
    unittest.main()
