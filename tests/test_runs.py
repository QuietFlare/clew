"""
--runs reads the engine's record directly and keeps only a digest sidecar
beside it. Kinds are detected from what is on disk; a sidecar written by
clew digest is merged back on the next load.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.extract import runs

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def graph(name):
    return {"tasks": {"aa/1": {"hash": "aa/1", "name": name, "process": name,
                               "container": "", "status": "", "script": "", "workdir": ""}},
            "edges": [], "outputs": {"aa/1": ["out.txt"]},
            "output_details": {"aa/1": [{"file": "out.txt", "size": 1}]}}


class GraphDirectory(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "first.json").write_text(json.dumps(graph("A")))
        (self.root / "second.json").write_text(json.dumps(graph("B")))
        import os, time
        os.utime(self.root / "second.json", (time.time() + 10, time.time() + 10))

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_kind_and_latest(self):
        store = runs.Runs(self.root)
        self.assertEqual(store.kind, "graphs")
        self.assertEqual([n for n, _, _ in store.names()], ["first", "second"])
        self.assertEqual(store.load()["run"]["name"], "second")
        self.assertEqual(store.load("first")["tasks"]["aa/1"]["name"], "A")

    def test_an_ambiguous_name_is_refused(self):
        (self.root / "fir.json").write_text(json.dumps(graph("C")))
        with self.assertRaises(SystemExit):
            runs.Runs(self.root).resolve("fi")

    def test_sidecar_round_trip(self):
        store = runs.Runs(self.root)
        g = store.load("first")
        g["output_details"]["aa/1"][0]["digest"] = "sha256:abc"
        g["published"] = {"out/out.txt": {"size": 1, "digest": "sha256:abc"}}
        path = store.save_sidecar(g)
        self.assertEqual(path, self.root / ".clew" / "first.digests.json")
        again = runs.Runs(self.root).load("first")
        self.assertEqual(again["output_details"]["aa/1"][0]["digest"], "sha256:abc")
        self.assertEqual(again["published"]["out/out.txt"]["digest"], "sha256:abc")
        self.assertEqual(runs.Runs(self.root).load("second").get("published"), None)

    def test_an_engine_sha256_is_not_overwritten_by_the_sidecar(self):
        g = graph("A")
        g["output_details"]["aa/1"][0]["digest"] = "sha256:engine"
        runs.merge_sidecar(g, {"outputs": {"aa/1": {"out.txt": "sha256:sidecar"}}})
        self.assertEqual(g["output_details"]["aa/1"][0]["digest"], "sha256:engine")

    def test_latest_reads_a_recorded_timestamp_before_mtime(self):
        # `touch` reorders mtimes; a timestamp inside the record does not move.
        import os, time
        early = graph("C")
        early["run"] = {"timestamp": "2026-01-01T00:00:00Z"}
        late = graph("D")
        late["run"] = {"timestamp": "2026-06-01T00:00:00Z"}
        (self.root / "early.json").write_text(json.dumps(early))
        (self.root / "late.json").write_text(json.dumps(late))
        os.utime(self.root / "early.json", (time.time() + 100, time.time() + 100))
        store = runs.Runs(self.root)
        self.assertEqual([r["name"] for r in store.records()][:2], ["early", "late"])
        self.assertFalse(store.records()[1]["by_mtime"])
        self.assertTrue(store.records()[-1]["by_mtime"])

    def test_the_mtime_fallback_is_said_out_loud(self):
        import contextlib, io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(runs.Runs(self.root).resolve(), ("second", "second"))
        self.assertIn("modification time", err.getvalue())

    def test_something_else_is_refused(self):
        empty = Path(tempfile.mkdtemp())
        try:
            with self.assertRaises(SystemExit):
                runs.Runs(empty)
        finally:
            shutil.rmtree(empty)


class StoreRuns(unittest.TestCase):
    """A store graph is one resume chain, so its sidecar is keyed by session."""

    FIRST, SECOND, OTHER = "a" * 32, "b" * 32, "c" * 32

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        history = self.root / ".history"
        history.mkdir()
        for run_hash, name, session, day in (
                (self.FIRST, "first_run", "session-1", 1),
                (self.SECOND, "second_run", "session-1", 2),
                (self.OTHER, "other_run", "session-2", 3)):
            (history / run_hash).write_text(
                f"2026-08-0{day} 10:00:00 CEST\t{name}\t{session}\tlid://{run_hash}\n")

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_both_runs_of_a_chain_share_one_sidecar(self):
        store = runs.Runs(self.root)
        self.assertEqual(store.sidecar_path(self.FIRST),
                         self.root / ".clew" / "session-1.digests.json")
        self.assertEqual(store.sidecar_path(self.FIRST), store.sidecar_path(self.SECOND))
        self.assertNotEqual(store.sidecar_path(self.FIRST), store.sidecar_path(self.OTHER))

    def test_a_digest_written_under_one_run_name_is_read_under_the_other(self):
        store = runs.Runs(self.root)
        g = store.load("first_run")
        g["output_details"] = {"aa/1": [{"file": "out.txt", "digest": "sha256:abc"}]}
        store.save_sidecar(g)
        again = runs.Runs(self.root).load("second_run")
        self.assertEqual(again["output_details"]["aa/1"][0]["digest"], "sha256:abc")
        self.assertNotIn("aa/1", runs.Runs(self.root).load("other_run").get("output_details", {}))

    def test_a_sidecar_filed_under_a_run_hash_is_still_read(self):
        # Sidecars written before the session key existed sit under the
        # run hash. Digests already on disk must keep counting.
        legacy = self.root / ".clew"
        legacy.mkdir()
        (legacy / f"{self.SECOND}.digests.json").write_text(json.dumps(
            {"clew_sidecar_version": 1,
             "outputs": {"aa/1": {"out.txt": "sha256:old"}},
             "published": {"p.txt": {"digest": "sha256:old", "size": 1}}}))
        g = runs.Runs(self.root).load("first_run")
        self.assertEqual(g["output_details"]["aa/1"][0]["digest"], "sha256:old")
        self.assertEqual(g["published"]["p.txt"]["digest"], "sha256:old")

class LineageStoreRuns(unittest.TestCase):
    """A session-id prefix names a resume chain; its newest run stands for it."""

    def setUp(self):
        from tests.test_lineage_store import RUN_A, RUN_B, RUN_C, CHAIN, OTHER
        self.root = Path(tempfile.mkdtemp())
        history = self.root / ".history"
        history.mkdir()
        (history / RUN_A).write_text(f"2026-08-01 10:00:00 CEST\tfirst_run\t{CHAIN}\tlid://{RUN_A}\n")
        (history / RUN_B).write_text(f"2026-08-02 10:00:00 CEST\tsecond_run\t{CHAIN}\tlid://{RUN_B}\n")
        (history / RUN_C).write_text(f"2026-08-03 10:00:00 CEST\tother_run\t{OTHER}\tlid://{RUN_C}\n")
        self.run_b = RUN_B

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_a_session_prefix_resolves_to_the_chain_s_newest_run(self):
        store = runs.Runs(self.root)
        self.assertEqual(store.resolve("session-ch"), ("second_run", self.run_b))
        self.assertEqual(store.resolve("bbbb"), ("second_run", self.run_b))

    def test_a_prefix_spanning_two_sessions_is_still_ambiguous(self):
        with self.assertRaises(SystemExit):
            runs.Runs(self.root).resolve("session-")


class HorusRuns(unittest.TestCase):
    def test_a_single_run_directory(self):
        store = runs.Runs(FIXTURES / "horus_run")
        self.assertEqual(store.kind, "horus-run")
        self.assertEqual(store.records()[0]["timestamp"], "2026-09-02T09:01:34.050766+00:00")
        g = store.load()
        self.assertEqual(g["run"]["name"], "horus_run")
        self.assertTrue(any(d.get("digest", "").startswith("sha256:")
                            for ds in g["output_details"].values() for d in ds))

    def test_a_root_of_run_directories(self):
        root = Path(tempfile.mkdtemp())
        try:
            shutil.copytree(FIXTURES / "horus_run", root / "run-1")
            shutil.copytree(FIXTURES / "horus_run", root / "run-2")
            store = runs.Runs(root)
            self.assertEqual(store.kind, "horus")
            self.assertEqual(sorted(n for n, _, _ in store.names()), ["run-1", "run-2"])
            self.assertEqual(store.sidecar_path("run-1"), root / ".clew" / "run-1.digests.json")
        finally:
            shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main()
