"""
Two ways `clew impact` used to report an obligation as discharged:

  - a cleaned work directory settled to ALREADY_GONE before the published
    tree was looked at, and directory outputs were never found there;
  - a subject that matched no task tag came back "0 affected", exit 0.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from clew.domains import nfcore

ROOT = Path(__file__).resolve().parent.parent


def run_impact(*args):
    cmd = [sys.executable, "-m", "clew.questions.impact", *args]
    return subprocess.run(cmd, capture_output=True, text=True)


def write_graph(tmp, output_details=True):
    """
    One donor, one task whose only output is a directory, plus an untagged
    sibling task. The sibling's directory is what a test creates under the
    work root to show the root is right and the donor's directory is gone,
    rather than every directory missing because the root is wrong.
    """
    graph = {
        "tasks": {
            "aa/000001": {"hash": "aa/000001", "name": "QUANT (donor_001)",
                          "process": "QUANT", "container": "img",
                          "script": "run", "status": "COMPLETED",
                          "workdir": "/elsewhere/work/aa/000001abcdef",
                          "target": ""},
            "bb/000002": {"hash": "bb/000002", "name": "INDEX",
                          "process": "INDEX", "container": "img",
                          "script": "run", "status": "COMPLETED",
                          "workdir": "/elsewhere/work/bb/000002abcdef",
                          "target": ""},
        },
        "edges": [{"consumer": "aa/000001", "producer": "EXTERNAL",
                   "filename": "donor_001.fq", "target": "/in/donor_001.fq"}],
        "outputs": {"aa/000001": ["donor_001"]},
    }
    if output_details:
        graph["output_details"] = {"aa/000001": [{"file": "donor_001", "size": 224}]}
    path = Path(tmp, "graph.json")
    path.write_text(json.dumps(graph))
    sheet = Path(tmp, "sheet.csv")
    sheet.write_text("patient,sample\ndonor_001,donor_001\n")
    return path, sheet


class TestCleanedScratch(unittest.TestCase):
    def plan(self, tmp, *extra):
        graph, sheet = write_graph(tmp)
        out = run_impact("--graph", str(graph), "--samplesheet", str(sheet),
                         "--subject", "donor_001", "--json", "-", *extra).stdout
        return json.loads(out[out.index("{"):])["plan"][0]

    def test_gone_workdir_is_undetermined_until_results_are_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp, "work")
            Path(work, "bb", "000002abcdef").mkdir(parents=True)
            item = self.plan(tmp, "--work-root", str(work))
        self.assertIsNone(item["action"])
        self.assertIsNone(item["storage"])
        self.assertIn("ALREADY_GONE", item["possible"])
        self.assertIn("published tree not checked", item["reason"])

    def test_gone_workdir_and_empty_results_is_already_gone(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp, "work")
            Path(work, "bb", "000002abcdef").mkdir(parents=True)
            results = Path(tmp, "results"); results.mkdir()
            item = self.plan(tmp, "--work-root", str(work),
                             "--results", str(results))
        self.assertEqual(item["action"], "ALREADY_GONE")

    def test_a_root_nothing_resolves_under_is_not_a_cleaned_run(self):
        # Same disk as above minus the sibling's directory: now no recorded
        # directory exists under the root, which is what a wrong --work-root
        # looks like, and it must not settle to ALREADY_GONE.
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp, "work"); work.mkdir()
            results = Path(tmp, "results"); results.mkdir()
            graph, sheet = write_graph(tmp)
            result = run_impact("--graph", str(graph), "--samplesheet", str(sheet),
                                "--subject", "donor_001", "--json", "-",
                                "--work-root", str(work), "--results", str(results))
            item = json.loads(result.stdout[result.stdout.index("{"):])["plan"][0]
        self.assertIsNone(item["action"])
        self.assertIsNone(item["storage"])
        self.assertIn("root looks wrong", result.stderr)

    def test_published_directory_keeps_the_obligation(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp, "work"); work.mkdir()
            results = Path(tmp, "results", "quant", "donor_001")
            results.mkdir(parents=True)
            (results / "quant.sf").write_text("x")
            item = self.plan(tmp, "--work-root", str(work),
                             "--results", str(Path(tmp, "results")))
        self.assertEqual(item["action"], "DESTROY")
        copy = item["published_copies"][0]
        self.assertEqual(copy["published"], ["quant/donor_001"])
        self.assertTrue(copy["ambiguous"])
        self.assertEqual(copy["match"], "directory name")

    def test_index_lists_directories_by_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a", "sample").mkdir(parents=True)
            Path(tmp, "a", "sample", "f.txt").write_text("abc")
            index = nfcore.index_results(tmp)
        self.assertEqual(index[("f.txt", 3)], ["a/sample/f.txt"])
        self.assertEqual(index[("sample", nfcore.DIRECTORY)], ["a/sample"])


class TestSubjectTrigger(unittest.TestCase):
    """
    `--trigger subject:X` is the documented spelling of `--subject X`. It
    used to bypass the domain adapter, so on an nf-core graph it found
    nothing while the flag worked.
    """

    def test_with_a_samplesheet_it_is_the_withdrawal_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph, sheet = write_graph(tmp)
            result = run_impact("--graph", str(graph), "--samplesheet", str(sheet),
                                "--trigger", "subject:donor_001", "--json", "-")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout[result.stdout.index("{"):])
        self.assertEqual(payload["trigger"], "withdrawal of donor_001")
        self.assertEqual(payload["entry_tasks"], ["aa/000001"])

    def test_without_a_samplesheet_the_message_points_at_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph, _ = write_graph(tmp)
            result = run_impact("--graph", str(graph), "--trigger", "subject:donor_001")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("carries a 'subject' label", result.stderr)
        self.assertIn("--samplesheet", result.stderr)


class TestGraphLimitsAreShown(unittest.TestCase):
    """Coverage notes and unexpanded subworkflows reach the terminal and the plan."""

    def test_coverage_and_unexpanded_calls_are_printed_and_carried(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph_path, sheet = write_graph(tmp)
            graph = json.loads(graph_path.read_text())
            graph["coverage"] = ["the record has a stated gap"]
            graph["tasks"]["aa/000001"]["unexpanded_subworkflow"] = "wf-1"
            graph_path.write_text(json.dumps(graph))
            result = run_impact("--graph", str(graph_path), "--container", "img",
                                "--json", "-")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WHAT THIS GRAPH DOES NOT COVER", result.stdout)
        self.assertIn("the record has a stated gap", result.stdout)
        self.assertIn("1 subworkflow call(s) were not expanded", result.stdout)
        payload = json.loads(result.stdout[result.stdout.index("{"):])
        self.assertIn("the record has a stated gap", payload["caveats"])
        self.assertTrue(any("not expanded" in c for c in payload["caveats"]))


class TestUnattributableSubject(unittest.TestCase):
    def test_subject_with_no_tagged_task_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph, sheet = write_graph(tmp)
            sheet.write_text("patient,sample\ndonor_001,donor_001\n"
                             "donor_999,donor_999\n")
            result = run_impact("--graph", str(graph), "--samplesheet",
                                str(sheet), "--subject", "donor_999")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Not attributable", result.stderr)
        self.assertNotIn("ids in the samplesheet", result.stderr)

    def test_total_mismatch_names_the_likely_cause(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph, sheet = write_graph(tmp)
            sheet.write_text("patient,sample\nLIMS-0001,LIMS-0001\n")
            result = run_impact("--graph", str(graph), "--samplesheet",
                                str(sheet), "--subject", "LIMS-0001")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ids in the samplesheet and the tags in the run disagree",
                      result.stderr)


if __name__ == "__main__":
    unittest.main()
