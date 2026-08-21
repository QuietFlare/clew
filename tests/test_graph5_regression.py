"""
Regression invariants on the real 5-donor sarek run (graph5.json).

These pin the numbers the demo claims. If an extractor or adapter change
moves any of them, that is either a real improvement (update the number,
say why in the commit) or a reintroduced false negative (the expensive kind).
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import blast_radius as core
from domains import sarek

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "graph5.json"
SHEET = ROOT / "donors.csv"


@unittest.skipUnless(GRAPH.exists(), "real-run graph not present")
class TestFiveDonorRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = core.load_graph(GRAPH)
        cls.donors = sarek.load_donors(SHEET)
        cls.entry = sarek.subject_entry_nodes(cls.graph, cls.donors)
        cls.radius = core.blast_radius(cls.graph, cls.entry)

    def test_shape(self):
        self.assertEqual(len(self.graph["tasks"]), 81)
        self.assertEqual(len(self.graph["edges"]), 344)
        self.assertEqual(len(self.donors), 5)

    def test_no_dangling_producers(self):
        known = set(self.graph["tasks"])
        dangling = [e for e in self.graph["edges"]
                    if e["producer"] not in known and e["producer"] != "EXTERNAL"]
        self.assertEqual(dangling, [])

    def test_every_donor_has_entry_points(self):
        # Zero entry points for a listed donor means attribution silently
        # failed — the false-negative direction.
        for donor, nodes in self.entry.items():
            self.assertEqual(len(nodes), 15, donor)

    def test_withdrawal_radius_per_donor(self):
        for donor, r in self.radius.items():
            self.assertEqual(len(r["affected"]), 16, donor)
            self.assertEqual(len(r["exclusive"]), 15, donor)
            # The single shared node is the aggregator.
            (shared,) = r["shared"]
            self.assertEqual(sarek.describe(self.graph, shared), "MULTIQC")

    def test_aggregator_sees_every_donor(self):
        # MULTIQC must be reachable from all five donors. This was the bug
        # that reported 110+ donor-derived files as clean.
        for donor, r in self.radius.items():
            self.assertTrue(
                any(sarek.describe(self.graph, h) == "MULTIQC"
                    for h in r["affected"]),
                f"{donor} does not reach MULTIQC",
            )

    def test_reference_update_is_load_bearing(self):
        subjects = sarek.external_input_entry_nodes(self.graph, "genome.fasta")
        entry = subjects["input:genome.fasta"]
        self.assertEqual(len(entry), 41)

        radius = core.blast_radius(self.graph, subjects)
        affected = radius["input:genome.fasta"]["affected"]
        # The reference reaches most of the run — far beyond any one donor.
        self.assertEqual(len(affected), 72)
        biggest_donor = max(len(r["affected"]) for r in self.radius.values())
        self.assertGreater(len(affected), biggest_donor)

    def test_tool_defect_radius(self):
        subjects = sarek.container_entry_nodes(self.graph, "gatk4")
        radius = core.blast_radius(self.graph, subjects)
        self.assertEqual(len(radius["container:gatk4"]["affected"]), 68)

    def test_donor_reference_asymmetry(self):
        # Withdrawing a donor must NOT pull in the reference-only tasks:
        # one withdrawal must never poison every sample ever aligned.
        ref_entry = set(
            sarek.external_input_entry_nodes(self.graph, "genome.fasta")["input:genome.fasta"]
        )
        for donor, r in self.radius.items():
            self.assertLess(len(r["affected"] & ref_entry), len(ref_entry), donor)


if __name__ == "__main__":
    unittest.main()
