"""
The Cromwell adapter, on metadata written by Cromwell 92 for two real
runs (fixtures/cromwell/).

diamond.json: a scatter over two samples, joined by a report.

    raw.csv -> prep -+-> analyse (shard 0) -+-> report
                     |-> analyse (shard 1) -+
                     +-> qc ----------------+
    calibration.txt ---> analyse (both shards)
    reference.txt --------------------------> report

outer.json: the same idea through an imported subworkflow, which Cromwell
records as a wrapper call carrying the inner workflow's metadata.
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.extract import cromwell as cw
from clew.graph import blast_radius as core
from clew.graph.graph import contract_violations, external_input_entry_nodes

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cromwell"

PREP, QC, REPORT = "diamond.prep", "diamond.qc", "diamond.report"
SHARD0, SHARD1 = "diamond.analyse/shard-0", "diamond.analyse/shard-1"


def edges_into(graph, consumer):
    return {e["filename"]: e["producer"]
            for e in graph["edges"] if e["consumer"] == consumer}


class Scatter(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.graph = cw.extract(cw.load_metadata(FIXTURES / "diamond.json"))

    def test_conforms_to_the_contract(self):
        self.assertEqual(contract_violations(self.graph), [])

    def test_one_node_per_call_and_shard(self):
        self.assertEqual(set(self.graph["tasks"]),
                         {PREP, QC, REPORT, SHARD0, SHARD1})

    def test_workflow_inputs_are_external(self):
        self.assertEqual(edges_into(self.graph, PREP), {"raw.csv": "EXTERNAL"})
        self.assertEqual(edges_into(self.graph, SHARD0),
                         {"prepped.csv": PREP, "calibration.txt": "EXTERNAL"})

    def test_an_array_input_joins_to_every_shard(self):
        into = edges_into(self.graph, REPORT)
        self.assertEqual(into["s1.analysis.txt"], SHARD0)
        self.assertEqual(into["s2.analysis.txt"], SHARD1)
        self.assertEqual(into["qc.txt"], QC)
        self.assertEqual(into["reference.txt"], "EXTERNAL")

    def test_a_string_input_is_not_an_edge(self):
        """
        analyse takes sample = "s1". Without a slash it cannot be a file.
        """
        self.assertNotIn("s1", edges_into(self.graph, SHARD0))

    def test_edge_target_is_the_absolute_path(self):
        edge = next(e for e in self.graph["edges"]
                    if e["consumer"] == QC and e["filename"] == "prepped.csv")
        self.assertTrue(edge["target"].startswith("/data/wdl/cromwell-executions/"))

    def test_outputs_by_name(self):
        self.assertEqual(self.graph["outputs"][PREP], ["prepped.csv"])
        self.assertEqual(self.graph["outputs"][SHARD1], ["s2.analysis.txt"])

    def test_re_execution_evidence(self):
        task = self.graph["tasks"][REPORT]
        self.assertEqual(task["status"], "COMPLETED")
        self.assertEqual(task["engine_status"], "Done")
        self.assertTrue(task["script"].startswith("cat "))
        self.assertTrue(task["workdir"].endswith("/call-report"))
        self.assertEqual(task["name"], "diamond.report")
        self.assertEqual(self.graph["tasks"][SHARD0]["name"],
                         "diamond.analyse (shard 0)")
        self.assertGreater(task["duration_s"], 0)

    def test_no_container_reads_as_empty(self):
        """
        The run declared no docker image, so the field is empty rather
        than invented. A declared image or its resolved digest fills it.
        """
        self.assertEqual(self.graph["tasks"][PREP]["container"], "")


class Subworkflow(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.graph = cw.extract(cw.load_metadata(FIXTURES / "outer.json"))

    def test_the_wrapper_call_is_flattened(self):
        self.assertEqual(set(self.graph["tasks"]),
                         {"outer.prep", "outer.finish", "outer.inner/pair.analyse"})

    def test_edges_cross_the_subworkflow_boundary(self):
        self.assertEqual(edges_into(self.graph, "outer.inner/pair.analyse"),
                         {"prepped.csv": "outer.prep", "calibration.txt": "EXTERNAL"})
        self.assertEqual(edges_into(self.graph, "outer.finish"),
                         {"sub.analysis.txt": "outer.inner/pair.analyse"})

    def test_an_unexpanded_subworkflow_stays_visible(self):
        """
        Fetched without expandSubWorkflows, the wrapper carries only an id.
        It is kept as a node and marked, never dropped.
        """
        metadata = cw.load_metadata(FIXTURES / "outer.json")
        wrapper = metadata["calls"]["outer.inner"][0]
        wrapper["subWorkflowId"] = wrapper.pop("subWorkflowMetadata")["id"]
        graph = cw.extract(metadata)
        self.assertEqual(contract_violations(graph), [])
        self.assertIn("outer.inner", graph["tasks"])
        self.assertEqual(graph["tasks"]["outer.inner"]["unexpanded_subworkflow"],
                         wrapper["subWorkflowId"])
        self.assertEqual(edges_into(graph, "outer.finish"),
                         {"sub.analysis.txt": "outer.inner"})


class Attempts(unittest.TestCase):

    def test_only_the_last_attempt_of_a_shard_counts(self):
        metadata = cw.load_metadata(FIXTURES / "diamond.json")
        first = dict(metadata["calls"]["diamond.prep"][0])
        first.update(attempt=1, executionStatus="RetryableFailure")
        metadata["calls"]["diamond.prep"][0]["attempt"] = 2
        metadata["calls"]["diamond.prep"].insert(0, first)
        graph = cw.extract(metadata)
        self.assertEqual(graph["tasks"][PREP]["status"], "COMPLETED")
        self.assertEqual(len([t for t in graph["tasks"] if t.startswith(PREP)]), 1)

    def test_a_cache_hit_is_marked(self):
        metadata = cw.load_metadata(FIXTURES / "diamond.json")
        metadata["calls"]["diamond.qc"][0]["callCaching"] = {"hit": True}
        graph = cw.extract(metadata)
        self.assertTrue(graph["tasks"][QC]["cached"])
        self.assertNotIn("cached", graph["tasks"][PREP])


class Triggers(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.graph = cw.extract(cw.load_metadata(FIXTURES / "diamond.json"))

    def affected_by(self, filename):
        entry = external_input_entry_nodes(self.graph, filename)
        radius = core.blast_radius(self.graph, entry)
        return set(radius[f"input:{filename}"]["affected"])

    def test_a_calibration_update_spares_qc(self):
        self.assertEqual(self.affected_by("calibration.txt"),
                         {SHARD0, SHARD1, REPORT})

    def test_the_raw_input_reaches_everything(self):
        self.assertEqual(self.affected_by("raw.csv"),
                         {PREP, QC, SHARD0, SHARD1, REPORT})


class Values(unittest.TestCase):

    def test_strings_are_found_in_nested_values(self):
        value = {"a": ["/x/1", {"left": "/x/2", "right": 3}], "b": None}
        self.assertEqual(cw.strings_in(value), ["/x/1", "/x/2"])

    def test_what_looks_like_a_file(self):
        self.assertTrue(cw.looks_like_file("/data/a.bam"))
        self.assertTrue(cw.looks_like_file("gs://bucket/a.bam"))
        self.assertTrue(cw.looks_like_file("sub/a.bam"))
        self.assertFalse(cw.looks_like_file("s1"))


class Cli(unittest.TestCase):

    def test_metadata_file_writes_a_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "graph.json"
            with contextlib.redirect_stdout(io.StringIO()):
                code = cw.main(["--metadata", str(FIXTURES / "diamond.json"),
                                "--json-out", str(out)])
            self.assertEqual(code, 0)
            self.assertEqual(contract_violations(json.loads(out.read_text())), [])

    def test_server_without_workflow_fails_cleanly(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cw.main(["--server", "http://localhost:8000"]), 2)


if __name__ == "__main__":
    unittest.main()
