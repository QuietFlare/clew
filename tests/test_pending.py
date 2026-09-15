"""Unattended runs: the adapter says what is pending, impact answers each."""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.contracts import Adapter
from clew.contracts.adapter import check
from clew.provider.nextflow import adapter_sarek as sarek
from clew.questions import impact

DATA = Path(__file__).resolve().parent.parent / "clew" / "data"
GRAPH = str(DATA / "graph5.json")
SHEET = str(DATA / "donors.csv")


class Site(sarek.Sarek):
    """Sarek plus what a site adds: where the sheet is, what is pending."""
    name = "sarek-test-site"
    queue = []
    triggers = {"patient": sarek.base.SheetKind("patient", members="sample",
                                                 locate=lambda graph: SHEET)}

    def pending(self):
        return list(self.queue)


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = impact.main(["--pipeline", "sarek-test-site", *argv])
    return code, out.getvalue(), err.getvalue()


class TestPending(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        Adapter.registered.pop("sarek-test-site", None)

    def test_each_pending_trigger_is_answered(self):
        Site.queue = [
            {"kind": "patient", "value": "donor_003", "asserted_by": "qa", "date": "2026-09-10"},
            {"kind": "container", "value": "gatk4"},
        ]
        code, out, _ = run("--graph", GRAPH)
        self.assertEqual(code, 0)
        self.assertIn("TRIGGER patient:donor_003  asserted by qa on 2026-09-10", out)
        self.assertIn("TRIGGER container:gatk4", out)
        self.assertIn("2 triggers, 0 failed", out)

    def test_a_bad_trigger_fails_its_answer_not_the_batch(self):
        Site.queue = [{"kind": "container", "value": "no-such-image"},
                      {"kind": "container", "value": "gatk4"}]
        code, out, err = run("--graph", GRAPH)
        self.assertEqual(code, 1)
        self.assertIn("FAILED container:no-such-image", err)
        self.assertIn("2 triggers, 1 failed", out)

    def test_nothing_pending_and_nothing_asked_is_refused(self):
        Site.queue = []
        with self.assertRaises(SystemExit) as stop:
            run("--graph", GRAPH)
        self.assertIn("nothing to ask", str(stop.exception))

    def test_an_explicit_question_wins_over_pending(self):
        Site.queue = [{"kind": "container", "value": "gatk4"}]
        code, out, _ = run("--graph", GRAPH, "--container", "bcftools")
        self.assertIsNone(code)
        self.assertNotIn("triggers, ", out)

    def test_each_answer_gets_its_own_output_file(self):
        Site.queue = [{"kind": "container", "value": "gatk4"}]
        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "plan.json"
            code, _, _ = run("--graph", GRAPH, "--json", str(plan))
            self.assertEqual(code, 0)
            self.assertTrue((Path(tmp) / "plan.container-gatk4.json").exists())

    def test_malformed_trigger_is_refused_before_anything_runs(self):
        self.assertEqual(check({"kind": "subject", "value": "x"}), [])
        self.assertTrue(check({"kind": "subject"}))
        self.assertTrue(check("subject:x"))

    def test_adapter_can_find_the_sheet_itself(self):
        Site.queue = []
        code, out, _ = run("--graph", GRAPH, "--trigger", "patient:donor_003")
        self.assertIsNone(code)
        self.assertIn("donor_003", out)


if __name__ == "__main__":
    unittest.main()
