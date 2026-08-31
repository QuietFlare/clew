"""
The RO-Crate adapter, on a real Workflow Run RO-Crate produced by nf-prov
(fixtures/ro-crate-metadata.json): a 5-task run with two samples, one
shared reference, and an aggregator.

Third ingest path, same graph schema, same engine — plus the honest
consequences of what a crate does NOT record.
"""

import sys
import tempfile
import json
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew import extract_from_rocrate as rc
from clew.core import blast_radius as core
from clew.domains import viralrecon  # sample-keyed adapter; the crate has no donors

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CRATE = FIXTURES / "ro-crate-metadata.json"
SHEET = FIXTURES / "rocrate_samples.csv"


@unittest.skipUnless(CRATE.exists(), "crate fixture missing")
class TestRoCrateAdapter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = rc.extract(CRATE)
        cls.subjects = viralrecon.load_subjects(SHEET)
        cls.entry = viralrecon.subject_entry_nodes(cls.graph, cls.subjects)
        cls.radius = core.blast_radius(cls.graph, cls.entry)

    def test_shape(self):
        # 2 ALIGN + 2 STATS + 1 MERGE_REPORT; the run-level CreateAction
        # must NOT be counted as a task.
        self.assertEqual(len(self.graph["tasks"]), 5)
        self.assertEqual(len(self.graph["edges"]), 8)
        self.assertNotIn("", self.graph["tasks"])

    def test_no_dangling_producers(self):
        known = set(self.graph["tasks"])
        dangling = [e for e in self.graph["edges"]
                    if e["producer"] not in known and e["producer"] != "EXTERNAL"]
        self.assertEqual(dangling, [])

    def test_tag_attribution_works_on_crate_names(self):
        # nf-prov writes the nf-core convention: "ALIGN (sample_beta)".
        for sample, nodes in self.entry.items():
            self.assertEqual(len(nodes), 2, sample)

    def test_aggregator_is_shared(self):
        for sample, r in self.radius.items():
            self.assertEqual(len(r["affected"]), 3, sample)
            self.assertEqual(len(r["exclusive"]), 2, sample)
            (shared,) = r["shared"]
            self.assertEqual(viralrecon.describe(self.graph, shared),
                             "MERGE_REPORT")

    def test_shared_reference_reaches_everything(self):
        subjects = viralrecon.external_input_entry_nodes(self.graph, "ref.fa")
        self.assertEqual(len(subjects["input:ref.fa"]), 2)
        radius = core.blast_radius(self.graph, subjects)
        self.assertEqual(len(radius["input:ref.fa"]["affected"]), 5)

    def test_missing_script_fails_closed(self):
        # A crate records what ran, not how to re-run it. With no script
        # recorded, classification must fall to IRREDUCIBLE rather than
        # optimistically claiming the task is regenerable.
        task_hash = next(iter(self.graph["tasks"]))
        facts = viralrecon.classify(self.graph, task_hash, exclusive=False)
        self.assertEqual(facts["contribution"], "IRREDUCIBLE")
        # But NOT destroyed. A crate records no workdir at all, so there is
        # nothing to look at — which is unverified, not gone. The previous
        # behaviour reported every crate-derived task as ALREADY_GONE, so a
        # whole extractor silently produced empty remediation plans.
        self.assertIsNone(facts["storage"])

    def test_external_inputs_recognised(self):
        external = {Path(e["filename"]).name for e in self.graph["edges"]
                    if e["producer"] == "EXTERNAL"}
        self.assertEqual(external,
                         {"ref.fa", "sample_alpha.fq", "sample_beta.fq"})


class TestRealCrateExternalForms(unittest.TestCase):
    """
    The first real nf-prov crate recorded external inputs as https:// URLs,
    relative results/ paths and #tmp entries — not the file:/// form the
    fixture taught this extractor to expect. All 117 were silently dropped,
    so a reference-update trigger found nothing and read as clean. Any
    object that is not another task's output must become an EXTERNAL edge.
    """

    def test_non_task_objects_become_external_edges(self):
        crate = {"@graph": [
            {"@id": "#task/aaaabbbbccccddddaaaabbbbccccdddd",
             "@type": "CreateAction", "name": "ALIGN (s1)",
             "object": [
                 {"@id": "https://example.org/ref/genome.fasta"},
                 {"@id": "results/prior_run/counts.tsv"},
                 {"@id": "#tmp/workflow_summary_mqc.yaml"},
                 {"@id": "#param/vep_version"},
             ],
             "result": []},
        ]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ro-crate-metadata.json"
            path.write_text(json.dumps(crate))
            graph = rc.extract(str(path))
        externals = {e["filename"]: e["target"]
                     for e in graph["edges"] if e["producer"] == "EXTERNAL"}
        self.assertEqual(set(externals), {"genome.fasta", "counts.tsv",
                                          "workflow_summary_mqc.yaml"})
        self.assertEqual(externals["counts.tsv"], "results/prior_run/counts.tsv")

if __name__ == "__main__":
    unittest.main()
