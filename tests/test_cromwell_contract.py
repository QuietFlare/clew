"""What this extractor emits is the one graph every question reads."""

import unittest
from pathlib import Path

from clew.graph.graph import STATUSES, contract_violations
from clew.provider.cromwell import extractor_metadata as cw

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cromwell"


class Contract(unittest.TestCase):
    def graphs(self):
        yield "diamond", cw.extract(cw.load_metadata(FIXTURES / "diamond.json"))
        yield "subworkflow", cw.extract(cw.load_metadata(FIXTURES / "outer.json"))

    def test_conforms(self):
        for label, graph in self.graphs():
            with self.subTest(graph=label):
                self.assertEqual(contract_violations(graph), [])

    def test_statuses_are_in_the_vocabulary(self):
        for label, graph in self.graphs():
            with self.subTest(graph=label):
                for task in graph["tasks"].values():
                    self.assertIn(task["status"], STATUSES)


if __name__ == "__main__":
    unittest.main()
