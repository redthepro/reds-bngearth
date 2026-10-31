"""Compiled artifacts: integrity, atomic publication, deduplication and active pins."""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import compiledcache


class CompiledCacheTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="compiled-cache-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = compiledcache.ArtifactCache(self.root / "cache")
        self.key = hashlib.sha256(b"exact prepared input").hexdigest()
        self.descriptor = {"schema": 1, "quality": "provisional", "source": "fixture-v1"}

    def materialize(self, cache=None, key=None, output="out", body=b"compiled fixture", builder=None, descriptor=None):
        return (cache or self.cache).materialize(key or self.key, descriptor or self.descriptor,
            builder or (lambda: {"terrain.bin": body}), {"terrain.bin": self.root / output / "terrain.bin"})

    def test_new_instance_hit_preserves_bytes_without_recompiling(self):
        first = self.materialize()
        self.assertFalse(first["hit"])
        self.assertTrue(first["stored"])
        other = compiledcache.ArtifactCache(self.cache.root)
        second = self.materialize(other, output="restart", builder=lambda: self.fail("cache hit compiled again"))
        self.assertTrue(second["hit"])
        self.assertEqual((self.root / "out/terrain.bin").read_bytes(), (self.root / "restart/terrain.bin").read_bytes())

    def test_fresh_process_hits_persisted_valid_artifact(self):
        self.materialize()
        code = """import json,sys
from compiledcache import ArtifactCache
def fail():
    raise AssertionError('restart tried to compile')
print(json.dumps(ArtifactCache(sys.argv[1]).materialize(sys.argv[2],json.loads(sys.argv[3]),fail,
    {'terrain.bin': sys.argv[4]})))
"""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(compiledcache.__file__).parent)
        destination = self.root / "fresh-process/terrain.bin"
        result = subprocess.run([sys.executable, "-c", code, str(self.cache.root), self.key,
                                 json.dumps(self.descriptor), str(destination)], env=env,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["hit"])
        self.assertEqual(destination.read_bytes(), b"compiled fixture")

    def test_threads_deduplicate_and_receive_complete_payload(self):
        calls = []
        results = []
        lock = threading.Lock()
        def build():
            with lock:
                calls.append(1)
            time.sleep(0.06)
            return {"terrain.bin": b"same bytes"}
        def run(i):
            results.append(self.materialize(output=f"thread-{i}", builder=build))
        threads = [threading.Thread(target=run, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
            self.assertFalse(t.is_alive())
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(results), 4)
        self.assertEqual(sum(r["hit"] for r in results), 3)
        for i in range(4):
            self.assertEqual((self.root / f"thread-{i}/terrain.bin").read_bytes(), b"same bytes")

    def test_corrupt_truncated_and_nonobject_manifest_rebuild(self):
        for index, body in enumerate((b"partial", struct.pack("<8sQ", compiledcache.MAGIC, 2) + b"[]",
                                      struct.pack("<8sQ", compiledcache.MAGIC, 4) + b"null")):
            self.cache.root.mkdir(exist_ok=True)
            self.cache._path(self.key).write_bytes(body)
            result = self.materialize(output=f"rebuilt-{index}")
            self.assertFalse(result["hit"])
            self.assertTrue(result["stored"])
        self.assertGreater(self.cache.snapshot()["invalid"], 0)

    def test_payload_checksum_and_descriptor_mismatch_rebuild(self):
        self.materialize()
        path = self.cache._path(self.key)
        body = bytearray(path.read_bytes())
        body[-1] ^= 1
        path.write_bytes(body)
        self.assertFalse(self.materialize(output="checksum")["hit"])
        self.assertFalse(self.materialize(output="new-settings", descriptor={**self.descriptor, "lod": 2})["hit"])

    def test_recursive_manifest_is_a_cache_miss(self):
        body = b"[" * 2000 + b"]" * 2000
        self.cache.root.mkdir()
        self.cache._path(self.key).write_bytes(struct.pack("<8sQ", compiledcache.MAGIC, len(body)) + body)
        self.assertFalse(self.materialize()["hit"])

    def test_interrupted_publication_has_no_partial_final_entry(self):
        self.cache.root.mkdir()
        with mock.patch.object(compiledcache.os, "replace", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.cache._publish(self.key, self.descriptor, {"terrain.bin": b"data"})
        self.assertFalse(self.cache._path(self.key).exists())
        self.assertFalse(list(self.cache.root.glob("*.tmp")))

    def test_oversized_entry_serves_fresh_without_persisting(self):
        cache = compiledcache.ArtifactCache(self.root / "small", cap_bytes=128, max_entry_bytes=128)
        result = self.materialize(cache, body=b"x" * 256)
        self.assertFalse(result["stored"])
        self.assertEqual((self.root / "out/terrain.bin").read_bytes(), b"x" * 256)
        self.assertFalse(list(cache.root.glob("*.artifact")))

    def test_eviction_is_bounded_and_respects_active_pins(self):
        cache = compiledcache.ArtifactCache(self.root / "bounded", cap_bytes=1100)
        other_key = hashlib.sha256(b"second input").hexdigest()
        self.assertTrue(self.materialize(cache, body=b"a" * 600)["stored"])
        with cache._pin(self.key):
            blocked = self.materialize(cache, other_key, "blocked", body=b"b" * 600)
            self.assertFalse(blocked["stored"])
            self.assertTrue(cache._path(self.key).exists())
        published = self.materialize(cache, other_key, "after-pin", body=b"b" * 600)
        self.assertTrue(published["stored"])
        self.assertFalse(cache._path(self.key).exists())
        self.assertLessEqual(sum(p.stat().st_size for p in cache.root.glob("*.artifact")), cache.cap_bytes)

    def test_crossprocess_pin_is_not_stolen_by_pruner(self):
        cache = compiledcache.ArtifactCache(self.root / "xproc", cap_bytes=1100)
        self.materialize(cache, body=b"a" * 600)
        code = """import sys
from compiledcache import ArtifactCache
with ArtifactCache(sys.argv[1])._pin(sys.argv[2]):
    print('PINNED', flush=True)
    sys.stdin.readline()
"""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(compiledcache.__file__).parent)
        child = subprocess.Popen([sys.executable, "-c", code, str(cache.root), self.key], env=env,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "PINNED")
            key = hashlib.sha256(b"crossprocess second").hexdigest()
            self.assertFalse(self.materialize(cache, key, "cross-pin", body=b"b" * 600)["stored"])
            self.assertTrue(cache._path(self.key).exists())
        finally:
            child.communicate("release\n", timeout=10)
        self.assertEqual(child.returncode, 0)

    def test_unlocked_orphan_pin_and_temporary_are_cleaned(self):
        self.materialize()
        orphan_pin = self.cache.root / "pins" / f"{self.key}.9999.orphan.pin"
        orphan_pin.write_bytes(b"")
        orphan = self.cache.root / f".{self.key}.9999.orphan.tmp"
        orphan.write_bytes(b"aborted")
        self.cache.prune()
        self.assertFalse(orphan.exists())
        self.assertFalse(orphan_pin.exists())

    def test_old_temporary_is_removed_while_new_same_key_writer_is_pinned(self):
        self.materialize()
        orphan_pin = self.cache.root / "pins" / f"{self.key}.9999.old.pin"
        orphan_pin.write_bytes(b"")
        orphan = self.cache.root / f".{self.key}.9999.old.tmp"
        orphan.write_bytes(b"aborted earlier writer")
        with self.cache._pin(self.key):
            self.cache.prune()
            self.assertFalse(orphan.exists())
            self.assertFalse(orphan_pin.exists())
            self.assertTrue(self.cache._path(self.key).exists())

    def test_invalid_key_and_role_cannot_select_paths(self):
        with self.assertRaises(ValueError):
            self.cache.materialize("../escape", {}, lambda: {}, {"x": self.root / "x"})
        with self.assertRaises(ValueError):
            self.cache.materialize(self.key, {}, lambda: {}, {"../escape": self.root / "x"})


if __name__ == "__main__":
    unittest.main()
