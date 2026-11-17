"""Cache files are only loaded when their SHA-256 sidecar matches, and only by the engine release
that wrote them; env overrides are validated.

  python -m unittest discover -s tests -p "test_*.py"
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import countries, make_card, pipeline, resources  # noqa: E402


class CacheRelease(unittest.TestCase):
    """make_card.reset_cache: OSM reads cached by one engine release are never loaded by another
    (1.4 run after 1.6 crashed unpickling 1.6's boundaries)."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.work = os.path.join(self.dir.name, "work")
        os.mkdir(self.work)
        self.pbf = os.path.join(self.dir.name, "australia.osm.pbf")
        with open(self.pbf, "wb") as f:
            f.write(b"extract" * 1000)
        self.inputs = [(countries.AU, self.pbf)]
        self.read = os.path.join(self.work, f"{pipeline.READS[1]}_AU.pickle")

    def tearDown(self):
        self.dir.cleanup()

    def build(self, release):
        """One build's cache check by an engine of this release; True if it keeps the cached reads."""
        with mock.patch.object(make_card, "engine_release", return_value=release):
            kept = make_card.reset_cache(self.inputs, self.work, None)
        if not os.path.exists(self.read):
            pipeline.save_cache(self.read, {"read by": release})
        return kept

    def test_same_release_reuses_reads(self):
        self.build("1.6.1+abc")
        self.assertTrue(self.build("1.6.1+abc"))
        self.assertTrue(pipeline.cache_ok(self.read))

    def test_older_or_newer_release_reads_again(self):
        for before, after in (("1.6.1+abc", "1.4.0+def"), ("1.4.0+def", "1.6.1+abc")):
            with self.subTest(before=before, after=after):
                self.build(before)
                with mock.patch.object(make_card, "engine_release", return_value=after):
                    self.assertFalse(make_card.reset_cache(self.inputs, self.work, None))
                self.assertFalse(os.path.exists(self.read))

    def test_release_before_stamping_reads_again(self):
        # 1.6.0 and earlier wrote no release into the key; the first stamped release must not trust them
        self.build(None)
        with mock.patch.object(make_card, "engine_release", return_value="1.6.1+abc"):
            self.assertFalse(make_card.reset_cache(self.inputs, self.work, None))
        self.assertFalse(os.path.exists(self.read))

    def test_source_checkout_keeps_reads_across_edits(self):
        self.build(None)
        self.assertTrue(self.build(None))

    def test_old_boundaries_cache_is_dropped(self):
        # caches named by an older reader format (boundaries3: Area without `town`) are never loaded
        old = os.path.join(self.work, "boundaries3_AU.pickle")
        self.build(None)
        pipeline.save_cache(old, "1.5 Area layout")
        self.build(None)
        self.assertFalse(os.path.exists(old))
        self.assertIn("boundaries4", pipeline.READS)

    def test_unstamped_engine_has_no_release(self):
        self.assertIsNone(make_card.engine_release())   # this checkout has no openlion/_release.py


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
