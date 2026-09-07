"""
Where a task's directory is under --work-root, and when Clew refuses to say.

The old rule joined the last two components of the recorded path onto the
root. That is the Nextflow layout and nothing else: every Snakemake job
resolved to the workflow directory, so one REDUNDANT output made the whole
workflow deletable, and Cromwell shards resolved nowhere and read as GONE.
"""

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.extract import digest
from clew.graph import contribution
from clew.graph.graph import contract_violations, local_workdir, resolve_workdirs
from clew.questions import reclaim


def task(hash_, **fields):
    base = {"hash": hash_, "name": hash_, "process": hash_, "container": "img",
            "status": "COMPLETED", "script": "run", "workdir": ""}
    base.update(fields)
    return base


def graph_of(*tasks):
    return {"tasks": {t["hash"]: t for t in tasks}, "edges": [],
            "outputs": {t["hash"]: ["out.txt"] for t in tasks}}


class Placement(unittest.TestCase):
    def test_workpath_is_joined_onto_the_root(self):
        t = task("wf.step/shard-1", workdir="/elsewhere/call-step/shard-1",
                 workpath="call-step/shard-1/execution")
        self.assertEqual(local_workdir(t, "/root"),
                         Path("/root/call-step/shard-1/execution"))

    def test_an_old_hashed_layout_graph_still_resolves(self):
        # Graphs extracted before workpath existed: the recorded path ends
        # in the task's own prefix/hash, which is the layout the old rule
        # was written for, so it still applies to exactly those.
        t = task("ab/123456", workdir="/on/another/host/work/ab/123456deadbeef")
        self.assertEqual(local_workdir(t, "/root"), Path("/root/ab/123456deadbeef"))

    def test_any_other_shape_is_not_placed(self):
        shared = task("trim/trimmed/s1.fq", workdir="/data/smk")
        call = task("wf.step", workdir="/data/cromwell-executions/wf/1234/call-step")
        none = task("x/1")
        for t in (shared, call, none):
            self.assertIsNone(local_workdir(t, "/root"))

    def test_no_root_means_no_placement(self):
        t = task("ab/123456", workdir="/work/ab/123456", workpath="ab/123456")
        self.assertIsNone(local_workdir(t, None))

    def test_workpath_must_stay_under_the_root(self):
        graph = graph_of(task("a/1", workpath="../outside"), task("a/2", workpath="/abs"))
        problems = contract_violations(graph)
        self.assertEqual(len(problems), 2)
        self.assertTrue(all("workpath" in p for p in problems))
        self.assertEqual(contract_violations(graph_of(task("a/3", workpath="a/3"))), [])


class Refusals(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_tasks_sharing_a_directory_are_all_unchecked(self):
        (self.root / "wf").mkdir()
        graph = graph_of(task("a/1", workpath="wf"), task("a/2", workpath="wf"),
                         task("a/3", workpath="own"))
        (self.root / "own").mkdir()
        resolved, warnings = resolve_workdirs(graph, self.root)
        self.assertIsNone(resolved["a/1"])
        self.assertIsNone(resolved["a/2"])
        self.assertEqual(resolved["a/3"], self.root / "own")
        self.assertEqual(len(warnings), 1)
        self.assertIn("shared directory", warnings[0])

    def test_a_root_nothing_exists_under_unplaces_everything(self):
        graph = graph_of(task("a/1", workpath="one"), task("a/2", workpath="two"))
        resolved, warnings = resolve_workdirs(graph, self.root)
        self.assertEqual(resolved, {"a/1": None, "a/2": None})
        self.assertIn("root looks wrong", warnings[0])

    def test_one_present_directory_proves_the_root(self):
        (self.root / "one").mkdir()
        graph = graph_of(task("a/1", workpath="one"), task("a/2", workpath="two"))
        resolved, warnings = resolve_workdirs(graph, self.root)
        self.assertEqual(warnings, [])
        self.assertEqual(contribution.storage_at(resolved["a/1"]), contribution.WRITABLE)
        self.assertEqual(contribution.storage_at(resolved["a/2"]), contribution.DESTROYED)

    def test_classify_never_reports_destroyed_for_an_unplaced_task(self):
        graph = graph_of(task("trim/trimmed/s1.fq", workdir=str(self.root)))
        facts = contribution.classify(graph, "trim/trimmed/s1.fq", exclusive=False,
                                      work_root=str(self.root))
        self.assertIsNone(facts["storage"])
        self.assertIn("not placed under --work-root", facts["reason"])

    def test_reclaim_keeps_every_task_of_a_shared_directory(self):
        # The Snakemake case: one directory for the whole workflow. A verdict
        # on it would be a verdict on every job, so none is given.
        (self.root / "wf" / "out").mkdir(parents=True)
        (self.root / "wf" / "out" / "out.txt").write_bytes(b"x")
        graph = graph_of(task("a/1", workpath="wf"), task("a/2", workpath="wf"))
        reclaimer = reclaim.Reclaimer(graph, self.root)
        for item in reclaimer.plan():
            self.assertEqual(item["verdict"], reclaim.KEEP)
            self.assertIn("not placed", item["reason"])
            self.assertEqual(item["bytes"], 0)
        self.assertEqual(len(reclaimer.warnings), 1)

    def test_reclaim_does_not_call_a_wrong_root_gone(self):
        graph = graph_of(task("a/1", workpath="one"), task("a/2", workpath="two"))
        items = reclaim.Reclaimer(graph, self.root).plan()
        self.assertEqual({i["verdict"] for i in items}, {reclaim.KEEP})

    def test_digest_finds_outputs_through_workpath(self):
        execution = self.root / "call-step" / "shard-0" / "execution"
        execution.mkdir(parents=True)
        (execution / "out.txt").write_bytes(b"abc")
        graph = graph_of(task("wf.step/shard-0", workdir="/elsewhere/call-step/shard-0",
                              workpath="call-step/shard-0/execution"))
        with contextlib.redirect_stderr(io.StringIO()):
            counts = digest.digest_outputs(graph, self.root)
        self.assertEqual(counts["hashed"], 1)
        self.assertEqual(graph["output_details"]["wf.step/shard-0"][0]["digest"],
                         digest.sha256_file(execution / "out.txt"))


if __name__ == "__main__":
    unittest.main()
