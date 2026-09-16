"""--runs on a Nextflow .lineage store: names, session prefixes, resume chains."""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


from clew.extract import runs


class LineageStoreRuns(unittest.TestCase):
    """A session-id prefix names a resume chain; its newest run stands for it."""

    def setUp(self):
        try:
            from tests.test_lineage_store import RUN_A, RUN_B, RUN_C, CHAIN, OTHER
        except ImportError:  # unittest discover -s tests imports the modules bare
            from test_lineage_store import RUN_A, RUN_B, RUN_C, CHAIN, OTHER
        self.root = Path(tempfile.mkdtemp())
        history = self.root / ".history"
        history.mkdir()
        (history / RUN_A).write_text(f"2026-08-01 10:00:00 CEST\tfirst_run\t{CHAIN}\tlid://{RUN_A}\n")
        (history / RUN_B).write_text(f"2026-08-02 10:00:00 CEST\tsecond_run\t{CHAIN}\tlid://{RUN_B}\n")
        (history / RUN_C).write_text(f"2026-08-03 10:00:00 CEST\tother_run\t{OTHER}\tlid://{RUN_C}\n")
        self.run_b = RUN_B

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_a_session_prefix_resolves_to_the_chain_s_newest_run(self):
        store = runs.Runs(self.root)
        self.assertEqual(store.resolve("session-ch"), ("second_run", self.run_b))
        self.assertEqual(store.resolve("bbbb"), ("second_run", self.run_b))

    def test_a_prefix_spanning_two_sessions_is_still_ambiguous(self):
        with self.assertRaises(SystemExit):
            runs.Runs(self.root).resolve("session-")


if __name__ == "__main__":
    unittest.main()
