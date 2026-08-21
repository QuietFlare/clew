"""
The extractor traps that cost real time. Each test here is a regression
guard for a bug that produced FALSE NEGATIVES — the graph reporting donor
data as absent when it was present.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import extract_lineage as ex


class TestTargetToHash(unittest.TestCase):
    """Trap 1: stage-<uuid>/ and tmp/ paths parse as plausible task hashes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_stage_dir_is_external_not_a_task(self):
        # 3e/d2a534 looks exactly like a task hash. It is not one.
        target = self.work / "stage-b3809b93-aaaa" / "3e" / "d2a534xxxx" / "genome.fasta"
        self.assertEqual(ex.target_to_hash(target, self.work), "EXTERNAL")

    def test_tmp_dir_is_external_not_a_task(self):
        target = self.work / "tmp" / "53" / "aabbccdd" / "workflow_summary_mqc.yaml"
        self.assertEqual(ex.target_to_hash(target, self.work), "EXTERNAL")

    def test_path_outside_work_is_external(self):
        outside = Path(self.tmp.name).parent / "somewhere-else" / "input.fastq.gz"
        self.assertEqual(ex.target_to_hash(outside, self.work), "EXTERNAL")

    def test_real_task_path_resolves_to_abbreviated_hash(self):
        target = self.work / "fc" / "861a98deadbeef0123" / "reads.bam"
        self.assertEqual(ex.target_to_hash(target, self.work), "fc/861a98")


class TestNumberedSubdirectories(unittest.TestCase):
    """
    Trap 3: aggregating tasks stage inputs in numbered subdirectories
    (./1/, ./18/). Walking only the top level made MULTIQC appear to have
    1 input instead of 20 — hiding exactly the many-into-one nodes this
    project exists to track.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.work = root / "work"

        # Producer task: ab/123456... makes two files.
        self.producer = self.work / "ab" / "123456aaaa0000"
        self.producer.mkdir(parents=True)
        (self.producer / "report_one.txt").write_text("x")
        (self.producer / "report_two.txt").write_text("y")

        # Aggregator task: cd/abcdef... consumes one file at top level and
        # one inside a numbered subdirectory, the MULTIQC pattern.
        self.aggregator = self.work / "cd" / "abcdef0000aaaa"
        (self.aggregator / "1").mkdir(parents=True)
        os.symlink(self.producer / "report_one.txt",
                   self.aggregator / "report_one.txt")
        os.symlink(self.producer / "report_two.txt",
                   self.aggregator / "1" / "report_two.txt")
        (self.aggregator / "aggregate.html").write_text("out")

        # Weblog for exactly this run.
        self.jsonl = root / "run.jsonl"
        lines = [
            {"trace": {"hash": "ab/123456", "task_id": 1, "name": "PRODUCE",
                       "process": "PRODUCE", "container": "img", "status": "COMPLETED",
                       "workdir": str(self.producer), "script": "make"}},
            {"trace": {"hash": "cd/abcdef", "task_id": 2, "name": "AGGREGATE",
                       "process": "AGGREGATE", "container": "img", "status": "COMPLETED",
                       "workdir": str(self.aggregator), "script": "agg"}},
        ]
        self.jsonl.write_text("\n".join(json.dumps(l) for l in lines))

    def tearDown(self):
        self.tmp.cleanup()

    def test_inputs_inside_numbered_subdirs_are_found(self):
        tasks, edges, outputs, missing = ex.extract(self.jsonl, self.work)
        self.assertEqual(missing, [])

        agg_edges = [e for e in edges if e["consumer"] == "cd/abcdef"]
        # Both inputs, not just the top-level one. This is the regression.
        self.assertEqual(len(agg_edges), 2)
        self.assertTrue(all(e["producer"] == "ab/123456" for e in agg_edges))
        filenames = {e["filename"] for e in agg_edges}
        self.assertIn("1/report_two.txt", filenames)

    def test_outputs_exclude_symlinked_inputs(self):
        tasks, edges, outputs, missing = ex.extract(self.jsonl, self.work)
        self.assertEqual(outputs["cd/abcdef"], ["aggregate.html"])

    def test_only_this_runs_tasks_are_loaded(self):
        # Trap 2: work/ accumulates every run ever executed. A task folder on
        # disk that is not in the JSONL must not appear in the graph.
        stray = self.work / "ee" / "ffffff00001111"
        stray.mkdir(parents=True)
        (stray / "old_run.txt").write_text("z")

        tasks, edges, outputs, missing = ex.extract(self.jsonl, self.work)
        self.assertEqual(set(tasks), {"ab/123456", "cd/abcdef"})


if __name__ == "__main__":
    unittest.main()
