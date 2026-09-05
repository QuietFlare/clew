"""
Latch extractor over a saved execution (fixtures/latch/): four tasks, a
reference and reads from outside the execution, one shared file nobody
in it produced.
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew import extract_from_latch as lt
from clew.core import blast_radius as core
from clew.core.graph import contract_violations, external_input_entry_nodes

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "latch"

ALIGN, FASTQC, CALL, REPORT = "1001", "1002", "1003", "1004"


def edges_into(graph, consumer):
    return {e["filename"]: e["producer"]
            for e in graph["edges"] if e["consumer"] == consumer}


class Extraction(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.graph = lt.extract(lt.load_records(FIXTURES))

    def test_conforms_to_the_contract(self):
        self.assertEqual(contract_violations(self.graph), [])

    def test_one_task_per_graph_node(self):
        self.assertEqual(set(self.graph["tasks"]), {ALIGN, FASTQC, CALL, REPORT})

    def test_outside_files_are_external(self):
        self.assertEqual(edges_into(self.graph, ALIGN),
                         {"sample_1.fastq.gz": "EXTERNAL", "genome.fa": "EXTERNAL"})

    def test_a_task_output_is_the_producer(self):
        self.assertEqual(edges_into(self.graph, CALL)["sample_1.bam"], ALIGN)

    def test_collections_are_followed(self):
        into = edges_into(self.graph, REPORT)
        self.assertEqual(into["sample_1.vcf.gz"], CALL)
        self.assertEqual(into["sample_1_fastqc.html"], FASTQC)
        self.assertEqual(into["metadata.csv"], "EXTERNAL")

    def test_same_path_twice_is_one_edge(self):
        bams = [e for e in self.graph["edges"]
                if e["consumer"] == CALL and e["filename"] == "sample_1.bam"]
        self.assertEqual(len(bams), 1)

    def test_edge_target_is_the_latch_path(self):
        edge = next(e for e in self.graph["edges"]
                    if e["consumer"] == CALL and e["filename"] == "sample_1.bam")
        self.assertEqual(edge["target"], "latch:///demo/out/align/sample_1.bam")

    def test_outputs_by_name(self):
        self.assertEqual(self.graph["outputs"][ALIGN], ["sample_1.bam"])
        self.assertEqual(self.graph["outputs"][REPORT], ["report.html"])

    def test_names_process_container_and_script(self):
        task = self.graph["tasks"][CALL]
        self.assertEqual(task["name"], "call")
        self.assertEqual(task["process"], "gatk_haplotypecaller")
        self.assertTrue(task["container"].startswith("wf.wgs@sha256:"))
        self.assertEqual(task["script"], "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678")
        self.assertEqual(task["status"], "SUCCEEDED")

    def test_price_prefers_cost_then_price(self):
        self.assertEqual(self.graph["tasks"][ALIGN]["price"], 0.12)
        self.assertEqual(self.graph["tasks"][CALL]["price"], 0.30)
        self.assertNotIn("price", self.graph["tasks"][REPORT])

    def test_duration_from_timestamps(self):
        self.assertEqual(self.graph["tasks"][CALL]["duration_s"], 1800)
        self.assertEqual(self.graph["tasks"][REPORT]["duration_s"], 60)


class Triggers(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.graph = lt.extract(lt.load_records(FIXTURES))

    def affected_by(self, filename):
        entry = external_input_entry_nodes(self.graph, filename)
        radius = core.blast_radius(self.graph, entry)
        return set(radius[f"input:{filename}"]["affected"])

    def test_reference_update_reaches_align_call_and_report(self):
        self.assertEqual(self.affected_by("genome.fa"), {ALIGN, CALL, REPORT})

    def test_fastqc_is_clean_under_a_reference_update(self):
        self.assertNotIn(FASTQC, self.affected_by("genome.fa"))


class PathWalking(unittest.TestCase):

    def test_only_latch_paths_are_files(self):
        value = {"literals": {"n": {"scalar": {"primitive": {"integer": 3}}},
                              "s": {"scalar": {"primitive": {"stringValue": "s3://x"}}},
                              "f": {"scalar": {"blob": {"uri": "latch:///a/b.txt"}}}}}
        self.assertEqual(lt.latch_paths(value), ["latch:///a/b.txt"])

    def test_missing_timestamps_give_no_duration(self):
        self.assertIsNone(lt.seconds_between(None, "2026-09-01T10:00:00Z"))


class Cli(unittest.TestCase):

    def test_records_dir_writes_a_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "graph.json"
            with contextlib.redirect_stdout(io.StringIO()):
                code = lt.main(["--records", str(FIXTURES), "--json-out", str(out)])
            self.assertEqual(code, 0)
            self.assertEqual(contract_violations(json.loads(out.read_text())), [])

    def test_execution_without_token_fails_cleanly(self):
        import os
        saved = os.environ.pop("LATCH_TOKEN", None)
        original = lt.TOKEN_FILE
        lt.TOKEN_FILE = Path("/nonexistent/token")
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(lt.main(["--execution", "1"]), 2)
        finally:
            lt.TOKEN_FILE = original
            if saved is not None:
                os.environ["LATCH_TOKEN"] = saved


if __name__ == "__main__":
    unittest.main()
