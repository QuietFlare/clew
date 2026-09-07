"""
The HTML impact report.

A rendered page is the artifact most likely to be detached from its
source and quoted later, so what it must not do matters as much as what
it shows.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.views import report

PLAN = {
    "clew_plan_version": 1,
    "trigger": "input:reference.dat",
    "tasks_affected": 3,
    "tasks_total": 8,
    "entry_tasks": ["run/a"],
    "policy_version": "v2",
    "policy_hash": "e6ba60ffe6763949106eca86f7888c3cc",
    "actions": {"REGENERATE": 2},
    "caveats": ["uninstrumented systems are unknown, never clean"],
    "plan": [
        {"task": "run/a", "process": "PREP", "target": "ssh://gpu-box",
         "contribution": "REGENERABLE", "storage": None, "action": None,
         "possible": "REGENERATE", "reason": "storage not checked",
         "exclusive": False, "terminal": False, "rule": "r1"},
        {"task": "run/b", "process": "RUN", "target": "ssh://gpu-box",
         "contribution": "REGENERABLE", "storage": "WRITABLE",
         "action": "REGENERATE", "reason": "can be re-executed",
         "exclusive": False, "terminal": False, "rule": "r1"},
        {"task": "run/c", "process": "JOIN", "target": "",
         "contribution": "IRREDUCIBLE", "storage": "WRITABLE",
         "action": "REGENERATE", "reason": "no script recorded",
         "exclusive": True, "terminal": False, "rule": "r2"},
    ],
}


class TestSelfContained(unittest.TestCase):
    """
    An auditor opens this from a USB stick on a machine with no network.
    """

    @classmethod
    def setUpClass(cls):
        cls.page = report.render(PLAN)

    def test_it_runs_no_scripts(self):
        self.assertNotIn("<script", self.page.lower())

    def test_it_fetches_nothing(self):
        self.assertNotIn("http://", self.page)
        self.assertNotIn("https://", self.page)
        self.assertNotIn("<link", self.page.lower())

    def test_the_stylesheet_is_inline(self):
        self.assertIn("<style>", self.page)


class TestReproducible(unittest.TestCase):
    def test_the_same_plan_renders_the_same_bytes(self):
        """
        No clock. Two people comparing pages should be comparing
        evidence, not diffing dates.
        """
        self.assertEqual(report.render(PLAN), report.render(PLAN))

    def test_no_date_is_stamped_into_the_page(self):
        self.assertIsNone(
            re.search(r"20\d\d-\d\d-\d\dT", report.render(PLAN)))


class TestContent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = report.render(PLAN)

    def test_the_trigger_and_reach_are_stated(self):
        self.assertIn("input:reference.dat", self.page)
        self.assertIn("3 of 8", self.page)

    def test_tasks_are_grouped_by_target(self):
        """
        The section the report exists for: forty tasks matter differently
        on a laptop and on a cluster.
        """
        self.assertIn("Where the work lands", self.page)
        self.assertIn("ssh://gpu-box", self.page)

    def test_the_target_column_shows_when_something_recorded_one(self):
        """
        Mixed is still worth showing: two of these ran somewhere named.
        """
        self.assertIn("<th>Target</th>", self.page)

    def test_every_affected_task_appears(self):
        for item in PLAN["plan"]:
            self.assertIn(item["task"], self.page)

    def test_the_reason_travels_with_the_verdict(self):
        """
        A verdict without its reason invites being quoted alone.
        """
        self.assertIn("no script recorded", self.page)

    def test_an_undetermined_task_shows_what_it_could_be(self):
        """
        `action` is None here; rendering that as blank would read as
        "nothing to do", which is the failure this guards against.
        """
        self.assertIn("REGENERATE", self.page)
        self.assertIn("not checked", self.page)


class TestHonesty(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = report.render(PLAN)

    def test_the_plans_own_caveats_are_shown(self):
        self.assertIn("uninstrumented systems are unknown", self.page)

    def test_the_cost_figures_are_labelled_relative(self):
        """
        ADR 0008: ranking is supported, absolute cost and carbon are not.
        A page that implied otherwise would be cited as if it did.
        """
        self.assertIn("relative signal", self.page)
        self.assertIn("not an absolute cost or carbon figure", self.page)

    def test_the_page_says_it_is_not_the_record(self):
        self.assertIn("this page is a view", self.page.lower())


class TestNoTargetsRecorded(unittest.TestCase):
    """
    An engine that runs a whole workflow on one machine has no host to
    report. A column of "not recorded" is noise pretending to be
    information, so the whole thing goes.
    """

    @classmethod
    def setUpClass(cls):
        plan = dict(PLAN)
        plan["plan"] = [dict(item, target="") for item in PLAN["plan"]]
        cls.page = report.render(plan)

    def test_the_section_is_gone(self):
        self.assertNotIn("Where the work lands", self.page)

    def test_the_column_is_gone(self):
        self.assertNotIn("<th>Target</th>", self.page)

    def test_the_tile_is_gone(self):
        self.assertNotIn("machines", self.page)

    def test_the_rest_of_the_report_is_unaffected(self):
        self.assertIn("What follows", self.page)
        for item in PLAN["plan"]:
            self.assertIn(item["task"], self.page)


if __name__ == "__main__":
    unittest.main()


class TestActionClasses(unittest.TestCase):
    """
    Every action the policy can return has a class of its own. One that
    falls through to the "open" class reads as an unreached verdict.
    """

    def render_with(self, action):
        item = dict(PLAN["plan"][1], action=action)
        return report.render(dict(PLAN, plan=[item]))

    def test_every_policy_action_has_a_class(self):
        from clew.graph import contribution
        for action in (contribution.PURGE, contribution.REGENERATE,
                       contribution.QUARANTINE, contribution.DESTROY,
                       contribution.NOTIFY_ONLY, contribution.ALREADY_GONE):
            self.assertIn(action, report.ACTION_KIND)

    def test_notify_only_is_settled_not_open(self):
        page = self.render_with("NOTIFY_ONLY")
        self.assertNotIn('<span class="tag unknown">NOTIFY_ONLY', page)
        self.assertIn('<span class="tag bad">NOTIFY_ONLY', page)

    def test_destroy_is_settled_not_open(self):
        page = self.render_with("DESTROY")
        self.assertNotIn('<span class="tag unknown">DESTROY', page)
