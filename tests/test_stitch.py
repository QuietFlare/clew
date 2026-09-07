"""
Stitch joins runs by content digest and by nothing else. A second run's
EXTERNAL input that carries the digest of a first run's output is rewritten
to point at the task that produced it.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.extract import stitch


def run_a():
    return {
        "tasks": {"11/aaaaaa": {"hash": "11/aaaaaa", "process": "COUNT"}},
        "edges": [],
        "outputs": {"11/aaaaaa": ["counts.tsv"]},
        "output_details": {"11/aaaaaa": [
            {"file": "counts.tsv", "size": 10, "digest": "sha256:c0ffee"}]},
    }


def run_b(digest="sha256:c0ffee"):
    edge = {"consumer": "22/bbbbbb", "producer": "EXTERNAL",
            "filename": "counts.tsv", "target": "/pub/counts.tsv"}
    if digest:
        edge["digest"] = digest
    return {
        "tasks": {"22/bbbbbb": {"hash": "22/bbbbbb", "process": "DESEQ"}},
        "edges": [edge],
        "outputs": {"22/bbbbbb": ["de.tsv"]},
    }


class Stitch(unittest.TestCase):
    def test_a_matching_digest_becomes_a_bridge(self):
        merged, bridges = stitch.stitch({"a": run_a(), "b": run_b()})
        self.assertEqual(bridges, [{"consumer": "b:22/bbbbbb", "producer": "a:11/aaaaaa",
                                    "digest": "sha256:c0ffee", "path": "/pub/counts.tsv",
                                    "candidates": 1}])
        self.assertNotIn("coverage", merged)
        self.assertEqual(merged["edges"][0]["producer"], "a:11/aaaaaa")
        self.assertEqual(sorted(merged["tasks"]), ["a:11/aaaaaa", "b:22/bbbbbb"])

    def test_no_digest_no_bridge(self):
        merged, bridges = stitch.stitch({"a": run_a(), "b": run_b(digest=None)})
        self.assertEqual(bridges, [])
        self.assertEqual(merged["edges"][0]["producer"], "EXTERNAL")

    def test_a_different_algorithm_does_not_match(self):
        _, bridges = stitch.stitch({"a": run_a(), "b": run_b(digest="nextflow-deep:c0ffee")})
        self.assertEqual(bridges, [])

    def test_a_run_never_bridges_to_itself(self):
        a = run_a()
        a["edges"].append({"consumer": "11/aaaaaa", "producer": "EXTERNAL",
                           "filename": "counts.tsv", "target": "/x", "digest": "sha256:c0ffee"})
        _, bridges = stitch.stitch({"a": a})
        self.assertEqual(bridges, [])

    def test_a_shared_digest_bridges_to_every_producer(self):
        """
        Two tasks in run a wrote the same bytes. Picking one would drop the
        other from every blast radius, so the consumer gets an edge to each
        and the graph says so.
        """
        a = run_a()
        a["tasks"]["12/cccccc"] = {"hash": "12/cccccc", "process": "COUNT"}
        a["outputs"]["12/cccccc"] = ["counts.tsv"]
        a["output_details"]["12/cccccc"] = [
            {"file": "counts.tsv", "size": 10, "digest": "sha256:c0ffee"}]
        merged, bridges = stitch.stitch({"a": a, "b": run_b()})
        self.assertEqual(sorted(b["producer"] for b in bridges),
                         ["a:11/aaaaaa", "a:12/cccccc"])
        self.assertEqual({b["candidates"] for b in bridges}, {2})
        self.assertEqual(sorted(e["producer"] for e in merged["edges"]),
                         ["a:11/aaaaaa", "a:12/cccccc"])
        self.assertEqual(stitch.ambiguous_bridges(bridges),
                         {"sha256:c0ffee": ["a:11/aaaaaa", "a:12/cccccc"]})
        self.assertIn("more than one task", merged["coverage"][0])

    def test_coverage_names_what_each_graph_carries(self):
        self.assertEqual(stitch.digest_coverage({"a": run_a(), "b": run_b()}),
                         {"a": (1, 0), "b": (0, 1)})


if __name__ == "__main__":
    unittest.main()
