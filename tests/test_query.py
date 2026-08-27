"""
The query surface: what an auditor's question resolves to.

One rule dominates these tests. An answer that asserts something must carry
citations, and there must be no way to get one that does not — because the
consumer on the other side is a language model whose fluent, confident prose
an auditor cannot distinguish from an accurate one by reading it. Citations
are what make a bad paraphrase checkable instead of persuasive.

The other half is coverage. An empty result must never be able to read as a
clean one.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.core import policy as policy_module
from clew.core import query

T = "2026-01-01T00:00:00+00:00"


def entry(seq, subject, event_type, effective_from, body=None,
          actor="tester", as_text=False):
    payload = body or {}
    return {
        "seq": seq, "subject": subject, "event_type": event_type,
        "effective_from": effective_from, "recorded_at": T, "actor": actor,
        "hash": f"{seq:064d}", "prev_hash": "",
        "body": json.dumps(payload, sort_keys=True) if as_text else payload,
    }


def plan_with(items, trigger="test:trigger"):
    return {
        "clew_plan_version": 1, "trigger": trigger,
        **policy_module.identify(policy_module.DEFAULT),
        "tasks_total": 10, "tasks_affected": len(items),
        "entry_tasks": ["t0"], "plan": items, "caveats": ["a stated limit"],
    }


def item(task, action="REGENERATE", rule="R7", **overrides):
    base = {
        "task": task, "process": "P", "name": task, "action": action,
        "rule": rule, "because": "because", "contribution": "REGENERABLE",
        "storage": "WRITABLE", "exclusive": False, "terminal": False,
        "reason": "test", "evidence_path": ["t0", task],
    }
    base.update(overrides)
    return base


class TestCitationsAreMandatory(unittest.TestCase):
    def test_answering_without_citations_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            query.answer("q", {"a": 1}, [])
        self.assertIn("no citations", str(caught.exception))

    def test_every_assertive_query_carries_citations(self):
        entries = [entry(1, "s1", "Withdrawn", "2026-03-01")]
        for result in (
            query.subject_history(entries, "s1"),
            query.plan_summary(plan_with([item("t1")])),
            query.verdict(plan_with([item("t1")]), policy_module.DEFAULT, "t1"),
            query.unaffected(plan_with([item("t1")]), "t9"),
        ):
            self.assertTrue(result["citations"], result["question"])

    def test_a_citation_names_where_to_look(self):
        entries = [entry(7, "s1", "Withdrawn", "2026-03-01",
                         actor="registry@example.org")]
        citation = query.subject_history(entries, "s1")["citations"][0]
        self.assertEqual(citation["seq"], 7)
        self.assertEqual(citation["actor"], "registry@example.org")
        self.assertEqual(len(citation["hash"]), 64)

    def test_a_verdict_cites_the_rule_with_its_own_rationale(self):
        result = query.verdict(plan_with([item("t1")]), policy_module.DEFAULT,
                               "t1")
        rule = next(c for c in result["citations"]
                    if c["kind"] == "policy_rule")
        self.assertEqual(rule["rule"], "R7")
        self.assertEqual(rule["policy_version"], policy_module.DEFAULT["version"])
        self.assertTrue(rule["because"])


class TestEmptyIsNotClean(unittest.TestCase):
    """Every way of finding nothing must say so, not imply the opposite."""

    def test_an_unknown_subject_says_it_is_unknown(self):
        result = query.subject_history([], "nobody")
        self.assertFalse(result["result"]["known"])
        self.assertIn("not the same as nothing having happened",
                      " ".join(result["coverage"]))

    def test_a_task_absent_from_a_plan_says_what_was_searched(self):
        result = query.verdict(plan_with([item("t1")]), policy_module.DEFAULT,
                               "t9")
        self.assertFalse(result["result"]["in_plan"])
        self.assertIn("not that it is unaffected by anything",
                      " ".join(result["coverage"]))

    def test_an_unaffected_answer_states_its_scope(self):
        result = query.unaffected(plan_with([item("t1")]), "t9")
        self.assertFalse(result["result"]["affected"])
        joined = " ".join(result["coverage"])
        self.assertIn("scoped to this trigger", joined)
        self.assertIn("instrumented work only", joined)

    def test_no_recorded_policy_adoption_says_so(self):
        result = query.policy_history([])
        self.assertEqual(result["result"]["adoptions"], [])
        self.assertIn("no policy adoption was ever recorded",
                      " ".join(result["coverage"]))

    def test_an_undetermined_verdict_is_flagged_as_unanswered(self):
        undecided = item("t1", action=None, rule=None,
                         possible={"ALREADY_GONE": "R1", "REGENERATE": "R7"})
        result = query.verdict(plan_with([undecided]), policy_module.DEFAULT,
                               "t1")
        self.assertIn("unanswered, not clean", " ".join(result["coverage"]))


class TestTemporal(unittest.TestCase):
    def setUp(self):
        self.entries = [
            entry(1, "v1", query.POLICY_ADOPTED, "2025-06-01",
                  {"policy_hash": "aaa"}),
            entry(2, "v2", query.POLICY_ADOPTED, "2026-01-15",
                  {"policy_hash": "bbb"}),
        ]

    def test_the_policy_in_force_depends_on_the_date_asked_about(self):
        self.assertEqual(
            query.policy_in_force(self.entries, "2025-08-01")["result"]["version"],
            "v1")
        self.assertEqual(
            query.policy_in_force(self.entries, "2026-08-01")["result"]["version"],
            "v2")

    def test_before_any_adoption_the_log_says_nothing(self):
        result = query.policy_in_force(self.entries, "2025-01-01")
        self.assertIsNone(result["result"]["version"])
        self.assertEqual(result["citations"], [])

    def test_history_is_ordered_by_when_facts_took_effect(self):
        # Entered later, effective earlier: the history reads in world order,
        # because "what happened and when" is the question behind it.
        entries = [entry(1, "s1", "Withdrawn", "2026-06-01"),
                   entry(2, "s1", "Reinstated", "2026-01-01")]
        facts = query.subject_history(entries, "s1")["result"]["facts"]
        self.assertEqual([f["event_type"] for f in facts],
                         ["Reinstated", "Withdrawn"])

    def test_both_clocks_travel_with_every_fact(self):
        facts = query.subject_history(
            [entry(1, "s1", "Withdrawn", "2026-06-01")], "s1")["result"]["facts"]
        self.assertIn("effective_from", facts[0])
        self.assertIn("recorded_at", facts[0])


class TestBodyTolerance(unittest.TestCase):
    """
    A live log hands back parsed bodies; a sealed bundle carries the canonical
    text that was hashed. Both are correct and queries must not care.
    """

    def test_a_text_body_is_read_the_same_as_a_parsed_one(self):
        as_text = [entry(1, "v1", query.POLICY_ADOPTED, "2025-06-01",
                         {"policy_hash": "aaa"}, as_text=True)]
        parsed = [entry(1, "v1", query.POLICY_ADOPTED, "2025-06-01",
                        {"policy_hash": "aaa"})]
        self.assertEqual(
            query.policy_in_force(as_text, "2026-01-01")["result"]["policy_hash"],
            query.policy_in_force(parsed, "2026-01-01")["result"]["policy_hash"])

    def test_an_unparseable_body_does_not_crash_a_query(self):
        broken = entry(1, "v1", query.POLICY_ADOPTED, "2025-06-01")
        broken["body"] = "not json at all"
        self.assertIsNone(
            query.policy_in_force([broken], "2026-01-01")["result"]["policy_hash"])


class TestNoAdvice(unittest.TestCase):
    """
    Clew answers what it recorded. It does not say whether that was enough.

    Guards the boundary against a well-meaning future addition: the moment a
    query resolves to "compliant" or "no action needed", this stops being a
    system of record and becomes an attester.
    """

    def test_no_query_returns_a_compliance_judgement(self):
        entries = [entry(1, "s1", "Withdrawn", "2026-03-01")]
        results = [
            query.subject_history(entries, "s1"),
            query.plan_summary(plan_with([item("t1")])),
            query.verdict(plan_with([item("t1")]), policy_module.DEFAULT, "t1"),
            query.unaffected(plan_with([item("t1")]), "t9"),
            query.policy_history(entries),
        ]
        forbidden = ("compliant", "acceptable", "satisfies",
                     "no further action", "you are safe")
        for result in results:
            text = json.dumps(result).lower()
            for word in forbidden:
                self.assertNotIn(word, text, f"{result['question']} -> {word}")


if __name__ == "__main__":
    unittest.main()
