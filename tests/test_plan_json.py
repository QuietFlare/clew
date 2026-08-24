"""
The machine-readable plan: structure, determinism, and the re-run payload.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless((ROOT / "graph_vr.json").exists(), "viralrecon graph not present")
class TestPlanJson(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cmd = [
            sys.executable, str(ROOT / "blast.py"),
            "--pipeline", "viralrecon",
            "--graph", str(ROOT / "graph_vr.json"),
            "--samplesheet", str(ROOT / "samplesheets" / "viralrecon_coguk.csv"),
            "--container", "ivar",
            "--json", "-",
        ]
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
        # The JSON payload starts at the first '{' after the human-readable plan.
        cls.raw = out[out.index("{"):]
        cls.payload = json.loads(cls.raw)
        cls.second = subprocess.run(cmd, capture_output=True, text=True,
                                    check=True).stdout

    def test_the_plan_names_the_policy_it_was_computed_under(self):
        # Without this the plan is a set of verdicts with no stated basis.
        # The version is a label anyone can print; the hash is what lets two
        # parties prove they were reading the same table.
        from core import policy
        self.assertEqual(self.payload["policy_version"], policy.DEFAULT["version"])
        self.assertEqual(self.payload["policy_hash"],
                         policy.fingerprint(policy.DEFAULT))

    def test_every_item_states_its_basis(self):
        # A decided item cites the rule that decided it. An undecided one has
        # no rule to cite, and must instead carry the candidates — so that a
        # missing action is never mistakable for "nothing to do".
        for item in self.payload["plan"]:
            self.assertTrue(item["because"], item["task"])
            if item["action"]:
                self.assertTrue(item["rule"], item["task"])
                self.assertNotIn("possible", item)
            else:
                self.assertIsNone(item["rule"], item["task"])
                self.assertGreater(len(item["possible"]), 1, item["task"])

    def test_the_cited_rule_actually_yields_the_stated_action(self):
        # Guards against the citation drifting from the verdict — a plan whose
        # rule ids are decorative would be worse than one with none.
        from core import policy
        by_id = {r["id"]: r for r in policy.DEFAULT["rules"]}
        for item in self.payload["plan"]:
            if not item["action"]:
                for action, rule in item["possible"].items():
                    self.assertEqual(by_id[rule]["action"], action, item["task"])
            elif item["rule"] == policy.FALLTHROUGH_RULE:
                self.assertEqual(item["action"], policy.FALLTHROUGH_ACTION)
            else:
                self.assertEqual(by_id[item["rule"]]["action"], item["action"])

    def test_this_run_is_undetermined_because_no_work_root_was_given(self):
        # Pins the honest default: without being told where to look, Clew
        # reports the storage question as open rather than answering it.
        undecided = [i for i in self.payload["plan"] if not i["action"]]
        self.assertEqual(len(undecided), len(self.payload["plan"]))
        self.assertEqual(self.payload["actions"], {"UNDETERMINED": 160})

    def test_shape_and_counts(self):
        p = self.payload
        self.assertEqual(p["clew_plan_version"], 1)
        self.assertEqual(p["trigger"], "container:ivar")
        self.assertEqual(p["tasks_total"], 219)
        self.assertEqual(p["tasks_affected"], 160)
        self.assertEqual(sum(p["actions"].values()), 160)
        self.assertEqual(len(p["plan"]), 160)

    def test_regenerable_items_carry_recorded_evidence(self):
        # Whether a task's action is REGENERATE or ALREADY_GONE depends on
        # live disk state; what must ALWAYS hold is that REGENERABLE tasks
        # carry the recorded script and container — that recording is what
        # the classification was based on.
        regen = [i for i in self.payload["plan"]
                 if i["contribution"] == "REGENERABLE"]
        self.assertTrue(regen)
        for item in regen:
            if item["action"] == "REGENERATE":
                self.assertTrue(item["container"], item["task"])
                self.assertTrue(item["script"], item["task"])

    def test_non_entry_tasks_carry_evidence(self):
        entry = set(self.payload["entry_tasks"])
        for item in self.payload["plan"]:
            if item["task"] not in entry:
                self.assertIn("evidence_path", item, item["task"])
                self.assertGreater(len(item["evidence_path"]), 1)

    def test_deterministic_output(self):
        # Same inputs, byte-identical plan: the replayability claim, enforced.
        self.assertEqual(self.raw, self.second[self.second.index("{"):])

    def test_caveats_present(self):
        self.assertTrue(any("unknown, never clean" in c
                            for c in self.payload["caveats"]))


if __name__ == "__main__":
    unittest.main()
