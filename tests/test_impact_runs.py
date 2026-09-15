"""impact --runs reads the engine's record and leaves the derived graph beside the plan."""

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.questions import impact

DATA = Path(__file__).resolve().parent.parent / "clew" / "data"


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        impact.main(list(argv))
    return out.getvalue()


class TestRuns(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy(DATA / "graph5.json", self.tmp / "cohort.json")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_a_directory_of_graphs_needs_no_extractor(self):
        plan = self.tmp / "plan.json"
        out = run("--runs", str(self.tmp), "--container", "gatk4", "--json", str(plan))
        self.assertIn("graph derived from", out)
        derived = self.tmp / "plan.graph.json"
        self.assertTrue(derived.exists())
        self.assertEqual(json.loads(derived.read_text())["run"]["name"], "cohort")
        self.assertEqual(len(json.loads(plan.read_text())["plan"]), 68)

    def test_graph_and_runs_together_are_refused(self):
        with self.assertRaises(SystemExit) as stop:
            run("--graph", str(self.tmp / "cohort.json"), "--runs", str(self.tmp), "--container", "gatk4")
        self.assertIn("not both", str(stop.exception))

    def test_neither_is_refused(self):
        with self.assertRaises(SystemExit) as stop:
            run("--container", "gatk4")
        self.assertIn("--graph or --runs", str(stop.exception))


if __name__ == "__main__":
    unittest.main()
