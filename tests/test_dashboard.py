"""
The HTML view.

Three properties are load-bearing here and each has a test.

It must be SELF-CONTAINED: an auditor opens it from a USB stick on a machine
with no network, and a page that fetches anything is a page that renders
differently, or not at all, depending on where it is opened.

It must be REPRODUCIBLE: no clock, so regenerating from unchanged bundles
gives an identical file. Two auditors comparing pages should be comparing
evidence, not diffing timestamps.

It must ESCAPE EVERYTHING. Subjects, actors, triggers and event types are
strings the recording organisation chose, and they go straight into markup.
An identifier containing a tag would otherwise break the page at best and
inject script at worst — in a document whose entire purpose is being trusted.
"""

import json
import re
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew import dashboard
from clew import mcp_server
from clew.core import evidence
from clew.core import eventlog
from clew.core import policy as policy_module

T = "2026-01-01T00:00:00+00:00"


def log_entry(seq, prev_hash, subject="s1", event_type="Withdrawn",
              actor="tester", body=None):
    fields = {
        "seq": seq, "effective_from": "2026-03-01T00:00:00+00:00",
        "recorded_at": T, "actor": actor, "event_type": event_type,
        "subject": subject, "body": eventlog.canonical(body or {}),
        "prev_hash": prev_hash,
    }
    fields["hash"] = eventlog.event_hash(fields)
    return fields


def a_plan(trigger="withdrawal of s1", undetermined=False):
    if undetermined:
        decision = policy_module.decide("REGENERABLE", storage=None)
        storage = None
    else:
        decision = policy_module.decide("REGENERABLE", storage="WRITABLE")
        storage = "WRITABLE"
    return {
        "clew_plan_version": 1, "trigger": trigger,
        **policy_module.identify(policy_module.DEFAULT),
        "tasks_total": 10, "tasks_affected": 1, "entry_tasks": ["t0"],
        "plan": [{
            "task": "t1", "process": "P", "name": "t1",
            "action": decision["action"], "rule": decision["rule"],
            "because": decision["because"], "possible": decision.get("possible"),
            "contribution": "REGENERABLE", "storage": storage,
            "exclusive": False, "terminal": False, "reason": "test",
            "evidence_path": ["t0", "t1"],
        }],
        "caveats": ["a stated limit"],
    }


class DashboardTestCase(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = holder.name

    def seal(self, name="b1", events=None, plan=None):
        events = events if events is not None else self.chain(2)
        head = ({"seq": events[-1]["seq"], "hash": events[-1]["hash"]}
                if events else {"seq": 0, "hash": eventlog.GENESIS})
        evidence.build(Path(self.tmp) / name,
                       {"plan.json": plan or a_plan(),
                        "policy.json": policy_module.DEFAULT,
                        "events.json": events, "inputs.json": {}},
                       log_head=head, coverage=["a sealed limit"])
        return Path(self.tmp) / name

    def chain(self, n, **kwargs):
        entries, prev = [], eventlog.GENESIS
        for i in range(1, n + 1):
            entries.append(log_entry(i, prev, **kwargs))
            prev = entries[-1]["hash"]
        return entries

    def page(self):
        store = mcp_server.load_store(self.tmp)
        return dashboard.render(store, self.tmp)


class TestWellFormed(DashboardTestCase):
    def test_every_tag_is_closed(self):
        self.seal()

        class Checker(HTMLParser):
            VOID = {"meta", "br", "link", "img", "input", "hr"}

            def __init__(self):
                super().__init__()
                self.stack, self.mismatched = [], []

            def handle_starttag(self, tag, attrs):
                if tag not in self.VOID:
                    self.stack.append(tag)

            def handle_endtag(self, tag):
                if self.stack and self.stack[-1] == tag:
                    self.stack.pop()
                elif tag in self.stack:
                    self.mismatched.append(tag)

        checker = Checker()
        checker.feed(self.page())
        self.assertEqual(checker.stack, [])
        self.assertEqual(checker.mismatched, [])

    def test_it_is_self_contained(self):
        # No network, no scripts. It has to render identically on a machine
        # with nothing.
        self.seal()
        page = self.page()
        self.assertNotIn("<script", page)
        self.assertNotIn("http://", page)
        self.assertNotIn("https://", page)
        self.assertNotIn("src=", page)


class TestReproducible(DashboardTestCase):
    def test_regenerating_gives_an_identical_page(self):
        self.seal()
        self.assertEqual(self.page(), self.page())

    def test_no_timestamp_leaks_into_the_page(self):
        self.seal()
        page = self.page()
        # Any ISO date in the page must have come from the evidence, not from
        # a clock read while rendering.
        for found in re.findall(r"\d{4}-\d{2}-\d{2}", page):
            self.assertIn(found, ("2026-03-01", "2026-01-01"), found)


class TestEscaping(DashboardTestCase):
    """
    Subjects and actors are strings the recording organisation chose. They go
    straight into markup, in a document whose purpose is being trusted.
    """

    HOSTILE = '<script>alert("x")</script>'

    def test_a_hostile_subject_is_escaped(self):
        self.seal(events=self.chain(2, subject=self.HOSTILE))
        page = self.page()
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)

    def test_a_hostile_actor_is_escaped(self):
        self.seal(events=self.chain(2, actor=self.HOSTILE))
        self.assertNotIn("<script>alert", self.page())

    def test_a_hostile_trigger_is_escaped(self):
        self.seal(plan=a_plan(trigger=self.HOSTILE))
        self.assertNotIn("<script>alert", self.page())

    def test_a_hostile_event_type_is_escaped(self):
        self.seal(events=self.chain(2, event_type=self.HOSTILE))
        self.assertNotIn("<script>alert", self.page())


