"""
The horus-lineage adapter, on a real run directory recorded by the plugin
(fixtures/horus_run/): a four-task diamond with two branches and a join.

    raw.csv ---> prep ---+--> analyse --+--> report
                         |              |
                         +--> qc -------+
    calibration.txt ------------> analyse
    reference.txt --------------------------> report

Fourth ingest path, same graph schema, different engine. Horus places
tasks on different machines, so this adapter joins on content digests
rather than on paths or work directories.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew import extract_from_horus as hz
from clew.core import blast_radius as core

FIXTURES = Path(__file__).resolve().parent / "fixtures"
RUN_DIR = FIXTURES / "horus_run"


def short(node):
    """A node id is '<run>/<task id>'; the task id is the readable half."""
    return node.split("/", 1)[-1]


@unittest.skipUnless(RUN_DIR.exists(), "horus fixture missing")
class TestHorusAdapter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = hz.extract(RUN_DIR)

    def test_every_task_becomes_a_node(self):
        """
        Four tasks in, four nodes out.
        """
        self.assertEqual(
            sorted(short(n) for n in self.graph["tasks"]),
            ["analyse", "prep", "qc", "report"])

    def test_the_diamond_is_reconstructed_from_digests(self):
        """
        No paths, no work directories: an input joins to the task whose
        output has the same sha256.
        """
        internal = sorted(
            (short(e["producer"]), short(e["consumer"]))
            for e in self.graph["edges"] if e["producer"] != "EXTERNAL")
        self.assertEqual(internal, [
            ("analyse", "report"),
            ("prep", "analyse"),
            ("prep", "qc"),
            ("qc", "report"),
        ])

    def test_inputs_the_run_did_not_produce_are_external(self):
        """
        A digest matching no output came from outside the run. Dropping
        these would make a reference update read as clean.
        """
        external = sorted(
            e["filename"] for e in self.graph["edges"]
            if e["producer"] == "EXTERNAL")
        self.assertEqual(
            external, ["calibration.txt", "raw.csv", "reference.txt"])

    def test_no_edge_dangles(self):
        """
        Every producer is either a node in this graph or EXTERNAL.
        """
        known = set(self.graph["tasks"])
        for edge in self.graph["edges"]:
            self.assertTrue(
                edge["producer"] in known or edge["producer"] == "EXTERNAL",
                f"dangling producer {edge['producer']}")

    def test_nodes_carry_re_execution_evidence(self):
        """
        Storage and reproducibility checks need to know what ran and in
        what, so a node without them fails closed.
        """
        prep = next(t for t in self.graph["tasks"].values()
                    if t["task_id"] == "prep")
        self.assertTrue(prep["script"].endswith("prep.py"))
        self.assertTrue(prep["container"].startswith("shell@"))
        self.assertTrue(prep["workdir"])

    def test_an_unknown_record_format_is_refused(self):
        """
        Refusing beats guessing at the fields of a version we do not know.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for path in RUN_DIR.glob("*.json"):
                tmp.joinpath(path.name).write_text(path.read_text())
            plan = json.loads((tmp / "run.json").read_text())
            plan["format"] = "horus-lineage/v99"
            (tmp / "run.json").write_text(json.dumps(plan))

            with self.assertRaises(SystemExit):
                hz.extract(tmp)

    def test_the_merged_layout_reads_the_same(self):
        """
        HORUS_LINEAGE_MERGE folds the per-task files into records.jsonl.
        Both layouts describe the same run.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            records = []
            for path in sorted(RUN_DIR.glob("*.json")):
                if path.name in ("run.json", "definition.json"):
                    tmp.joinpath(path.name).write_text(path.read_text())
                else:
                    records.append(json.loads(path.read_text()))
            (tmp / "records.jsonl").write_text(
                "\n".join(json.dumps(r) for r in records) + "\n")

            merged = hz.extract(tmp)

        self.assertEqual(sorted(merged["tasks"]), sorted(self.graph["tasks"]))
        self.assertEqual(len(merged["edges"]), len(self.graph["edges"]))


@unittest.skipUnless(RUN_DIR.exists(), "horus fixture missing")
class TestHorusThroughCore(unittest.TestCase):
    """
    The graph is the point: core must answer impact questions on it
    without knowing which engine produced it.
    """

    @classmethod
    def setUpClass(cls):
        cls.graph = hz.extract(RUN_DIR)
        cls.subjects = {}
        for edge in cls.graph["edges"]:
            if edge["producer"] == "EXTERNAL":
                cls.subjects.setdefault(
                    edge["filename"], []).append(edge["consumer"])
        cls.radius = core.blast_radius(cls.graph, cls.subjects)

    def test_a_root_input_reaches_everything(self):
        """
        raw.csv feeds prep, and every other task descends from it.
        """
        affected = self.radius["raw.csv"]["affected"]
        self.assertEqual(
            sorted(short(n) for n in affected),
            ["analyse", "prep", "qc", "report"])

    def test_a_mid_branch_input_spares_the_other_branch(self):
        """
        calibration.txt feeds analyse only, so qc is untouched. This is
        the answer the engine itself gives: changing calibration.txt
        re-runs analyse and report, and skips prep and qc.
        """
        affected = self.radius["calibration.txt"]["affected"]
        self.assertEqual(
            sorted(short(n) for n in affected), ["analyse", "report"])

    def test_a_leaf_input_reaches_only_the_last_task(self):
        """
        reference.txt is read by report and nothing downstream of it.
        """
        affected = self.radius["reference.txt"]["affected"]
        self.assertEqual(sorted(short(n) for n in affected), ["report"])

    def test_exclusive_and_shared_split_the_radius(self):
        """
        The split is what makes a remediation plan: a node reachable from
        one subject only can go, one reachable from several must be
        rebuilt without the bad subject.
        """
        raw = self.radius["raw.csv"]
        self.assertEqual(
            sorted(short(n) for n in raw["exclusive"]), ["prep", "qc"])
        self.assertEqual(
            sorted(short(n) for n in raw["shared"]), ["analyse", "report"])


if __name__ == "__main__":
    unittest.main()
