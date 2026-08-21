"""
The native lineage-store adapter, on a synthetic store and (when the
sibling Petri store is present) on a real sarek run.

The equivalence test at the bottom is the one that matters: the symlink
extractor and the store adapter are two independent witnesses of the same
pipeline, and every impact number must agree between them.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import extract_from_lineage_store as ls

RUN_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
RUN_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
PRODUCER = "11112222333344445555666677778888"
CONSUMER = "99990000aaaabbbbccccddddeeeeffff"
OTHER_RUN_TASK = "fedcba9876543210fedcba9876543210"


def write_record(path, record):
    path.mkdir(parents=True, exist_ok=True)
    (path / ".data.json").write_text(json.dumps(record))


class SyntheticStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name)

        history = self.store / ".history"
        history.mkdir()
        (history / RUN_A).write_text(
            f"2026-08-01 10:00:00 CEST\tfirst_run\tsession-a\tlid://{RUN_A}\n")
        (history / RUN_B).write_text(
            f"2026-08-02 10:00:00 CEST\tsecond_run\tsession-b\tlid://{RUN_B}\n")

        # Run B: producer task with one output file, plus an external input
        # carrying a checksum.
        write_record(self.store / PRODUCER, {
            "version": "lineage/v1beta1", "kind": "TaskRun",
            "spec": {
                "sessionId": "session-b", "workflowRun": f"lid://{RUN_B}",
                "name": "PIPE:ALIGN (subject_1)",
                "container": "quay.io/aligner:1.0", "script": "align",
                "input": [
                    {"type": "val", "name": "meta", "value": {"id": "subject_1"}},
                    {"type": "path", "name": "ref", "value": [
                        {"path": "https://example.org/ref/genome.fasta",
                         "checksum": {"value": "abc123", "algorithm": "nextflow",
                                      "mode": "standard"}}]},
                ],
            },
        })
        write_record(self.store / PRODUCER / "out.bam", {
            "version": "lineage/v1beta1", "kind": "FileOutput",
            "spec": {"path": f"/work/{PRODUCER[:2]}/{PRODUCER[2:]}/out.bam",
                     "source": f"lid://{PRODUCER}", "workflowRun": f"lid://{RUN_B}",
                     "taskRun": f"lid://{PRODUCER}"},
        })

        # Run B: consumer referencing the producer by lid.
        write_record(self.store / CONSUMER, {
            "version": "lineage/v1beta1", "kind": "TaskRun",
            "spec": {
                "sessionId": "session-b", "workflowRun": f"lid://{RUN_B}",
                "name": "PIPE:STATS (subject_1)",
                "container": "quay.io/stats:2.0", "script": "stats",
                "input": [
                    {"type": "path", "name": "bam",
                     "value": [f"lid://{PRODUCER}/out.bam"]},
                ],
            },
        })

        # Run A: a task that must NOT appear when extracting run B.
        write_record(self.store / OTHER_RUN_TASK, {
            "version": "lineage/v1beta1", "kind": "TaskRun",
            "spec": {"sessionId": "session-a", "workflowRun": f"lid://{RUN_A}",
                     "name": "PIPE:OLD", "container": "img", "script": "old",
                     "input": []},
        })

        # A workflow-level "#output" entry, which is not a task.
        write_record(Path(str(self.store / PRODUCER) + "#output"), {
            "version": "lineage/v1beta1", "kind": "WorkflowOutput", "spec": {}})

    def tearDown(self):
        self.tmp.cleanup()

    def test_history_and_run_selection(self):
        runs = ls.load_history(self.store)
        self.assertEqual([r["name"] for r in runs], ["first_run", "second_run"])
        # Default is the most recent run.
        self.assertEqual(ls.pick_run(runs, None)["name"], "second_run")
        # Selectable by name, run-hash prefix, and sessionId prefix.
        self.assertEqual(ls.pick_run(runs, "first_run")["run_hash"], RUN_A)
        self.assertEqual(ls.pick_run(runs, RUN_A[:8])["name"], "first_run")
        self.assertEqual(ls.pick_run(runs, "session-a")["name"], "first_run")

    def test_ambiguous_run_selection_refuses(self):
        runs = ls.load_history(self.store)
        # Both hashes contain no shared prefix, but "" matches everything.
        with self.assertRaises(SystemExit):
            ls.pick_run(runs, "no_such_run")

    def test_extract_filters_to_one_run(self):
        graph = ls.extract(self.store, RUN_B)
        # The other run's task is excluded — the cross-run merge trap,
        # solved by field lookup instead of heuristic.
        self.assertEqual(
            set(graph["tasks"]),
            {ls.abbreviate(PRODUCER), ls.abbreviate(CONSUMER)},
        )

    def test_lid_input_becomes_internal_edge(self):
        graph = ls.extract(self.store, RUN_B)
        internal = [e for e in graph["edges"] if e["producer"] != "EXTERNAL"]
        self.assertEqual(len(internal), 1)
        edge = internal[0]
        self.assertEqual(edge["consumer"], ls.abbreviate(CONSUMER))
        self.assertEqual(edge["producer"], ls.abbreviate(PRODUCER))
        self.assertEqual(edge["filename"], "out.bam")

    def test_external_input_keeps_checksum(self):
        graph = ls.extract(self.store, RUN_B)
        external = [e for e in graph["edges"] if e["producer"] == "EXTERNAL"]
        self.assertEqual(len(external), 1)
        self.assertEqual(external[0]["filename"], "genome.fasta")
        # Content identity travels in `target`; the symlink extractor never
        # had this.
        self.assertIn("abc123", external[0]["target"])

    def test_outputs_and_workdir_recovered(self):
        graph = ls.extract(self.store, RUN_B)
        producer = ls.abbreviate(PRODUCER)
        self.assertEqual(graph["outputs"][producer], ["out.bam"])
        self.assertEqual(
            graph["tasks"][producer]["workdir"],
            f"/work/{PRODUCER[:2]}/{PRODUCER[2:]}",
        )

    def test_tag_preserved_for_domain_parsing(self):
        # Domain adapters attribute subjects by parsing "(tag)" from the
        # name; the adapter must not strip it from `name`, only `process`.
        graph = ls.extract(self.store, RUN_B)
        task = graph["tasks"][ls.abbreviate(PRODUCER)]
        self.assertEqual(task["name"], "PIPE:ALIGN (subject_1)")
        self.assertEqual(task["process"], "PIPE:ALIGN")


PETRI_STORE = Path(__file__).resolve().parent.parent.parent / "Petri" / ".lineage"


@unittest.skipUnless(PETRI_STORE.is_dir(), "Petri lineage store not present")
class RealStoreEquivalence(unittest.TestCase):
    """
    The store adapter must reproduce the exact impact numbers the symlink
    extractor established on the same pipeline (graph5.json regression).
    Same pipeline, two independent capture mechanisms, one answer.
    """

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from core import blast_radius as core
        from domains import sarek

        runs = ls.load_history(PETRI_STORE)
        run = ls.pick_run(runs, "tender_mccarthy")
        cls.graph = ls.extract(PETRI_STORE, run["run_hash"])
        cls.core = core
        cls.sarek = sarek

    def test_shape(self):
        self.assertEqual(len(self.graph["tasks"]), 81)

    def test_no_dangling(self):
        known = set(self.graph["tasks"])
        dangling = [e for e in self.graph["edges"]
                    if e["producer"] not in known and e["producer"] != "EXTERNAL"]
        self.assertEqual(dangling, [])

    def test_withdrawal_numbers_match_symlink_extractor(self):
        donors = self.sarek.load_donors(
            Path(__file__).resolve().parent.parent / "donors.csv")
        entry = self.sarek.subject_entry_nodes(self.graph, donors)
        radius = self.core.blast_radius(self.graph, entry)
        for donor, r in radius.items():
            self.assertEqual(len(entry[donor]), 15, donor)
            self.assertEqual(len(r["affected"]), 16, donor)
            self.assertEqual(len(r["exclusive"]), 15, donor)
            (shared,) = r["shared"]
            self.assertEqual(self.sarek.describe(self.graph, shared), "MULTIQC")

    def test_reference_and_container_numbers_match(self):
        subjects = self.sarek.external_input_entry_nodes(self.graph, "genome.fasta")
        self.assertEqual(len(subjects["input:genome.fasta"]), 41)
        radius = self.core.blast_radius(self.graph, subjects)
        self.assertEqual(len(radius["input:genome.fasta"]["affected"]), 72)

        subjects = self.sarek.container_entry_nodes(self.graph, "gatk4")
        radius = self.core.blast_radius(self.graph, subjects)
        self.assertEqual(len(radius["container:gatk4"]["affected"]), 68)


if __name__ == "__main__":
    unittest.main()
