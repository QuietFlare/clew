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

    def test_something_else_is_refused(self):
        empty = Path(tempfile.mkdtemp())
        try:
            with self.assertRaises(SystemExit):
                runs.Runs(empty)
        finally:
            shutil.rmtree(empty)


class HorusRuns(unittest.TestCase):
    def test_a_single_run_directory(self):
        store = runs.Runs(FIXTURES / "horus_run")
        self.assertEqual(store.kind, "horus-run")
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
