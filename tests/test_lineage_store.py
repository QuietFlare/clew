"""
The native lineage-store adapter, on a synthetic store and (when the
sibling Petri store is present) on a real sarek run.

The synthetic store models a resume chain, because that is where the
adapter earns its keep: a cached task writes no new record, so the one from
the run that first executed it still stands and still names that run. Only
`sessionId` spans the chain.

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

from clew import extract_from_lineage_store as ls

RUN_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
RUN_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
RUN_C = "cccccccccccccccccccccccccccccccc"
PRODUCER = "11112222333344445555666677778888"
CONSUMER = "99990000aaaabbbbccccddddeeeeffff"
CONSUMER_V2 = "5555666677778888999900001111aaaa"
OTHER_SESSION_TASK = "fedcba9876543210fedcba9876543210"

CHAIN = "session-chain"
OTHER = "session-other"


def write_record(path, record):
    path.mkdir(parents=True, exist_ok=True)
    (path / ".data.json").write_text(json.dumps(record))


def task_run(session, workflow_run, name, **extra):
    spec = {
        "sessionId": session, "workflowRun": f"lid://{workflow_run}",
        "name": name, "container": "quay.io/tool:1.0", "script": "run",
        "input": [],
    }
    spec.update(extra)
    return {"version": "lineage/v1beta1", "kind": "TaskRun", "spec": spec}


class SyntheticStore(unittest.TestCase):
    """
    Two runs in one resume chain, plus one unrelated run sharing the store.

    Run A executed PRODUCER and CONSUMER. Run B resumed, re-executed
    CONSUMER as CONSUMER_V2, and reused PRODUCER from run A.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name)

        history = self.store / ".history"
        history.mkdir()
        (history / RUN_A).write_text(
            f"2026-08-01 10:00:00 CEST\tfirst_run\t{CHAIN}\tlid://{RUN_A}\n")
        (history / RUN_B).write_text(
            f"2026-08-02 10:00:00 CEST\tsecond_run\t{CHAIN}\tlid://{RUN_B}\n")
        (history / RUN_C).write_text(
            f"2026-08-03 10:00:00 CEST\tother_run\t{OTHER}\tlid://{RUN_C}\n")

        # Executed in run A, cached in run B: its record still names run A.
        write_record(self.store / PRODUCER, task_run(
            CHAIN, RUN_A, "PIPE:ALIGN (subject_1)",
            input=[
                {"type": "val", "name": "meta", "value": {"id": "subject_1"}},
                {"type": "path", "name": "ref", "value": [
                    {"path": "https://example.org/ref/genome.fasta",
                     "checksum": {"value": "abc123", "algorithm": "nextflow",
                                  "mode": "standard"}}]},
            ]))
        write_record(self.store / PRODUCER / "out.bam", {
            "version": "lineage/v1beta1", "kind": "FileOutput",
            "spec": {"path": f"/work/{PRODUCER[:2]}/{PRODUCER[2:]}/out.bam",
                     "checksum": {"value": "d1", "algorithm": "nextflow",
                                  "mode": "standard"},
                     "source": f"lid://{PRODUCER}",
                     "workflowRun": f"lid://{RUN_A}", "size": 12,
                     "taskRun": f"lid://{PRODUCER}"},
        })

        # The first version of CONSUMER, from run A, later superseded.
        write_record(self.store / CONSUMER, task_run(
            CHAIN, RUN_A, "PIPE:STATS (subject_1)",
            input=[{"type": "path", "name": "bam",
                    "value": [f"lid://{PRODUCER}/out.bam"]}]))
        write_record(Path(f"{self.store / CONSUMER}#output"), {
            "version": "lineage/v1beta1", "kind": "TaskOutput",
            "spec": {"createdAt": "2026-08-01T10:05:00+02:00", "output": []},
        })

        # Re-executed in run B after an edit: same name, newer outputs.
        write_record(self.store / CONSUMER_V2, task_run(
            CHAIN, RUN_B, "PIPE:STATS (subject_1)",
            input=[{"type": "path", "name": "bam",
                    "value": [f"lid://{PRODUCER}/out.bam"]}]))
        write_record(Path(f"{self.store / CONSUMER_V2}#output"), {
            "version": "lineage/v1beta1", "kind": "TaskOutput",
            "spec": {"createdAt": "2026-08-02T10:05:00+02:00", "output": []},
        })

        # A different pipeline sharing the store; must never be pulled in.
        write_record(self.store / OTHER_SESSION_TASK,
                     task_run(OTHER, RUN_C, "PIPE:OLD"))

        # A workflow-level "#output" entry, which is not a task.
        write_record(Path(str(self.store / PRODUCER) + "#output"), {
            "version": "lineage/v1beta1", "kind": "TaskOutput",
            "spec": {"createdAt": "2026-08-01T10:01:00+02:00", "output": []}})

    def tearDown(self):
        self.tmp.cleanup()

    def test_history_and_run_selection(self):
        runs = ls.load_history(self.store)
        self.assertEqual([r["name"] for r in runs],
                         ["first_run", "second_run", "other_run"])
        # Default is the most recent run.
        self.assertEqual(ls.pick_run(runs, None)["name"], "other_run")
        # Selectable by name and by run-hash prefix.
        self.assertEqual(ls.pick_run(runs, "first_run")["run_hash"], RUN_A)
        self.assertEqual(ls.pick_run(runs, RUN_A[:8])["name"], "first_run")

    def test_ambiguous_run_selection_refuses(self):
        runs = ls.load_history(self.store)
        with self.assertRaises(SystemExit):
            ls.pick_run(runs, "no_such_run")

    def test_chain_groups_runs_by_session(self):
        runs = ls.load_history(self.store)
        chain = ls.chain_of(runs, CHAIN)
        self.assertEqual([r["name"] for r in chain],
                         ["first_run", "second_run"])

    def test_a_cached_task_stays_in_the_chain(self):
        """
        The whole point. PRODUCER's record names run A, and run B reused it.
        Filtering by run would drop it and orphan its consumer.
        """
        graph = ls.extract(self.store, CHAIN)
        self.assertIn(ls.abbreviate(PRODUCER), graph["tasks"])
        self.assertIn(ls.abbreviate(CONSUMER_V2), graph["tasks"])

    def test_another_session_is_excluded(self):
        graph = ls.extract(self.store, CHAIN)
        self.assertNotIn(ls.abbreviate(OTHER_SESSION_TASK), graph["tasks"])

    def test_the_cached_edge_resolves(self):
        """
        Without session scoping this edge was DANGLING, which is what made
        a resumed run look externally fed.
        """
        graph = ls.extract(self.store, CHAIN)
        known = set(graph["tasks"])
        dangling = [e for e in graph["edges"]
                    if e["producer"] not in known
                    and e["producer"] != "EXTERNAL"]
        self.assertEqual(dangling, [])

    def test_a_replaced_task_is_marked_not_dropped(self):
        """
        Its outputs may still be on disk, so a deletion plan needs it.
        """
        graph = ls.extract(self.store, CHAIN)
        older = graph["tasks"][ls.abbreviate(CONSUMER)]
        newer = graph["tasks"][ls.abbreviate(CONSUMER_V2)]
        self.assertTrue(older.get("superseded"))
        self.assertNotIn("superseded", newer)

    def test_an_unorderable_pair_claims_nothing(self):
        """
        Without timestamps there is no way to tell which version is live,
        and guessing is worse than admitting it.
        """
        (self.store / f"{CONSUMER}#output" / ".data.json").unlink()
        graph = ls.extract(self.store, CHAIN)
        self.assertNotIn("superseded",
                         graph["tasks"][ls.abbreviate(CONSUMER)])
        self.assertNotIn("superseded",
                         graph["tasks"][ls.abbreviate(CONSUMER_V2)])

    def test_external_input_keeps_checksum(self):
        graph = ls.extract(self.store, CHAIN)
        external = [e for e in graph["edges"] if e["producer"] == "EXTERNAL"]
        self.assertEqual(len(external), 1)
        self.assertEqual(external[0]["filename"], "genome.fasta")
        self.assertIn("abc123", external[0]["target"])

    def test_outputs_and_workdir_recovered(self):
        graph = ls.extract(self.store, CHAIN)
        producer = ls.abbreviate(PRODUCER)
        self.assertEqual(graph["outputs"][producer], ["out.bam"])
        self.assertEqual(
            graph["tasks"][producer]["workdir"],
            f"/work/{PRODUCER[:2]}/{PRODUCER[2:]}",
        )

    def test_tag_preserved_for_domain_parsing(self):
        # Domain adapters attribute subjects by parsing "(tag)" from the
        # name; the adapter must not strip it from `name`, only `process`.
        graph = ls.extract(self.store, CHAIN)
        task = graph["tasks"][ls.abbreviate(PRODUCER)]
        self.assertEqual(task["name"], "PIPE:ALIGN (subject_1)")
        self.assertEqual(task["process"], "PIPE:ALIGN")

    def test_an_unknown_store_version_is_refused(self):
        """
        A renamed field in a version we do not know empties a graph
        silently, so it is a hard stop rather than a warning.
        """
        write_record(self.store / ("d" * 32), {
            "version": "lineage/v2", "kind": "TaskRun",
            "spec": {"sessionId": CHAIN, "name": "NEW", "input": []}})
        with self.assertRaises(SystemExit):
            ls.extract(self.store, CHAIN)