class TestGapsAreProminent(DashboardTestCase):
    def test_the_unknowns_section_comes_before_the_findings(self):
        # A record that renders its gaps below the fold manufactures the
        # impression of a clean bill of health.
        self.seal()
        page = self.page()
        self.assertLess(page.index("What is not known"), page.index("Findings"))

    def test_withheld_verdicts_are_counted(self):
        self.seal(plan=a_plan(undetermined=True))
        page = self.page()
        self.assertIn("verdicts withheld", page)
        self.assertIn("UNDETERMINED", page)

    def test_sealed_coverage_notes_are_shown(self):
        self.seal()
        self.assertIn("a sealed limit", self.page())

    def test_the_page_says_it_is_not_the_record(self):
        self.seal()
        self.assertIn("This page is not the record", self.page())

    def test_the_page_states_what_clew_does_not_claim(self):
        self.seal()
        page = self.page()
        self.assertIn("not an attester", page)
        self.assertIn("proof of non-use, not proof of destruction", page)


class TestCrossLogWarning(DashboardTestCase):
    def test_bundles_from_different_logs_produce_a_stop_panel(self):
        self.seal(name="log-a", events=self.chain(2, subject="s1"))
        self.seal(name="log-b", events=self.chain(2, subject="s2"))
        page = self.page()
        self.assertIn("sealed from different logs", page)
        # A stop panel, not a warn one: this is not a caveat, it is a reason
        # to distrust the combined timeline entirely.
        self.assertIn("panel stop", page)
        self.assertLess(page.index("panel stop"), page.index("What is not known"))


class TestCli(DashboardTestCase):
    def test_an_empty_directory_is_refused_rather_than_rendered(self):
        import subprocess
        empty = Path(self.tmp) / "empty"
        empty.mkdir()
        result = subprocess.run(
            [sys.executable, "-m", "clew.dashboard",
             "--bundles", str(empty), "--out", str(Path(self.tmp) / "x.html")],
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no readable bundle", result.stderr)


class TestSurfacesShareOneStore(unittest.TestCase):
    """
    The dashboard and the MCP server must read evidence the same way, or one
    day they disagree and nobody can say which is wrong. The shared layer is
    core/bundlestore.py, and neither surface may import the other.
    """

    def test_neither_surface_imports_the_other(self):
        root = Path(__file__).resolve().parent.parent
        dash = (root / "clew" / "dashboard.py").read_text()
        served = (root / "clew" / "mcp_server.py").read_text()
        self.assertNotRegex(
            dash, re.compile(r"^\s*(from|import)\s+mcp_server", re.M),
            "dashboard.py imports mcp_server.py")
        self.assertNotRegex(
            served, re.compile(r"^\s*(from|import)\s+dashboard", re.M),
            "mcp_server.py imports dashboard.py")
        for name, text in (("dashboard.py", dash), ("mcp_server.py", served)):
            self.assertIn("bundlestore", text,
                          f"{name} no longer reads through core/bundlestore")

if __name__ == "__main__":
    unittest.main()
