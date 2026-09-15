"""What this extractor emits is the one graph every question reads."""

import unittest
from pathlib import Path

from clew.graph.graph import STATUSES, contract_violations
from clew.provider.nextflow import extractor_rocrate as rc

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class Contract(unittest.TestCase):
    def graphs(self):
        yield "ro-crate", rc.extract(FIXTURES / "ro-crate-metadata.json")

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
