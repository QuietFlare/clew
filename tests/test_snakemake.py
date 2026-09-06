"""
The Snakemake adapter, on stores written by Snakemake 9.26 for two real
runs.

fixtures/snakemake_wildcards/: the file backend. A wildcard rule with two
outputs per sample, a directory output, and an aggregating report.

    data/{s}.fq + genome.fa -> align -> {s}.bam, {s}.bam.bai
    {s}.bam -> stats -> {s}_stats/
    {s}_stats, {s}.bam -> report

fixtures/snakemake_diamond/: the SQLite backend, on the four-rule diamond
the other adapters are tested against.
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.extract import snakemake as sm
from clew.graph import blast_radius as core
from clew.graph.graph import contract_violations, external_input_entry_nodes

FIXTURES = Path(__file__).resolve().parent / "fixtures"
WILDCARDS = FIXTURES / "snakemake_wildcards"
DIAMOND = FIXTURES / "snakemake_diamond"

ALIGN1, ALIGN2 = "align/results/s1.bam", "align/results/s2.bam"
STATS1, STATS2 = "stats/results/s1_stats", "stats/results/s2_stats"
REPORT = "report/results/report.txt"


def edges_into(graph, consumer):
    return {e["filename"]: e["producer"]
            for e in graph["edges"] if e["consumer"] == consumer}


class FileBackend(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.graph = sm.extract(sm.load_records(WILDCARDS), "/data/smk")

    def test_conforms_to_the_contract(self):
        self.assertEqual(contract_violations(self.graph), [])

    def test_a_job_with_two_outputs_is_one_node(self):
        """
        Snakemake writes one record per output file. Grouping them back
        is what keeps align from appearing twice per sample.
        """
        self.assertEqual(set(self.graph["tasks"]),
                         {ALIGN1, ALIGN2, STATS1, STATS2, REPORT})
        self.assertEqual(self.graph["outputs"][ALIGN1], ["s1.bam", "s1.bam.bai"])

    def test_workflow_inputs_are_external(self):
        self.assertEqual(edges_into(self.graph, ALIGN1),
                         {"s1.fq": "EXTERNAL", "genome.fa": "EXTERNAL"})

    def test_a_directory_output_joins_by_path(self):
        into = edges_into(self.graph, REPORT)
        self.assertEqual(into["s1_stats"], STATS1)
        self.assertEqual(into["s2.bam"], ALIGN2)

    def test_input_checksums_reach_the_edges(self):
        bam = next(e for e in self.graph["edges"]
                   if e["consumer"] == STATS1 and e["filename"] == "s1.bam")
        self.assertEqual(len(bam["sha256"]), 64)
        stats_dir = next(e for e in self.graph["edges"]
                         if e["consumer"] == REPORT and e["filename"] == "s1_stats")
        self.assertNotIn("sha256", stats_dir)

    def test_re_execution_evidence(self):
        task = self.graph["tasks"][ALIGN1]
        self.assertEqual(task["process"], "align")
        self.assertEqual(task["name"], "align (results/s1.bam)")
        self.assertEqual(task["status"], "COMPLETED")
        self.assertTrue(task["script"].startswith("cat data/s1.fq genome.fa"))
        self.assertEqual(task["workdir"], "/data/smk")
        self.assertEqual(task["container"], "")
        self.assertGreaterEqual(task["duration_s"], 0)

    def test_edge_target_is_the_workdir_relative_path(self):
        edge = next(e for e in self.graph["edges"]
                    if e["consumer"] == STATS1 and e["filename"] == "s1.bam")
        self.assertEqual(edge["target"], "results/s1.bam")

    def test_the_file_name_is_the_output_path(self):
        self.assertEqual(sm.decode_key(("cmVzdWx0cy9zMS5iYW0=",)), "results/s1.bam")
        long = ["@cmVzdWx0", "cy9zMS5iYW0="]
        self.assertEqual(sm.decode_key(long), "results/s1.bam")


class DbBackend(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.graph = sm.extract(sm.load_records(DIAMOND), "/data/smk")

    def test_conforms_to_the_contract(self):
        self.assertEqual(contract_violations(self.graph), [])

    def test_the_diamond_is_reconstructed(self):
        internal = sorted(
            (e["producer"].split("/")[0], e["consumer"].split("/")[0])
            for e in self.graph["edges"] if e["producer"] != "EXTERNAL")
        self.assertEqual(internal, [
            ("analyse", "report"), ("prep", "analyse"),
            ("prep", "qc"), ("qc", "report")])

    def test_every_edge_carries_a_digest(self):
        self.assertTrue(all(e.get("sha256") for e in self.graph["edges"]))

    def test_a_second_namespace_must_be_chosen(self):
        with tempfile.TemporaryDirectory() as tmp:
            import shutil
            import sqlite3
            db = Path(tmp) / "metadata.db"
            shutil.copy(DIAMOND / "metadata.db", db)
            connection = sqlite3.connect(db)
            connection.execute(
                "insert into snakemake_metadata select "
                + ", ".join(
                    "'/other/.snakemake'" if c == "namespace" else c
                    for c in [d[1] for d in connection.execute(
                        "pragma table_info(snakemake_metadata)")])
                + " from snakemake_metadata")
            connection.commit()
            connection.close()
            with self.assertRaises(SystemExit):
                sm.load_records(tmp)
            chosen = sm.load_records(tmp, namespace="/other/.snakemake")
            self.assertEqual(len(chosen), 4)


class Grouping(unittest.TestCase):

    def test_records_without_a_job_hash_group_on_rule_inputs_and_command(self):
        records = {
            "a.bam": {"rule": "align", "input": ["a.fq"], "shellcmd": "x"},
            "a.bai": {"rule": "align", "input": ["a.fq"], "shellcmd": "x"},
            "b.bam": {"rule": "align", "input": ["b.fq"], "shellcmd": "y"},
        }
        nodes = {node: paths for node, _, paths, _ in sm.group_jobs(records)}
        self.assertEqual(nodes, {"align/a.bai": ["a.bai", "a.bam"],
                                 "align/b.bam": ["b.bam"]})

    def test_a_newer_record_format_is_refused(self):
        with self.assertRaises(SystemExit):
            sm.extract({"x": {"rule": "r", "record_format_version": 99}})

    def test_container_then_conda_then_nothing(self):
        self.assertEqual(sm.environment_of({"container_img_url": "docker://img:1"}),
                         "docker://img:1")
        self.assertEqual(sm.environment_of({"conda_env": "bmFtZTogeA==",
                                            "software_stack_hash": "abcdef0123456789"}),
                         "conda@abcdef012345")
        self.assertEqual(sm.environment_of({}), "")


class Triggers(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.graph = sm.extract(sm.load_records(WILDCARDS), "/data/smk")

    def affected_by(self, filename):
        entry = external_input_entry_nodes(self.graph, filename)
        radius = core.blast_radius(self.graph, entry)
        return set(radius[f"input:{filename}"]["affected"])

    def test_one_sample_spares_the_other(self):
        self.assertEqual(self.affected_by("s1.fq"), {ALIGN1, STATS1, REPORT})

    def test_the_reference_reaches_everything(self):
        self.assertEqual(self.affected_by("genome.fa"),
                         {ALIGN1, ALIGN2, STATS1, STATS2, REPORT})


class Cli(unittest.TestCase):

    def test_workdir_writes_a_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "graph.json"
            with contextlib.redirect_stdout(io.StringIO()):
                code = sm.main(["--workdir", str(WILDCARDS), "--json-out", str(out)])
            self.assertEqual(code, 0)
            self.assertEqual(contract_violations(json.loads(out.read_text())), [])

    def test_a_directory_without_a_store_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                sm.main(["--workdir", tmp])


if __name__ == "__main__":
    unittest.main()
