"""An adapter that knows a step may say its class; the plan records that it did."""

import io
import json
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.contracts import Adapter
from clew.provider.nextflow import adapter_sarek as sarek
from clew.questions import impact

GRAPH = str(Path(__file__).resolve().parent.parent / "clew" / "data" / "graph5.json")
MULTIQC = "c9/023b13"


class Knows(sarek.Sarek):
    name = "sarek-test-classes"
    answer = None

    def contribution(self, graph, task_hash, kind):
        type(self).asked_kind = kind
        return self.answer if task_hash == MULTIQC else None


def plan_items(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        impact.main(["--pipeline", "sarek-test-classes", "--graph", GRAPH,
                     "--container", "gatk4", "--json", "-", *argv])
    text = out.getvalue()
    return {i["task"]: i for i in json.loads(text[text.index("{"):])["plan"]}


class TestClassHook(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        Adapter.registered.pop("sarek-test-classes", None)

    def test_adapter_answer_replaces_evidence_and_is_recorded(self):
        Knows.answer = "SEPARABLE"
        item = plan_items()[MULTIQC]
        self.assertEqual(item["contribution"], "SEPARABLE")
        self.assertEqual(item["class_asserted_by"], "sarek-test-classes")
        self.assertIn("evidence alone said REGENERABLE", item["reason"])
        self.assertEqual(Knows.asked_kind, "container")

    def test_none_keeps_the_evidence_answer(self):
        Knows.answer = None
        item = plan_items()[MULTIQC]
        self.assertEqual(item["contribution"], "REGENERABLE")
        self.assertNotIn("class_asserted_by", item)

    def test_an_unknown_class_is_refused_by_name(self):
        Knows.answer = "REMOVABLE"
        with self.assertRaises(SystemExit) as stop:
            plan_items()
        self.assertIn("REMOVABLE", str(stop.exception))
        self.assertIn("SEPARABLE, REGENERABLE, IRREDUCIBLE", str(stop.exception))


if __name__ == "__main__":
    unittest.main()
