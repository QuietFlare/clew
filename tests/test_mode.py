"""
Selector × mode: the same subject trigger must produce different verdicts
under remove (withdrawal) and distrust (contamination), and remove must be
refused for non-subject selectors.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_blast(*extra):
    cmd = [sys.executable, "-m", "clew.impact",
           "--pipeline", "viralrecon",
           "--graph", str(ROOT / "clew" / "data" / "graph_vr.json"),
           "--samplesheet", str(ROOT / "clew" / "data" / "samplesheets" / "viralrecon_coguk.csv"),
           *extra]
    return subprocess.run(cmd, capture_output=True, text=True)


@unittest.skipUnless((ROOT / "clew" / "data" / "graph_vr.json").exists(), "viralrecon graph not present")
class TestMode(unittest.TestCase):
    def plan(self, *extra):
        out = run_blast(*extra, "--json", "-").stdout
        return json.loads(out[out.index("{"):])["plan"]

    # NOTE these assert the exclusive/terminal FACTS, not the final actions.
    # Actions also depend on storage — live disk state — so pinning them
    # made the suite fail the day work/ was (correctly) cleaned up. The
    # facts→action table itself is pinned hermetically in test_policy.
    #
    # These runs pass no --work-root, so storage is unverified and most items
    # come back with action None and a `possible` set instead. That is the
    # point of the design, so the assertions below check the whole set of
    # outcomes still in play rather than one settled verdict.

    def outcomes(self, item):
        """Every action this item could still resolve to."""
        return [item["action"]] if item["action"] else list(item["possible"])

    def test_withdrawal_marks_exclusive(self):
        plan = self.plan("--donor", "ERR10000000")
        exclusive = [i for i in plan if i["exclusive"]]
        self.assertEqual(len(exclusive), 41)
        self.assertEqual(len(plan), 46)
        # Exclusive artifacts must never resolve to a rebuild: they either
        # get destroyed, are already gone, or are quarantined unwritable.
        # True whether or not storage has been checked — an unverified item
        # must not have a rebuild among its possibilities either.
        for i in exclusive:
            for outcome in self.outcomes(i):
                self.assertIn(outcome,
                              ("DESTROY", "ALREADY_GONE", "QUARANTINE"), i["task"])

    def test_contamination_marks_nothing_exclusive(self):
        # Same specimen, same radius — but the data is wrong, not withdrawn,
        # so nothing is owned-and-destroyable.
        plan = self.plan("--donor", "ERR10000000", "--mode", "distrust")
        self.assertEqual(len(plan), 46)
        self.assertEqual([i for i in plan if i["exclusive"]], [])
        for i in plan:
            self.assertNotIn("DESTROY", self.outcomes(i), i["task"])

    def test_remove_refused_for_input_selector(self):
        result = run_blast("--input", "nCoV-2019.primer.bed", "--mode", "remove")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires a subject trigger", result.stderr)


if __name__ == "__main__":
    unittest.main()
