"""
Regression invariants on a real nf-core/viralrecon 2.6.0 run: 5 COG-UK
SARS-CoV-2 specimens (public ENA accessions), ARTIC V3 amplicon protocol,
219 tasks, extracted from the native lineage store (graph_vr.json).

This is the first pipeline the nfcore adapter machinery never saw during
development — these numbers pin the proof that it generalised.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import blast_radius as core
from domains import viralrecon

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "graph_vr.json"
SHEET = ROOT / "samplesheets" / "viralrecon_coguk.csv"


@unittest.skipUnless(GRAPH.exists() and SHEET.exists(), "viralrecon graph not present")
class TestViralreconRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = core.load_graph(GRAPH)
        cls.subjects = viralrecon.load_subjects(SHEET)
        cls.entry = viralrecon.subject_entry_nodes(cls.graph, cls.subjects)
        cls.radius = core.blast_radius(cls.graph, cls.entry)

    def test_shape(self):
        self.assertEqual(len(self.graph["tasks"]), 219)
        self.assertEqual(len(self.graph["edges"]), 533)
        self.assertEqual(len(self.subjects), 5)

    def test_no_dangling_producers(self):
        known = set(self.graph["tasks"])
        dangling = [e for e in self.graph["edges"]
                    if e["producer"] not in known and e["producer"] != "EXTERNAL"]
        self.assertEqual(dangling, [])

    def test_every_specimen_attributed(self):
        # 41 entry tasks per specimen; zero would mean the tag parser
        # silently failed on this pipeline's naming — the false-negative
        # direction.
        for specimen, nodes in self.entry.items():
            self.assertEqual(len(nodes), 41, specimen)

    def test_specimen_withdrawal_radius(self):
        for specimen, r in self.radius.items():
            self.assertEqual(len(r["affected"]), 46, specimen)
            self.assertEqual(len(r["exclusive"]), 41, specimen)
            self.assertEqual(len(r["shared"]), 5, specimen)

    def test_primer_scheme_is_load_bearing(self):
        subjects = viralrecon.external_input_entry_nodes(
            self.graph, "nCoV-2019.primer.bed")
        entry = subjects["input:nCoV-2019.primer.bed"]
        self.assertEqual(len(entry), 11)
        radius = core.blast_radius(self.graph, subjects)
        self.assertEqual(len(radius["input:nCoV-2019.primer.bed"]["affected"]), 161)

    def test_reference_reaches_furthest(self):
        subjects = viralrecon.external_input_entry_nodes(
            self.graph, "nCoV-2019.reference.fasta")
        radius = core.blast_radius(self.graph, subjects)
        self.assertEqual(
            len(radius["input:nCoV-2019.reference.fasta"]["affected"]), 193)

    def test_ivar_defect_radius(self):
        subjects = viralrecon.container_entry_nodes(self.graph, "ivar")
        radius = core.blast_radius(self.graph, subjects)
        self.assertEqual(len(radius["container:ivar"]["affected"]), 160)

    def test_every_load_bearing_input_present(self):
        # The adapter's declared list must match what the store recorded;
        # a renamed file in a future release should fail here, loudly.
        from pathlib import Path as P
        seen = {P(e["filename"]).name for e in self.graph["edges"]
                if e["producer"] == "EXTERNAL"}
        for name in viralrecon.LOAD_BEARING_INPUTS:
            self.assertIn(name, seen, name)


if __name__ == "__main__":
    unittest.main()
