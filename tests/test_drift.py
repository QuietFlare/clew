"""
Drift pairs tasks by name across two runs, compares output digests, and
names the first task on each chain that differs together with the cause
read from the record. Every verdict is pinned on a small synthetic chain.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.questions import drift


def run(prefix, digests, container="img:1", ref="sha256:ref1", extra_task=False):
    """ref -> A -> B -> C, with per-task output digests given in `digests`."""
    def h(n):
        return f"{prefix}{n}"
    tasks = {
        h(1): {"hash": h(1), "name": "ALIGN (s1)", "process": "ALIGN", "container": container, "script": "align"},
        h(2): {"hash": h(2), "name": "STATS (s1)", "process": "STATS", "container": "img:1", "script": "stats"},
        h(3): {"hash": h(3), "name": "REPORT (s1)", "process": "REPORT", "container": "img:1", "script": "report"},
    }
    edges = [
        {"consumer": h(1), "producer": "EXTERNAL", "filename": "ref.fa", "target": "/ref.fa", "digest": ref},
        {"consumer": h(2), "producer": h(1), "filename": "s.bam", "target": ""},
        {"consumer": h(3), "producer": h(2), "filename": "s.stats", "target": ""},
    ]
    outputs = {h(1): ["s.bam", "versions.yml"], h(2): ["s.stats", "versions.yml"], h(3): ["report.html"]}
    details = {
        h(1): [{"file": "s.bam", "digest": digests[0]}, {"file": "versions.yml", "digest": f"sha256:v{prefix}"}],
        h(2): [{"file": "s.stats", "digest": digests[1]}, {"file": "versions.yml", "digest": f"sha256:v{prefix}"}],
        h(3): [{"file": "report.html", "digest": digests[2]}],
    }
    if extra_task:
        tasks[h(4)] = {"hash": h(4), "name": "QC (s1)", "process": "QC", "container": "img:1", "script": "qc"}
        outputs[h(4)] = ["qc.txt"]
        details[h(4)] = [{"file": "qc.txt", "digest": "sha256:qc"}]
    return {"tasks": tasks, "edges": edges, "outputs": outputs, "output_details": details}


SAME = ["sha256:bam", "sha256:stats", "sha256:report"]


def verdicts(before, after, **kw):
    return {i["name"]: i for i in drift.drift(before, after, **kw)}


class Drift(unittest.TestCase):
    def test_identical_runs_reproduce(self):
        v = verdicts(run("b", SAME), run("a", SAME))
        self.assertEqual({i["verdict"] for i in v.values()}, {drift.REPRODUCED})

    def test_bookkeeping_differences_are_ignored(self):
        v = verdicts(run("b", SAME), run("a", SAME))
        self.assertEqual(v["ALIGN (s1)"]["verdict"], drift.REPRODUCED)
        v = verdicts(run("b", SAME), run("a", SAME), ignore=())
        self.assertEqual(v["ALIGN (s1)"]["verdict"], drift.DRIFTED)

    def test_a_changed_input_is_the_root_and_the_rest_is_downstream(self):
        after = run("a", ["sha256:bam2", "sha256:stats2", "sha256:report2"], ref="sha256:ref2")
        v = verdicts(run("b", SAME), after)
        self.assertEqual(v["ALIGN (s1)"]["verdict"], drift.DRIFTED)
        self.assertIn("input changed: ref.fa", v["ALIGN (s1)"]["reason"])
        self.assertEqual(v["STATS (s1)"]["verdict"], drift.DOWNSTREAM)
        self.assertIn("a1", v["STATS (s1)"]["reason"])
        self.assertEqual(v["REPORT (s1)"]["verdict"], drift.DOWNSTREAM)

    def test_a_changed_container_is_named(self):
        after = run("a", ["sha256:bam2", "sha256:stats", "sha256:report"], container="img:2")
        v = verdicts(run("b", SAME), after)
        self.assertIn("container changed: img:1 -> img:2", v["ALIGN (s1)"]["reason"])

    def test_same_recipe_different_output_is_its_own_finding(self):
        after = run("a", ["sha256:bam", "sha256:stats2", "sha256:report2"])
        v = verdicts(run("b", SAME), after)
        self.assertEqual(v["ALIGN (s1)"]["verdict"], drift.REPRODUCED)
        self.assertEqual(v["STATS (s1)"]["verdict"], drift.DRIFTED)
        self.assertIn("same inputs and recipe, different outputs", v["STATS (s1)"]["reason"])

    def test_reproduced_despite_upstream_drift_says_so(self):
        after = run("a", ["sha256:bam2", "sha256:stats", "sha256:report"], container="img:2")
        v = verdicts(run("b", SAME), after)
        self.assertEqual(v["STATS (s1)"]["verdict"], drift.REPRODUCED)
        self.assertIn("although a1 drifted", v["STATS (s1)"]["reason"])

    def test_missing_digests_are_unverified_not_reproduced(self):
        after = run("a", SAME)
        after["output_details"]["a2"][0].pop("digest")
        v = verdicts(run("b", SAME), after)
        self.assertEqual(v["STATS (s1)"]["verdict"], drift.UNVERIFIED)
        self.assertEqual(v["REPORT (s1)"]["verdict"], drift.REPRODUCED)

    def test_added_and_removed_tasks(self):
        v = verdicts(run("b", SAME, extra_task=True), run("a", SAME))
        self.assertEqual(v["QC (s1)"]["verdict"], drift.REMOVED)
        v = verdicts(run("b", SAME), run("a", SAME, extra_task=True))
        self.assertEqual(v["QC (s1)"]["verdict"], drift.ADDED)

    def test_an_unverified_upstream_gives_unsettled_not_downstream(self):
        # A root can hide behind a missing digest. DOWNSTREAM said the
        # cause was known; it was not.
        after = run("a", ["sha256:bam", "sha256:stats", "sha256:report2"])
        after["output_details"]["a2"][0].pop("digest")
        v = verdicts(run("b", SAME), after)
        self.assertEqual(v["STATS (s1)"]["verdict"], drift.UNVERIFIED)
        self.assertEqual(v["REPORT (s1)"]["verdict"], drift.UNSETTLED)
        self.assertIn("upstream a2 not verified", v["REPORT (s1)"]["reason"])

    def test_the_page_leads_with_roots(self):
        from clew.views import drift_report
        after = run("a", ["sha256:bam2", "sha256:stats2", "sha256:report2"], ref="sha256:ref2")
        built = drift.plan_to_dict(drift.drift(run("b", SAME), after), "b.json", "a.json", ())
        page = drift_report.render(built)
        self.assertIn("Where the runs part ways", page)
        self.assertIn("input changed: ref.fa", page)
        self.assertEqual(page, drift_report.render(built))


def shards(prefix, digests, inputs=("sha256:in1", "sha256:in2"), superseded=None):
    """Two same-named ALIGN shards, each on its own external input."""
    tasks, edges, outputs, details = {}, [], {}, {}
    for i, (digest, ref) in enumerate(zip(digests, inputs), 1):
        h = f"{prefix}{i}"
        tasks[h] = {"hash": h, "name": "ALIGN (s1)", "process": "ALIGN",
                    "container": "img:1", "script": "align"}
        if superseded == i:
            tasks[h]["superseded"] = True
        edges.append({"consumer": h, "producer": "EXTERNAL", "filename": f"in{i}.fq",
                      "target": "", "digest": ref})
        outputs[h] = ["s.bam"]
        details[h] = [{"file": "s.bam", "digest": digest}]
    return {"tasks": tasks, "edges": edges, "outputs": outputs, "output_details": details}


class Pairing(unittest.TestCase):
    """
    Same-named tasks were paired by sorted hash, so two shards paired
    crosswise and both read DRIFTED. They pair on input digests now.
    """

    def test_shards_pair_on_their_inputs_not_their_hash_order(self):
        # After-run hashes sort the other way round from the before run.
        before = shards("b", ["sha256:x", "sha256:y"])
        after = shards("a", ["sha256:y", "sha256:x"], inputs=("sha256:in2", "sha256:in1"))
        pairs = drift.pair_tasks(before, after)
        self.assertEqual(pairs, [("b1", "a2", None), ("b2", "a1", None)])
        self.assertEqual({i["verdict"] for i in drift.drift(before, after)}, {drift.REPRODUCED})

    def test_a_superseded_version_is_left_out(self):
        before = shards("b", ["sha256:x", "sha256:old"], inputs=("sha256:in1", "sha256:in1"),
                        superseded=2)
        after = shards("a", ["sha256:x"], inputs=("sha256:in1",))
        self.assertEqual(drift.pair_tasks(before, after), [("b1", "a1", None)])

    def test_what_the_inputs_cannot_tell_apart_is_unverified(self):
        before = shards("b", ["sha256:x", "sha256:y"], inputs=("sha256:same", "sha256:same"))
        after = shards("a", ["sha256:x", "sha256:y"], inputs=("sha256:same", "sha256:same"))
        items = {i["task"]: i for i in drift.drift(before, after)}
        self.assertEqual({i["verdict"] for i in items.values()}, {drift.UNVERIFIED})
        self.assertIn("share this name", items["a1"]["reason"])
        self.assertEqual(len(items), 4)

    def test_one_leftover_on_each_side_pairs_by_elimination(self):
        # Shard 2's input changed. Its partner is the only unclaimed task,
        # so the changed input is found rather than hidden as unverified.
        before = shards("b", ["sha256:x", "sha256:y"])
        after = shards("a", ["sha256:x", "sha256:y2"], inputs=("sha256:in1", "sha256:in2b"))
        v = {i["task"]: i for i in drift.drift(before, after)}
        self.assertEqual(v["a1"]["verdict"], drift.REPRODUCED)
        self.assertEqual(v["a2"]["verdict"], drift.DRIFTED)
        self.assertIn("input changed: in2.fq", v["a2"]["reason"])

    def test_an_input_without_a_digest_cannot_key_a_pair(self):
        before = shards("b", ["sha256:x", "sha256:y"])
        after = shards("a", ["sha256:x", "sha256:y"])
        for e in after["edges"]:
            e.pop("digest")
        self.assertIsNone(drift.input_digests(after, "a1"))
        verdict = {i["task"]: i["verdict"] for i in drift.drift(before, after)}
        self.assertEqual(verdict["a1"], drift.UNVERIFIED)


class SameChain(unittest.TestCase):
    """
    The store holds one graph per resume chain. Two runs of one chain
    load the same graph, and drift compared it with itself.
    """

    def test_two_runs_of_one_session_are_refused(self):
        import contextlib
        import io
        import tempfile
        from tests.test_lineage_store import RUN_A, RUN_B, CHAIN, PRODUCER, task_run, write_record
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp)
            (store / ".history").mkdir()
            (store / ".history" / RUN_A).write_text(
                f"2026-08-01 10:00:00 CEST\tfirst_run\t{CHAIN}\tlid://{RUN_A}\n")
            (store / ".history" / RUN_B).write_text(
                f"2026-08-02 10:00:00 CEST\tsecond_run\t{CHAIN}\tlid://{RUN_B}\n")
            write_record(store / PRODUCER, task_run(CHAIN, RUN_A, "PIPE:ALIGN"))
            with self.assertRaises(SystemExit) as refused, \
                    contextlib.redirect_stdout(io.StringIO()):
                drift.main(["--runs", tmp, "--before", "first_run", "--after", "second_run"])
        self.assertIn("same graph", str(refused.exception))
        self.assertIn("resume chain", str(refused.exception))


if __name__ == "__main__":
    unittest.main()
