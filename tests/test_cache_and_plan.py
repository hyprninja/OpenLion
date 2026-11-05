"""Cache files are only loaded when their SHA-256 sidecar matches; env overrides are validated.

  python -m unittest discover -s tests -p "test_*.py"
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import pipeline, resources  # noqa: E402


class CacheFiles(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "roads2_AU.pickle")

    def tearDown(self):
        self.dir.cleanup()

    def test_written_cache_is_reused(self):
        calls = []
        compute = lambda: calls.append(1) or {"a": 1}
        self.assertEqual(pipeline.cached(self.path, compute), {"a": 1})
        self.assertEqual(pipeline.cached(self.path, compute), {"a": 1})
        self.assertEqual(len(calls), 1)

    def test_changed_cache_is_recomputed(self):
        pipeline.save_cache(self.path, [1, 2, 3])
        with open(self.path, "ab") as f:
            f.write(b"tampered")
        self.assertFalse(pipeline.cache_ok(self.path))
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(pipeline.cached(self.path, lambda: "fresh"), "fresh")

    def test_cache_without_sidecar_is_not_loaded(self):
        with open(self.path, "wb") as f:
            f.write(b"not ours")
        with self.assertRaises(RuntimeError):
            pipeline.load_cache(self.path)
        self.assertFalse(os.path.exists(self.path))


class PlanOverrides(unittest.TestCase):
    GB = 1 << 30

    def plan(self, **env):
        with mock.patch.dict(os.environ, env, clear=False):
            return resources.make_plan(available=64 * self.GB, cpus=8)

    def test_bad_memory_mode(self):
        with self.assertRaises(ValueError):
            self.plan(OPENLION_MEMORY="lots")

    def test_bad_worker_count(self):
        with self.assertRaises(ValueError):
            self.plan(OPENLION_MEMORY="high", OPENLION_WORKERS="abc")

    def test_workers_clamped_to_cpus(self):
        p = self.plan(OPENLION_MEMORY="high", OPENLION_WORKERS="999")
        self.assertEqual((p.read_workers, p.fea_workers), (8, 8))


if __name__ == "__main__":
    unittest.main()
