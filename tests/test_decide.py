"""A person's decision on a held incident: what it must carry, and what it may not do."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.intake import decide

HELD = {"outcome": "held", "choice": "none", "confidence": 0.49,
        "reason": "none at 0.49, below the dismissal bar 0.9", "request_sha256": "a" * 64,
        "settings": {"version": "v1", "hash": "b" * 64},
        "incident": {"sha256": "c" * 64, "text": "n"},
        "options": {"toolkit": "container:toolkit", "U3": "unit:U3"}}


class TestDecide(unittest.TestCase):
    def test_asking_records_who_what_and_what_they_were_shown(self):
        made = decide.decide(HELD, "qa.lead@example.org", ask="unit:U3", reason="confirmed by phone",
                             at="2026-10-03T16:00:00+00:00")
        self.assertEqual((made["decision"], made["trigger"], made["actor"]),
                         ("ask", "unit:U3", "qa.lead@example.org"))
        self.assertEqual(made["incident"], "c" * 64)
        self.assertEqual(made["triage"]["confidence"], 0.49)
        self.assertEqual(made["decided_at"], "2026-10-03T16:00:00+00:00")

    def test_dismissing_carries_no_trigger(self):
        made = decide.decide(HELD, "qa", dismiss=True)
        self.assertEqual((made["decision"], made["trigger"]), ("dismiss", None))

    def refused(self, record=HELD, **kwargs):
        with self.assertRaises(decide.Refused) as stopped:
            decide.decide(record, **kwargs)
        return str(stopped.exception)

    def test_only_what_triage_offered_can_be_asked(self):
        self.assertIn("not a trigger triage offered", self.refused(actor="qa", ask="unit:U9"))

    def test_a_decision_has_a_name_and_exactly_one_choice(self):
        self.assertIn("needs the name", self.refused(actor="", ask="unit:U3"))
        self.assertIn("one of", self.refused(actor="qa"))
        self.assertIn("one of", self.refused(actor="qa", ask="unit:U3", dismiss=True))

    def test_a_incident_that_was_not_held_takes_no_decision(self):
        for outcome in ("ask", "dismissed"):
            self.assertIn("not held", self.refused(dict(HELD, outcome=outcome), actor="qa", dismiss=True))

    def test_the_event_goes_in_under_the_person(self):
        event = decide.decided(decide.decide(HELD, "qa.lead@example.org", dismiss=True))
        self.assertEqual((event["event_type"], event["actor"], event["subject"]),
                         ("IncidentDecided", "qa.lead@example.org", "c" * 64))
        self.assertNotIn("actor", event["body"])


if __name__ == "__main__":
    unittest.main()
