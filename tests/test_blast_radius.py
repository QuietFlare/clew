"""
Core traversal on synthetic graphs, including the two must-pass cases from
CLAUDE.md: mixed verdicts from one node, and the load-bearing input.

Synthetic graphs use readable ids ("pool", "paper") precisely because core
must not care — if these tests pass, core never looked inside the strings.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.graph import blast_radius as core
from clew.graph import contribution as c
from clew.ledger import policy


def graph_from(edges, tasks=None):
    """Build a graph dict from (consumer, producer) pairs."""
    return {
        "tasks": tasks or {},
        "edges": [{"consumer": a, "producer": b, "filename": "", "target": ""}
                  for a, b in edges],
        "outputs": {},
    }


class TestTraversal(unittest.TestCase):
    def test_forward_index_inverts_and_skips_external(self):
        g = graph_from([("b", "a"), ("c", "b"), ("a", "EXTERNAL"), ("x", "x")])
        forward = core.forward_index(g["edges"])
        self.assertEqual(forward["a"], {"b"})
        self.assertEqual(forward["b"], {"c"})
        # EXTERNAL is a marker, not a node: nothing is reachable through it.
        self.assertNotIn("EXTERNAL", forward)
        # Self-edges (the Strelka case) must not create cycles.
        self.assertNotIn("x", forward)

    def test_reachable_includes_start_nodes(self):
        g = graph_from([("b", "a"), ("c", "b")])
        forward = core.forward_index(g["edges"])
        self.assertEqual(core.reachable(["a"], forward), {"a", "b", "c"})
        self.assertEqual(core.reachable(["c"], forward), {"c"})

    def test_exclusive_and_shared_split(self):
        # d1 -> own1 -> pool <- own2 <- d2 : the pool is shared, the rest not.
        g = graph_from([("own1", "d1"), ("own2", "d2"),
                        ("pool", "own1"), ("pool", "own2")])
        radius = core.blast_radius(g, {"s1": ["d1"], "s2": ["d2"]})
        self.assertEqual(radius["s1"]["exclusive"], {"d1", "own1"})
        self.assertEqual(radius["s1"]["shared"], {"pool"})
        self.assertEqual(radius["s2"]["exclusive"], {"d2", "own2"})

    def test_paths_provide_checkable_evidence(self):
        g = graph_from([("b", "a"), ("c", "b")])
        forward = core.forward_index(g["edges"])
        paths = core.paths_to(["a"], "c", forward, limit=3)
        self.assertEqual(paths, [["a", "b", "c"]])


class TestEvidenceSearchScales(unittest.TestCase):
    """
    Path evidence is one breadth-first pass, not a walk over every simple
    path. A ladder of diamonds has 2**depth paths from top to bottom; the
    old search enumerated them and a forty-level ladder never finished.
    """

    def ladder(self, depth):
        edges = []
        prev = ["top"]
        for level in range(depth):
            cur = [f"l{level:02d}a", f"l{level:02d}b"]
            for node in cur:
                for parent in prev:
                    edges.append((node, parent))
            prev = cur
        edges.append(("bottom", prev[0]))
        edges.append(("bottom", prev[1]))
        nodes = {n for pair in edges for n in pair}
        return graph_from(edges, tasks={n: {} for n in nodes})

    def test_deep_ladder_answers_every_target_at_once(self):
        g = self.ladder(60)
        forward = core.forward_index(g["edges"])
        tree = core.evidence_tree(["top"], forward)
        self.assertEqual(len(tree), len(g["tasks"]))
        for target in g["tasks"]:
            path = core.path_from(tree, target)
            if target == "top":
                self.assertEqual(path, [])
                continue
            self.assertEqual(path[0], "top")
            self.assertEqual(path[-1], target)
            for a, b in zip(path, path[1:]):
                self.assertIn(b, forward[a], f"{a} -> {b} is not an edge")

    def test_tree_is_deterministic(self):
        g = self.ladder(5)
        forward = core.forward_index(g["edges"])
        first = core.evidence_tree(["top"], forward)
        second = core.evidence_tree(["top"], forward)
        self.assertEqual(first, second)
        self.assertEqual(core.path_from(first, "bottom"),
                         ["top", "l00a", "l01a", "l02a", "l03a", "l04a", "bottom"])

    def test_unreached_target_has_no_path(self):
        g = graph_from([("b", "a"), ("d", "c")])
        forward = core.forward_index(g["edges"])
        self.assertEqual(core.path_from(core.evidence_tree(["a"], forward), "d"), [])
        self.assertEqual(core.paths_to(["a"], "d", forward), [])


class TestMixedVerdictsFromOneNode(unittest.TestCase):
    """
    The withdrawn donor fed a pool. The pool fed BOTH a published paper and
    an unpublished analysis. One traversal must produce two different
    answers — NOTIFY_ONLY on the published branch, REGENERATE on the other.
    If the model cannot do this, it is wrong.
    """

    def test_two_answers_from_one_pool(self):
        g = graph_from([
            ("pool", "d_withdrawn"), ("pool", "d_other"),
            ("paper", "pool"), ("analysis", "pool"),
        ])
        radius = core.blast_radius(
            g, {"withdrawn": ["d_withdrawn"], "other": ["d_other"]}
        )
        affected = radius["withdrawn"]["affected"]
        self.assertEqual(affected, {"d_withdrawn", "pool", "paper", "analysis"})

        exclusive = radius["withdrawn"]["exclusive"]
        published = {"paper"}  # asserted from outside, as in real use

        verdicts = {}
        for node in affected:
            verdicts[node] = policy.remediate(
                c.REGENERABLE,
                exclusive=node in exclusive,
                terminal=node in published,
            )

        self.assertEqual(verdicts["paper"], c.NOTIFY_ONLY)
        self.assertEqual(verdicts["analysis"], c.REGENERATE)
        self.assertEqual(verdicts["d_withdrawn"], c.DESTROY)
        # The pool itself is shared and unpublished: rebuild it.
        self.assertEqual(verdicts["pool"], c.REGENERATE)


class TestLoadBearingInput(unittest.TestCase):
    """
    Removing a reference/control invalidates everything calibrated against
    it — artifacts that are NOT downstream of any donor. The trigger is the
    input itself, and the blast radius must reach past the donors entirely.
    """

    def test_reference_reaches_what_donors_do_not(self):
        # Two donors calibrated against one reference; one unrelated task.
        g = graph_from([
            ("align1", "d1"), ("align1", "ref"),
            ("align2", "d2"), ("align2", "ref"),
            ("stats1", "align1"), ("stats2", "align2"),
            ("unrelated", "other_input"),
        ])
        radius = core.blast_radius(g, {"input:ref": ["ref"]})
        affected = radius["input:ref"]["affected"]

        # Everything calibrated against the reference, transitively.
        self.assertEqual(
            affected, {"ref", "align1", "align2", "stats1", "stats2"}
        )
        # But not things that never touched it.
        self.assertNotIn("unrelated", affected)

        # A donor's own radius does NOT cover the other donor's artifacts —
        # only the reference trigger reaches both. That asymmetry is the point.
        donor_radius = core.blast_radius(g, {"d1": ["d1"]})
        self.assertNotIn("align2", donor_radius["d1"]["affected"])


if __name__ == "__main__":
    unittest.main()
