"""
Every extractor emits the same graph, and everything downstream assumes
its shape. The shipped graphs are checked here; each provider checks its
own extractor's output in its own tests.
"""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from importlib.metadata import version
from clew.graph.graph import STATUSES, contract_violations, task_status

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "clew" / "data"

SHIPPED = ["graph5.json", "graph_rna.json", "graph_vr.json",
           "graph_da.json", "graph_chain.json"]


class ShippedGraphsConform(unittest.TestCase):

    def test_every_shipped_graph(self):
        for name in SHIPPED:
            with self.subTest(graph=name):
                graph = json.loads((DATA / name).read_text())
                self.assertEqual(contract_violations(graph), [])


class ContractCatchesBreakage(unittest.TestCase):

    def setUp(self):
        self.graph = json.loads((DATA / "graph5.json").read_text())

    def test_conforming_graph_has_no_problems(self):
        self.assertEqual(contract_violations(self.graph), [])

    def test_dangling_producer(self):
        self.graph["edges"][0]["producer"] = "no/such"
        self.assertTrue(any("producer" in p for p in contract_violations(self.graph)))

    def test_self_edge(self):
        edge = self.graph["edges"][0]
        edge["producer"] = edge["consumer"]
        self.assertTrue(any("feeds itself" in p for p in contract_violations(self.graph)))

    def test_lower_case_status(self):
        first = next(iter(self.graph["tasks"].values()))
        first["status"] = "completed"
        self.assertTrue(any("upper-case" in p for p in contract_violations(self.graph)))

    def test_missing_task_field(self):
        first = next(iter(self.graph["tasks"].values()))
        del first["container"]
        self.assertTrue(any("container" in p for p in contract_violations(self.graph)))

    def test_outputs_for_unknown_task(self):
        self.graph["outputs"]["no/such"] = ["x"]
        self.assertTrue(any("not a task" in p for p in contract_violations(self.graph)))

    def test_not_a_graph(self):
        self.assertEqual(len(contract_violations({})), 3)


class StatusVocabulary(unittest.TestCase):
    """
    Every engine's word lands in one of four states, and a word nobody
    listed is UNKNOWN rather than FAILED, because FAILED is deletable.
    """

    def test_engine_words_map_to_core_states(self):
        for word, status in (("Done", "COMPLETED"), ("SUCCEEDED", "COMPLETED"),
                             ("completed", "COMPLETED"), ("skipped", "CACHED"),
                             ("CACHED", "CACHED"), ("Failed", "FAILED"),
                             ("RetryableFailure", "FAILED"), ("terminated", "FAILED"),
                             ("RUNNING", "UNKNOWN"), ("waiting_on_input", "UNKNOWN"),
                             ("", ""), (None, "")):
            with self.subTest(word=word):
                self.assertEqual(task_status(word), status)


class VersionMatchesPackaging(unittest.TestCase):

    def test_installed_metadata_and_pyproject_agree(self):
        # A checkout is installed editable; a stale install fails here, not in the field.
        text = (ROOT / "pyproject.toml").read_text()
        declared = re.search(r'^version = "([^"]+)"', text, re.M).group(1)
        self.assertEqual(version("clew-lineage"), declared)


if __name__ == "__main__":
    unittest.main()
