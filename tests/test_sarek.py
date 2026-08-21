"""
The sarek domain adapter: donor attribution, trigger selection, assertions.

This layer is allowed to be brittle — it parses display names — so its tests
pin down the exact failure modes we have already paid for once.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from domains import sarek


def graph_with_tasks(tasks):
    return {"tasks": tasks, "edges": [], "outputs": {}}


class TestOwnerResolution(unittest.TestCase):
    def setUp(self):
        self.labels = {"donor_1": "donor_1", "donor_10": "donor_10"}

    def test_exact_match(self):
        self.assertEqual(sarek._owner_of("donor_1", self.labels), "donor_1")

    def test_lane_suffix_matches(self):
        # FASTQC reads the FASTQ directly; missing these made five tasks
        # invisible on the real run. The lane suffix must resolve.
        self.assertEqual(sarek._owner_of("donor_1-L1", self.labels), "donor_1")

    def test_prefix_does_not_swallow_longer_donor(self):
        # "donor_1" must not claim "donor_10"'s tasks.
        self.assertEqual(sarek._owner_of("donor_10", self.labels), "donor_10")
        self.assertEqual(sarek._owner_of("donor_10-L1", self.labels), "donor_10")

    def test_non_donor_tags_resolve_to_nothing(self):
        # "(genome)" and friends use the same display-name shape.
        self.assertIsNone(sarek._owner_of("genome", self.labels))
        self.assertIsNone(sarek._owner_of("genome.interval_list", self.labels))


class TestTriggerSelectors(unittest.TestCase):
    def setUp(self):
        self.graph = {
            "tasks": {
                "aa/000001": {"name": "A", "process": "A", "container": "quay.io/gatk4:4.2.1"},
                "bb/000002": {"name": "B", "process": "B", "container": "quay.io/samtools:1.21"},
            },
            "edges": [
                {"consumer": "aa/000001", "producer": "EXTERNAL",
                 "filename": "genome.fasta", "target": "/refs/genome.fasta"},
                {"consumer": "bb/000002", "producer": "EXTERNAL",
                 "filename": "1/genome.fasta", "target": "/refs/genome.fasta"},
                {"consumer": "bb/000002", "producer": "aa/000001",
                 "filename": "out.bam", "target": ""},
            ],
            "outputs": {},
        }

    def test_container_selector(self):
        subjects = sarek.container_entry_nodes(self.graph, "gatk4")
        self.assertEqual(subjects, {"container:gatk4": ["aa/000001"]})

    def test_container_selector_empty_when_no_match(self):
        subjects = sarek.container_entry_nodes(self.graph, "nonexistent")
        self.assertEqual(subjects["container:nonexistent"], [])

    def test_external_input_selector_matches_basename(self):
        # The same reference staged at top level for one task and inside a
        # numbered subdirectory for another must select both.
        subjects = sarek.external_input_entry_nodes(self.graph, "genome.fasta")
        self.assertEqual(
            subjects["input:genome.fasta"], ["aa/000001", "bb/000002"]
        )

    def test_external_input_selector_ignores_internal_edges(self):
        subjects = sarek.external_input_entry_nodes(self.graph, "out.bam")
        self.assertEqual(subjects["input:out.bam"], [])


class TestAssertions(unittest.TestCase):
    def test_missing_file_means_no_assertions(self):
        self.assertEqual(sarek.load_assertions(None), {})

    def test_published_assertion_sets_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "assertions.json"
            path.write_text(json.dumps({
                "published": [{"task": "aa/000001", "what": "Fig 3",
                               "asserted_by": "someone", "date": "2026-08-21"}]
            }))
            published = sarek.load_assertions(path)

        graph = graph_with_tasks({
            "aa/000001": {"name": "A", "process": "A",
                          "container": "img", "script": "run", "workdir": ""},
        })
        facts = sarek.classify(graph, "aa/000001", exclusive=False,
                               published=published)
        self.assertTrue(facts["terminal"])
        # The reason must carry the assertion's provenance, because the claim
        # is the asserter's, not Clew's.
        self.assertIn("someone", facts["reason"])

    def test_unpublished_task_is_not_terminal(self):
        graph = graph_with_tasks({
            "aa/000001": {"name": "A", "process": "A",
                          "container": "img", "script": "run", "workdir": ""},
        })
        facts = sarek.classify(graph, "aa/000001", exclusive=False, published={})
        self.assertFalse(facts["terminal"])


class TestClassification(unittest.TestCase):
    def test_missing_script_fails_closed_to_irreducible(self):
        graph = graph_with_tasks({
            "aa/000001": {"name": "A", "process": "A",
                          "container": "img", "script": "", "workdir": ""},
        })
        facts = sarek.classify(graph, "aa/000001", exclusive=False)
        self.assertEqual(facts["contribution"], "IRREDUCIBLE")

    def test_recorded_script_and_container_is_regenerable(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = graph_with_tasks({
                "aa/000001": {"name": "A", "process": "A",
                              "container": "img", "script": "run", "workdir": tmp},
            })
            facts = sarek.classify(graph, "aa/000001", exclusive=False)
        self.assertEqual(facts["contribution"], "REGENERABLE")

    def test_missing_workdir_is_destroyed(self):
        graph = graph_with_tasks({
            "aa/000001": {"name": "A", "process": "A", "container": "img",
                          "script": "run", "workdir": "/nonexistent/path"},
        })
        facts = sarek.classify(graph, "aa/000001", exclusive=False)
        self.assertEqual(facts["storage"], "DESTROYED")


if __name__ == "__main__":
    unittest.main()
