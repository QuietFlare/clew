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

    def test_shape_and_counts(self):
        p = self.payload
        self.assertEqual(p["clew_plan_version"], 1)
        self.assertEqual(p["trigger"], "container:ivar")
        self.assertEqual(p["tasks_total"], 219)
        self.assertEqual(p["tasks_affected"], 160)
        self.assertEqual(sum(p["actions"].values()), 160)
        self.assertEqual(len(p["plan"]), 160)

    def test_regenerate_items_carry_rerun_payload(self):
        regen = [i for i in self.payload["plan"] if i["action"] == "REGENERATE"]
        self.assertTrue(regen)
        for item in regen:
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
