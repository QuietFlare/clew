"""What this extractor emits is the one graph every question reads."""

import unittest
from pathlib import Path

from clew.graph.graph import STATUSES, contract_violations
from clew.provider.dnanexus import extractor_describe as dx

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "dnanexus"


class Contract(unittest.TestCase):
    def graphs(self):
        yield "dnanexus", dx.extract(dx.load_records(FIXTURES))

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
