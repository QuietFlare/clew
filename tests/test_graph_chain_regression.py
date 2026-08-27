"""
Regression invariants on the first CROSS-RUN graph: a real nf-core/rnaseq
run (171 tasks) stitched to a real nf-core/differentialabundance run
(12 tasks) at the published count matrix.

This is the demonstration engine-level lineage cannot make: one withdrawal
whose radius crosses a pipeline boundary, with the bridge checkable.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.core import blast_radius as core
from clew.domains import rnaseq

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "clew" / "data" / "graph_chain.json"
SHEET = ROOT / "clew" / "data" / "samplesheets" / "rnaseq_yeast.csv"


@unittest.skipUnless(GRAPH.exists(), "stitched chain graph not present")
class TestCrossRunChain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = core.load_graph(GRAPH)
        cls.subjects = rnaseq.load_subjects(SHEET)
        cls.entry = rnaseq.subject_entry_nodes(cls.graph, cls.subjects)
        cls.radius = core.blast_radius(cls.graph, cls.entry)

    def test_shape(self):
        self.assertEqual(len(self.graph["tasks"]), 183)  # 171 + 12
        self.assertEqual(len(self.graph["edges"]), 567)

    def test_exactly_one_bridge(self):
        # The count matrix is the only artifact that crosses the runs: one
        # EXTERNAL edge rewritten to an rna-task producer inside a da-task.
        bridges = [e for e in self.graph["edges"]
                   if e["consumer"].startswith("da:")
                   and e["producer"] not in (None, "EXTERNAL")
                   and e["producer"].startswith("rna:")]
        self.assertEqual(len(bridges), 1)
        self.assertEqual(Path(bridges[0]["filename"]).name,
                         "salmon.merged.gene_counts.tsv")

    def test_withdrawal_crosses_the_boundary(self):
        r = self.radius["SRR10441036_cox4d"]
        self.assertEqual(len(r["affected"]), 57)  # 46 in rna + 11 in da
        da_reached = {h for h in r["affected"] if h.startswith("da:")}
        self.assertEqual(len(da_reached), 11)
        # The report bundle — the artifact people actually receive — is in
        # the radius. This is the row that makes the plan legible.
        names = {self.graph["tasks"][h]["process"].rsplit(":", 1)[-1]
                 for h in da_reached}
        self.assertIn("MAKE_REPORT_BUNDLE", names)
        self.assertIn("DESEQ2_DIFFERENTIAL", names)

    def test_every_sample_reaches_the_de_run(self):
        # The matrix mixes ALL samples, so every withdrawal crosses over —
        # the many-into-one aggregation working across a run boundary.
        for sample, r in self.radius.items():
            self.assertTrue(any(h.startswith("da:") for h in r["affected"]),
                            sample)

    def test_evidence_chain_spans_both_runs(self):
        forward = core.forward_index(self.graph["edges"])
        entry = self.entry["SRR10441036_cox4d"]
        target = next(h for h, t in self.graph["tasks"].items()
                      if h.startswith("da:")
                      and t["process"].endswith("DESEQ2_DIFFERENTIAL"))
        paths = core.paths_to(entry, target, forward, limit=1)
        self.assertTrue(paths)
        labels = {h.split(":", 1)[0] for h in paths[0]}
        self.assertEqual(labels, {"rna", "da"})


if __name__ == "__main__":
    unittest.main()
