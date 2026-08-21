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
    cmd = [sys.executable, str(ROOT / "blast.py"),
           "--pipeline", "viralrecon",
           "--graph", str(ROOT / "graph_vr.json"),
           "--samplesheet", str(ROOT / "samplesheets" / "viralrecon_coguk.csv"),
           *extra]
    return subprocess.run(cmd, capture_output=True, text=True)


@unittest.skipUnless((ROOT / "graph_vr.json").exists(), "viralrecon graph not present")
class TestMode(unittest.TestCase):
    def actions(self, *extra):
        out = run_blast(*extra, "--json", "-").stdout
        return json.loads(out[out.index("{"):])["actions"]

    def test_withdrawal_destroys_exclusive(self):
        self.assertEqual(self.actions("--donor", "ERR10000000"),
                         {"DESTROY": 41, "REGENERATE": 5})

    def test_contamination_destroys_nothing(self):
        # Same specimen, same radius — but the data is wrong, not withdrawn,
        # so every artifact survives as a rebuild target.
        self.assertEqual(self.actions("--donor", "ERR10000000",
                                      "--mode", "distrust"),
                         {"REGENERATE": 46})

    def test_remove_refused_for_input_selector(self):
        result = run_blast("--input", "nCoV-2019.primer.bed", "--mode", "remove")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires a subject trigger", result.stderr)


if __name__ == "__main__":
    unittest.main()
