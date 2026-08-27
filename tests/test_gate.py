"""
The pre-flight gate: three outcomes, and every way of failing to check.

The tests that matter are the ones about UNKNOWN. A gate reporting "not
blocked" for a subject it never found is a gate that passes everything it
failed to check, and it goes green on the day someone points it at the wrong
log or mistypes an identifier. That is the same failure shape as the storage
probe, and it is guarded here explicitly.

No database. The core takes plain dicts, so the whole decision surface is
testable without one.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.core import gate

ROOT = Path(__file__).resolve().parent.parent

BLOCK = ["Withdrawn", "Contaminated"]
CLEAR = ["Reinstated", "Passed"]


def fact(seq, subject, event_type, effective_from, actor="tester"):
    return {"seq": seq, "subject": subject, "event_type": event_type,
            "effective_from": effective_from,
            "recorded_at": "2026-08-01T00:00:00+00:00",
            "actor": actor, "hash": f"{seq:064d}", "prev_hash": "",
            "body": "{}"}


class TestOutcomes(unittest.TestCase):
    def test_a_blocking_fact_blocks(self):
        result = gate.decide(["s1"], [fact(1, "s1", "Withdrawn", "2026-01-01")],
                             BLOCK, CLEAR)
        self.assertEqual(result["subjects"]["s1"]["status"], gate.BLOCKED)
        self.assertFalse(result["passed"])

    def test_a_clearing_fact_clears(self):
        result = gate.decide(["s1"], [fact(1, "s1", "Passed", "2026-01-01")],
                             BLOCK, CLEAR)
        self.assertEqual(result["subjects"]["s1"]["status"], gate.CLEARED)
        self.assertTrue(result["passed"])

    def test_an_unheard_of_subject_is_unknown_not_cleared(self):
        # The distinction the whole file exists for.
        result = gate.decide(["s1"], [], BLOCK, CLEAR)
        self.assertEqual(result["subjects"]["s1"]["status"], gate.UNKNOWN)
        self.assertIsNone(result["subjects"]["s1"]["fact"])

    def test_unknown_stops_the_build_by_default(self):
        self.assertFalse(gate.decide(["s1"], [], BLOCK, CLEAR)["passed"])

    def test_unknown_can_be_allowed_but_only_deliberately(self):
        result = gate.decide(["s1"], [], BLOCK, CLEAR, unknown_blocks=False)
        self.assertTrue(result["passed"])
        # Still reported as unknown. Allowing it must not relabel it as clean.
        self.assertEqual(result["subjects"]["s1"]["status"], gate.UNKNOWN)
        self.assertEqual(result["counts"][gate.UNKNOWN], 1)

    def test_irrelevant_fact_types_do_not_decide_anything(self):
        # A log holds many facts that are not gate decisions.
        result = gate.decide(["s1"], [fact(1, "s1", "Published", "2026-01-01")],
                             BLOCK, CLEAR)
        self.assertEqual(result["subjects"]["s1"]["status"], gate.UNKNOWN)

    def test_every_verdict_names_the_fact_behind_it(self):
        result = gate.decide(["s1"], [fact(7, "s1", "Withdrawn", "2026-01-01",
                                           actor="registry@example.org")],
                             BLOCK, CLEAR)
        evidence = result["subjects"]["s1"]["fact"]
        self.assertEqual(evidence["seq"], 7)
        self.assertEqual(evidence["actor"], "registry@example.org")
        self.assertEqual(evidence["event_type"], "Withdrawn")


class TestLatestEffectiveWins(unittest.TestCase):
    """
    Decisions get reversed. A gate that only ever accumulated prohibitions
    would refuse legitimate work forever.
    """

    def test_a_reinstatement_after_a_withdrawal_clears(self):
        entries = [fact(1, "s1", "Withdrawn", "2026-01-01"),
                   fact(2, "s1", "Reinstated", "2026-06-01")]
        self.assertEqual(gate.decide(["s1"], entries, BLOCK, CLEAR)
                         ["subjects"]["s1"]["status"], gate.CLEARED)

    def test_a_withdrawal_after_a_reinstatement_blocks(self):
        entries = [fact(1, "s1", "Reinstated", "2026-01-01"),
                   fact(2, "s1", "Withdrawn", "2026-06-01")]
        self.assertEqual(gate.decide(["s1"], entries, BLOCK, CLEAR)
                         ["subjects"]["s1"]["status"], gate.BLOCKED)

    def test_effective_order_decides_not_log_order(self):
        # Entered later, effective earlier. What matters is when the decision
        # was made in the world, not when we happened to hear about it.
        entries = [fact(1, "s1", "Withdrawn", "2026-06-01"),
                   fact(2, "s1", "Reinstated", "2026-01-01")]
        self.assertEqual(gate.decide(["s1"], entries, BLOCK, CLEAR)
                         ["subjects"]["s1"]["status"], gate.BLOCKED)

    def test_log_order_breaks_a_same_day_tie(self):
        # The only tiebreak nobody can back-date.
        entries = [fact(1, "s1", "Withdrawn", "2026-01-01"),
                   fact(2, "s1", "Reinstated", "2026-01-01")]
        self.assertEqual(gate.decide(["s1"], entries, BLOCK, CLEAR)
                         ["subjects"]["s1"]["status"], gate.CLEARED)


class TestAsOf(unittest.TestCase):
    """
    'Was this run permitted when we ran it?' — a different question from
    'is it permitted now', and both have to be answerable.
    """

    def setUp(self):
        self.entries = [fact(1, "s1", "Contaminated", "2026-04-12"),
                        fact(2, "s1", "Passed", "2026-05-20")]

    def test_as_of_before_the_reversal_still_blocks(self):
        result = gate.decide(["s1"], self.entries, BLOCK, CLEAR,
                             as_of="2026-05-01")
        self.assertEqual(result["subjects"]["s1"]["status"], gate.BLOCKED)

    def test_as_of_after_the_reversal_clears(self):
        result = gate.decide(["s1"], self.entries, BLOCK, CLEAR,
                             as_of="2026-06-01")
        self.assertEqual(result["subjects"]["s1"]["status"], gate.CLEARED)

    def test_facts_effective_in_the_future_do_not_count(self):
        # Otherwise every historical gate result becomes unreproducible the
        # moment somebody records something new.
        result = gate.decide(["s1"], self.entries, BLOCK, CLEAR,
                             as_of="2026-01-01")
        self.assertEqual(result["subjects"]["s1"]["status"], gate.UNKNOWN)

    def test_a_historical_result_is_stable(self):
        first = gate.decide(["s1"], self.entries, BLOCK, CLEAR,
                            as_of="2026-05-01")
        later = gate.decide(["s1"], self.entries + [
            fact(3, "s1", "Withdrawn", "2027-01-01")], BLOCK, CLEAR,
            as_of="2026-05-01")
        self.assertEqual(first["subjects"], later["subjects"])


class TestCliFailsClosed(unittest.TestCase):
    """Every way of not establishing permission must exit non-zero."""

    def run_gate(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "clew.gate", *args],
            capture_output=True, text=True)

    def test_no_blocking_types_is_refused(self):
        result = self.run_gate("--samplesheet", str(ROOT / "clew" / "data" / "donors.csv"),
                               "--dsn", "postgresql://nowhere/none")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no blocking fact types", result.stderr)

    def test_no_connection_string_is_refused(self):
        env_free = subprocess.run(
            [sys.executable, "-m", "clew.gate",
             "--samplesheet", str(ROOT / "clew" / "data" / "donors.csv"), "--block-on", "X"],
            capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
        self.assertNotEqual(env_free.returncode, 0)
        self.assertIn("gate that cannot", env_free.stderr)

    def test_an_unreachable_log_stops_the_build(self):
        # The one that matters most. An unreachable log is not an absence of
        # prohibitions, and a green build here would be a lie.
        #
        # Two ways of not reaching it — no driver installed, or nothing
        # listening — and the assertion is on the property both must have
        # rather than on either message. Pinning one string would have let
        # the other path regress to exit 0 unnoticed.
        result = self.run_gate(
            "--samplesheet", str(ROOT / "clew" / "data" / "donors.csv"), "--block-on", "X",
            "--dsn", "postgresql://nobody@127.0.0.1:1/none")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(
            any(phrase in result.stderr for phrase in
                ("not the same as a clean one", "needs psycopg")),
            f"stopped, but for an unclear reason: {result.stderr!r}")

    def test_no_way_of_failing_to_check_ever_exits_zero(self):
        # The property, stated once over every failure mode: a green build
        # must mean "checked and permitted", never "could not check".
        for args in (
            ("--block-on", "X", "--dsn", "postgresql://nobody@127.0.0.1:1/none"),
            ("--dsn", "postgresql://nowhere/none"),
            ("--block-on", "X", "--clear-on", "X",
             "--dsn", "postgresql://nowhere/none"),
            ("--block-on", "X", "--dsn", "not-a-connection-string"),
        ):
            result = self.run_gate("--samplesheet", str(ROOT / "clew" / "data" / "donors.csv"),
                                   *args)
            self.assertNotEqual(result.returncode, 0, f"{args} exited 0")

    def test_a_type_that_both_blocks_and_clears_is_refused(self):
        result = self.run_gate(
            "--samplesheet", str(ROOT / "clew" / "data" / "donors.csv"),
            "--block-on", "X", "--clear-on", "X",
            "--dsn", "postgresql://nowhere/none")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("both blocking and clearing", result.stderr)

    def test_the_shipped_policy_template_is_valid_json_and_names_types(self):
        template = json.loads((ROOT / "clew" / "data" / "gate-policy.example.json").read_text())
        self.assertTrue(template["blocking"])
        self.assertFalse(set(template["blocking"]) & set(template["clearing"]))


if __name__ == "__main__":
    unittest.main()
