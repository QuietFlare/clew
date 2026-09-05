"""
Every extractor emits the same graph, and everything downstream assumes
its shape. This runs the contract over every shipped graph and every
fixture-built one, so a new extractor joins by adding one line.
"""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import clew
from clew import extract_from_dnanexus as dx
from clew import extract_from_horus as hz
from clew import extract_from_latch as lt
from clew import extract_from_rocrate as rc
from clew.core.graph import contract_violations

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "clew" / "data"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

SHIPPED = ["graph5.json", "graph_rna.json", "graph_vr.json",
           "graph_da.json", "graph_chain.json"]


def fixture_graphs():
    yield "horus", hz.extract(FIXTURES / "horus_run")
    yield "rocrate", rc.extract(FIXTURES / "ro-crate-metadata.json")
    yield "dnanexus", dx.extract(dx.load_records(FIXTURES / "dnanexus"))
    yield "latch", lt.extract(lt.load_records(FIXTURES / "latch"))


class ShippedGraphsConform(unittest.TestCase):

    def test_every_shipped_graph(self):
        for name in SHIPPED:
            with self.subTest(graph=name):
                graph = json.loads((DATA / name).read_text())
                self.assertEqual(contract_violations(graph), [])


class ExtractedGraphsConform(unittest.TestCase):

    def test_every_extractor(self):
        for name, graph in fixture_graphs():
            with self.subTest(extractor=name):
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


class VersionMatchesPackaging(unittest.TestCase):

    def test_init_and_pyproject_agree(self):
        text = (ROOT / "pyproject.toml").read_text()
        declared = re.search(r'^version = "([^"]+)"', text, re.M).group(1)
        self.assertEqual(clew.__version__, declared)


if __name__ == "__main__":
    unittest.main()
