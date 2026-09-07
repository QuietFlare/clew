"""
The demo must settle something in every act without claiming a disk it
has not seen. What is open says what would settle it.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_demo(*args):
    return subprocess.run([sys.executable, "-m", "clew.demo", *args],
                          capture_output=True, text=True, cwd=ROOT)


class TestWithoutADisk(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run_demo()

    def test_it_runs(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr)

    def test_every_act_settles_the_published_report(self):
        acts = self.result.stdout.split("=" * 70)
        settled = [a for a in acts if "NOTIFY_ONLY" in a]
        self.assertGreaterEqual(len(settled), 3)

    def test_open_verdicts_name_what_each_storage_state_gives(self):
        out = self.result.stdout
        self.assertNotIn("UNDETERMINED", out)
        self.assertIn("OPEN", out)
        self.assertIn("REGENERATE   if the workdir is still there", out)
        self.assertIn("DESTROY      if the workdir is still there", out)
        self.assertIn("ALREADY_GONE if it was cleaned", out)

    def test_it_says_why_storage_is_open_and_what_settles_it(self):
        self.assertIn("Clew never guesses", self.result.stdout)
        self.assertIn("--work-root", self.result.stdout)


class TestWithAWorkRoot(unittest.TestCase):
    """
    One live workdir settles its task. The rest were looked for and not
    found, which is not ALREADY_GONE until the published tree is checked.
    """

    def test_a_present_workdir_settles_and_a_cleaned_one_stays_open(self):
        graph = json.loads((ROOT / "clew" / "data" / "graph5.json").read_text())
        workdir = graph["tasks"]["06/f8d5a0"]["workdir"]
        with tempfile.TemporaryDirectory() as work_root:
            Path(work_root, *Path(workdir).parts[-2:]).mkdir(parents=True)
            result = run_demo("--work-root", work_root)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        self.assertIn(f"Storage checked under {work_root}", out)
        self.assertIn("REGENERATE     1  recompute", out)
        self.assertIn("published copies not checked", out)
        self.assertNotIn("ALREADY_GONE  ", out)


if __name__ == "__main__":
    unittest.main()
