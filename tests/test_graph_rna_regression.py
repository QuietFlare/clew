"""
Regression invariants on a real nf-core/rnaseq 3.26.0 run: 4 real yeast
samples (PRJNA589326, wild-type vs cox4Δ), iGenomes R64-1-1, 171 tasks,
extracted from the native lineage store (graph_rna.json).

Third pipeline through the shared nfcore adapter machinery, zero core or
adapter-machinery changes — these numbers pin that claim.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.core import blast_radius as core
from clew.domains import rnaseq

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "clew" / "data" / "graph_rna.json"
SHEET = ROOT / "clew" / "data" / "samplesheets" / "rnaseq_yeast.csv"


@unittest.skipUnless(GRAPH.exists() and SHEET.exists(), "rnaseq graph not present")
class TestRnaseqRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = core.load_graph(GRAPH)
        cls.subjects = rnaseq.load_subjects(SHEET)
        cls.entry = rnaseq.subject_entry_nodes(cls.graph, cls.subjects)
        cls.radius = core.blast_radius(cls.graph, cls.entry)

    def test_shape(self):
        self.assertEqual(len(self.graph["tasks"]), 171)
        self.assertEqual(len(self.graph["edges"]), 504)
        self.assertEqual(len(self.subjects), 4)

    def test_no_dangling_producers(self):
        known = set(self.graph["tasks"])
        dangling = [e for e in self.graph["edges"]
                    if e["producer"] not in known and e["producer"] != "EXTERNAL"]
        self.assertEqual(dangling, [])

    def test_every_sample_attributed(self):
        for sample, nodes in self.entry.items():
            self.assertEqual(len(nodes), 40, sample)

    def test_sample_withdrawal_radius(self):
        for sample, r in self.radius.items():
            # One cox4d sample carries a single extra exclusive task
            # (46 affected vs 45); the invariant that matters is the bound
            # and the shared aggregator count.
            self.assertIn(len(r["affected"]), (45, 46), sample)
            self.assertEqual(len(r["shared"]), 5, sample)

    def test_gtf_shallow_entry_deep_reach(self):
        subjects = rnaseq.external_input_entry_nodes(self.graph, "genes.gtf")
        entry = subjects["input:genes.gtf"]
        self.assertEqual(len(entry), 1)  # one direct consumer...
        radius = core.blast_radius(self.graph, subjects)
        # ...and 149 of 171 tasks in the radius. The annotation-bump story.
        self.assertEqual(len(radius["input:genes.gtf"]["affected"]), 149)

    def test_genome_radius(self):
        subjects = rnaseq.external_input_entry_nodes(self.graph, "genome.fa")
        radius = core.blast_radius(self.graph, subjects)
        self.assertEqual(len(radius["input:genome.fa"]["affected"]), 150)

    def test_aligner_vs_quantifier_asymmetry(self):
        # A STAR defect poisons nearly everything; a Salmon defect stays
        # contained. Same trigger type, order-of-magnitude different radius —
        # the reason per-tool blast radii matter.
        star = core.blast_radius(self.graph,
                                 rnaseq.container_entry_nodes(self.graph, "star"))
        salmon = core.blast_radius(self.graph,
                                   rnaseq.container_entry_nodes(self.graph, "salmon"))
        self.assertEqual(len(star["container:star"]["affected"]), 148)
        self.assertEqual(len(salmon["container:salmon"]["affected"]), 15)

    def test_every_load_bearing_input_present(self):
        seen = {Path(e["filename"]).name for e in self.graph["edges"]
                if e["producer"] == "EXTERNAL"}
        for name in rnaseq.LOAD_BEARING_INPUTS:
            self.assertIn(name, seen, name)


if __name__ == "__main__":
    unittest.main()
