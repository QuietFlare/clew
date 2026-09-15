"""--runs on a horus-lineage record: one run directory, or a root of them."""

import shutil
import tempfile
import unittest
from pathlib import Path

from clew.extract import runs

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class HorusRuns(unittest.TestCase):
    def test_a_single_run_directory(self):
        store = runs.Runs(FIXTURES / "horus_run")
        self.assertEqual(store.kind, "horus")
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