class Coverage(unittest.TestCase):
    """
    What the graph could not see, carried on the graph rather than printed.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name)
        (self.store / ".history").mkdir()
        write_record(self.store / PRODUCER,
                     task_run(CHAIN, RUN_A, "PIPE:ALIGN"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_exit_status_is_always_declared(self):
        """
        True of every store, so a reader is told every time.
        """
        graph = ls.extract(self.store, CHAIN)
        self.assertTrue(any("exit status" in n for n in graph["coverage"]))

    def test_a_metadata_checksum_is_declared_as_such(self):
        """
        Standard mode hashes path and mtime, so it changes on copy and
        cannot identify a published artifact.
        """
        write_record(self.store / PRODUCER / "out.bam", {
            "version": "lineage/v1beta1", "kind": "FileOutput",
            "spec": {"path": f"/work/{PRODUCER[:2]}/{PRODUCER[2:]}/out.bam",
                     "checksum": {"value": "d1", "algorithm": "nextflow",
                                  "mode": "standard"}, "size": 1}})
        graph = ls.extract(self.store, CHAIN)
        self.assertTrue(any("standard" in n for n in graph["coverage"]))

    def test_a_content_checksum_is_not_flagged(self):
        write_record(self.store / PRODUCER / "out.bam", {
            "version": "lineage/v1beta1", "kind": "FileOutput",
            "spec": {"path": f"/work/{PRODUCER[:2]}/{PRODUCER[2:]}/out.bam",
                     "checksum": {"value": "d1", "algorithm": "nextflow",
                                  "mode": "sha256"}, "size": 1}})
        graph = ls.extract(self.store, CHAIN)
        self.assertFalse(any("cannot identify a copy" in n
                             for n in graph["coverage"]))

    def test_an_unread_record_kind_is_reported(self):
        """
        AgentRun exists today and this adapter skips it. Silence there
        would read as "nothing else was in the store".
        """
        write_record(self.store / ("e" * 32), {
            "version": "lineage/v1beta1", "kind": "AgentRun", "spec": {}})
        graph = ls.extract(self.store, CHAIN)
        self.assertTrue(any("AgentRun" in n for n in graph["coverage"]))


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
        from clew.core import blast_radius as core
        from clew.domains import sarek

        runs = ls.load_history(PETRI_STORE)
        run = ls.pick_run(runs, "tender_mccarthy")
        cls.graph = ls.extract(PETRI_STORE, run["session_id"])
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
            Path(__file__).resolve().parent.parent / "clew" / "data" / "donors.csv")
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
