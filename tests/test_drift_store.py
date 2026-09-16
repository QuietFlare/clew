"""
Drift pairs tasks by name across two runs, compares output digests, and
names the first task on each chain that differs together with the cause
read from the record. Every verdict is pinned on a small synthetic chain.
"""

import sys
import unittest
from pathlib import Path


from clew.questions import drift


class SameChain(unittest.TestCase):
    """
    The store holds one graph per resume chain. Two runs of one chain
    load the same graph, and drift compared it with itself.
    """

    def test_two_runs_of_one_session_are_refused(self):
        import contextlib
        import io
        import tempfile
        try:
            from tests.test_lineage_store import RUN_A, RUN_B, CHAIN, PRODUCER, task_run, write_record
        except ImportError:  # unittest discover -s tests imports the modules bare
            from test_lineage_store import RUN_A, RUN_B, CHAIN, PRODUCER, task_run, write_record
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp)
            (store / ".history").mkdir()
            (store / ".history" / RUN_A).write_text(
                f"2026-08-01 10:00:00 CEST\tfirst_run\t{CHAIN}\tlid://{RUN_A}\n")
            (store / ".history" / RUN_B).write_text(
                f"2026-08-02 10:00:00 CEST\tsecond_run\t{CHAIN}\tlid://{RUN_B}\n")
            write_record(store / PRODUCER, task_run(CHAIN, RUN_A, "PIPE:ALIGN"))
            with self.assertRaises(SystemExit) as refused, \
                    contextlib.redirect_stdout(io.StringIO()):
                drift.main(["--runs", tmp, "--before", "first_run", "--after", "second_run"])
        self.assertIn("same graph", str(refused.exception))
        self.assertIn("resume chain", str(refused.exception))


if __name__ == "__main__":
    unittest.main()
