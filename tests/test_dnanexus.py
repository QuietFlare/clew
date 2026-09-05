"""
DNAnexus extractor over a saved analysis (fixtures/dnanexus/): four jobs,
a reference and reads uploaded by a user, one file link nobody described.
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew import extract_from_dnanexus as dx
from clew.core import blast_radius as core
from clew.core.graph import contract_violations, external_input_entry_nodes

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "dnanexus"

ALIGN = "job-ALIGN00000000000000001"
FASTQC = "job-FASTQC0000000000000001"
CALL = "job-CALL000000000000000001"
REPORT = "job-REPORT0000000000000001"


def edges_into(graph, consumer):
    return {e["filename"]: e["producer"]
            for e in graph["edges"] if e["consumer"] == consumer}


class Extraction(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.graph = dx.extract(dx.load_records(FIXTURES))

    def test_conforms_to_the_contract(self):
        self.assertEqual(contract_violations(self.graph), [])

    def test_one_task_per_job_keyed_by_job_id(self):
        self.assertEqual(set(self.graph["tasks"]), {ALIGN, FASTQC, CALL, REPORT})

    def test_user_uploads_are_external(self):
        self.assertEqual(edges_into(self.graph, ALIGN),
                         {"sample_1.fastq.gz": "EXTERNAL", "genome.fa": "EXTERNAL"})

    def test_a_job_output_is_the_producer(self):
        self.assertEqual(edges_into(self.graph, CALL)["sample_1.bam"], ALIGN)

    def test_array_inputs_are_followed(self):
        into = edges_into(self.graph, REPORT)
        self.assertEqual(into["sample_1.vcf.gz"], CALL)
        self.assertEqual(into["sample_1_fastqc.html"], FASTQC)

    def test_same_file_linked_twice_is_one_edge(self):
        bams = [e for e in self.graph["edges"]
                if e["consumer"] == CALL and e["target"].startswith("file-BAM")]
        self.assertEqual(len(bams), 1)

    def test_undescribed_file_is_external_under_its_id(self):
        into = edges_into(self.graph, REPORT)
        self.assertEqual(into["file-MISSING000000000000001"], "EXTERNAL")

    def test_edge_target_is_the_file_id(self):
        edge = next(e for e in self.graph["edges"]
                    if e["consumer"] == CALL and e["filename"] == "sample_1.bam")
        self.assertEqual(edge["target"], "file-BAM0000000000000000001")

    def test_outputs_by_name(self):
        self.assertEqual(self.graph["outputs"][ALIGN], ["sample_1.bam"])
        self.assertEqual(self.graph["outputs"][REPORT], ["report.html"])

    def test_status_is_upper_case_state(self):
        self.assertEqual(self.graph["tasks"][CALL]["status"], "DONE")

    def test_container_names_the_executable(self):
        self.assertEqual(self.graph["tasks"][CALL]["container"],
                         "gatk_haplotypecaller@app-gatk")

    def test_price_and_duration_when_present(self):
        self.assertEqual(self.graph["tasks"][CALL]["price"], 0.35)
        self.assertEqual(self.graph["tasks"][CALL]["duration_s"], 1800)
        self.assertNotIn("price", self.graph["tasks"][REPORT])
        self.assertEqual(self.graph["tasks"][REPORT]["duration_s"], 60)


class Triggers(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.graph = dx.extract(dx.load_records(FIXTURES))

    def affected_by(self, filename):
        entry = external_input_entry_nodes(self.graph, filename)
        radius = core.blast_radius(self.graph, entry)
        return set(radius[f"input:{filename}"]["affected"])

    def test_reference_update_reaches_align_call_and_report(self):
        self.assertEqual(self.affected_by("genome.fa"), {ALIGN, CALL, REPORT})

    def test_fastqc_is_clean_under_a_reference_update(self):
        self.assertNotIn(FASTQC, self.affected_by("genome.fa"))


class LinkWalking(unittest.TestCase):

    def test_scalars_and_non_file_links_are_ignored(self):
        value = {"n": 3, "s": "x", "proj": {"$dnanexus_link": "project-X"},
                 "rec": {"$dnanexus_link": {"id": "record-1"}}}
        self.assertEqual(dx.link_ids(value), [])

    def test_nested_links_in_order(self):
        value = {"a": [{"$dnanexus_link": "file-1"}, {"b": {"$dnanexus_link": {"id": "file-2"}}}]}
        self.assertEqual(dx.link_ids(value), ["file-1", "file-2"])


class Cli(unittest.TestCase):

    def test_records_dir_writes_a_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "graph.json"
            with contextlib.redirect_stdout(io.StringIO()):
                code = dx.main(["--records", str(FIXTURES), "--json-out", str(out)])
            self.assertEqual(code, 0)
            graph = json.loads(out.read_text())
            self.assertEqual(contract_violations(graph), [])

    def test_analysis_without_token_fails_cleanly(self):
        import os
        saved = {k: os.environ.pop(k, None)
                 for k in ("DX_SECURITY_CONTEXT", "DX_API_TOKEN")}
        try:
            self.assertEqual(dx.main(["--analysis", "analysis-A"]), 2)
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
