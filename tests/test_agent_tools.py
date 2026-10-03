"""
Clew's agent tools, without an agent.

Every tool is a plain function over a directory, so the checks that matter
run here with no model and no agent runtime: the trigger comes from the
record and not from the caller, a held notice takes a recommendation and
nothing more, and an id cannot name a file outside the inbox.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clew.agent import mainsheet as tools

GRAPH = {
    "tasks": {
        "a": {"hash": "a", "container": "registry.example/lib/toolkit:2.1",
              "process": "PREP", "script": "prep", "labels": {}},
        "b": {"hash": "b", "container": "other-1.0", "process": "JOIN",
              "script": "join", "labels": {}},
    },
    "edges": [
        {"consumer": "a", "producer": "EXTERNAL", "filename": "/in/reference.dat"},
        {"consumer": "b", "producer": "a", "filename": "mid.dat"},
    ],
    "outputs": {"a": ["mid.dat"], "b": ["out.dat"]},
}


class AgentDirectory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        (self.base / "inbox").mkdir()
        (self.base / "graph.json").write_text(json.dumps(GRAPH))
        (self.base / "inbox" / "named.txt").write_text("toolkit 2.1 truncates indexes\n")
        (self.base / "inbox" / "vague.txt").write_text("the preparation step is wrong\n")
        # Name matching, no database and no key, so nothing here leaves the machine.
        env = {k: v for k, v in os.environ.items()
               if k not in ("TYPESAFE_API_KEY", "CLEW_DSN", "CLEW_PIPELINE",
                            "CLEW_WORK_ROOT", "CLEW_RESULTS")}
        env.update(CLEW_AGENT_DIR=str(self.base), CLEW_TRIAGE_BACKEND="name",
                   PYTHONPATH=str(ROOT))
        patched = mock.patch.dict(os.environ, env, clear=True)
        patched.start()
        self.addCleanup(patched.stop)


class TestWorkflow(AgentDirectory):
    def test_inbox_lists_ids_and_states_without_text(self):
        listed = tools.inbox(self.base)
        self.assertEqual([n["notice"] for n in listed], ["named", "vague"])
        self.assertEqual({n["state"] for n in listed}, {"waiting"})
        self.assertNotIn("toolkit", json.dumps(listed))

    def test_a_named_notice_runs_through_to_a_verified_bundle(self):
        sorted_as = tools.triage(self.base, "named")
        self.assertEqual(sorted_as["outcome"], "ask")
        self.assertEqual(sorted_as["trigger"], "container:toolkit")

        plan = tools.impact(self.base, "named")
        self.assertEqual(plan["trigger"], "container:toolkit")
        self.assertEqual(plan["tasks_affected"], 2)

        sealed = tools.seal(self.base, "named")
        self.assertTrue(sealed["verified"])
        inputs = json.loads((Path(sealed["bundle"]) / "inputs.json").read_text())
        self.assertIn("triage.json", json.dumps(inputs))
        self.assertEqual(tools.inbox(self.base)[0]["state"], "ask")

    def test_options_come_from_the_graph(self):
        offered = tools.options(self.base)
        self.assertLessEqual({"toolkit", "other", "reference.dat", "none"}, set(offered))


class TestGuards(AgentDirectory):
    def refused(self, call, *args):
        with self.assertRaises(tools.ToolError) as stopped:
            call(self.base, *args)
        return str(stopped.exception)

    def test_impact_before_triage_is_refused(self):
        self.assertIn("not been triaged", self.refused(tools.impact, "named"))

    def test_a_held_notice_gets_no_plan(self):
        self.assertEqual(tools.triage(self.base, "vague")["outcome"], "held")
        self.assertIn("no person has decided", self.refused(tools.impact, "vague"))

    def test_seal_before_impact_is_refused(self):
        tools.triage(self.base, "named")
        self.assertIn("no plan", self.refused(tools.seal, "named"))

    def test_an_id_cannot_leave_the_inbox(self):
        for notice in ("../graph", "a/b", "", "named.txt"):
            self.assertIn("not a notice id", self.refused(tools.triage, notice))

    def test_an_unknown_id_is_said(self):
        self.assertIn("no notice", self.refused(tools.triage, "absent"))


class TestRecommendation(AgentDirectory):
    def setUp(self):
        super().setUp()
        tools.triage(self.base, "vague")
        tools.triage(self.base, "named")

    def refused(self, *args):
        with self.assertRaises(tools.ToolError) as stopped:
            tools.recommend(self.base, *args)
        return str(stopped.exception)

    def test_a_recommendation_is_written_and_decides_nothing(self):
        done = tools.recommend(self.base, "vague", "ask", "toolkit",
                               "PREP runs in the toolkit image", "clew")
        review = json.loads(Path(done["written"]).read_text())
        self.assertEqual(review["trigger"], "container:toolkit")
        self.assertEqual(review["status"], "recommendation, not a decision")
        self.assertEqual(review["recommended_by"], "clew")
        # The record still says held, and no plan appeared.
        self.assertEqual(tools.record_of(self.base, "vague")["outcome"], "held")
        self.assertFalse((self.base / "out" / "vague" / "plan.json").exists())

    def test_only_a_held_notice_takes_one(self):
        self.assertIn("not held", self.refused("named", "dismiss", "", "why", "clew"))

    def test_an_option_the_run_does_not_have_is_refused(self):
        self.assertIn("not one of this run's options",
                      self.refused("vague", "ask", "invented", "why", "clew"))

    def test_a_verdict_outside_the_three_is_refused(self):
        self.assertIn("verdict must be", self.refused("vague", "approve", "", "why", "clew"))

    def test_a_reason_is_required(self):
        self.assertIn("needs its reason", self.refused("vague", "person", "", "  ", "clew"))


class TestDecision(AgentDirectory):
    """A held notice moves only on a person's decision, and only to a trigger triage offered."""

    def setUp(self):
        super().setUp()
        tools.triage(self.base, "vague")

    def test_asking_a_trigger_leads_to_a_plan_and_a_bundle_that_holds_the_decision(self):
        decision = tools.decide(self.base, "vague", "qa.lead@example.org",
                                ask="container:toolkit", reason="PREP is the toolkit step")
        self.assertEqual((decision["decision"], decision["actor"]), ("ask", "qa.lead@example.org"))
        plan = tools.impact(self.base, "vague")
        self.assertEqual(plan["trigger"], "container:toolkit")
        sealed = tools.seal(self.base, "vague")
        self.assertTrue(sealed["verified"])
        inputs = (Path(sealed["bundle"]) / "inputs.json").read_text()
        self.assertIn("decision.json", inputs)
        self.assertIn("a person decided: ask", tools.inbox(self.base)[1]["state"])

    def test_a_dismissed_notice_gets_no_plan(self):
        tools.decide(self.base, "vague", "qa.lead@example.org")
        with self.assertRaises(tools.ToolError) as stopped:
            tools.impact(self.base, "vague")
        self.assertIn("no person has decided to ask", str(stopped.exception))

    def refused(self, *args, **kwargs):
        with self.assertRaises(tools.ToolError) as stopped:
            tools.decide(self.base, *args, **kwargs)
        return str(stopped.exception)

    def test_a_trigger_that_was_not_offered_is_refused(self):
        self.assertIn("not a trigger triage offered",
                      self.refused("vague", "qa", ask="container:invented"))

    def test_a_decision_needs_a_name_and_is_not_rewritten(self):
        self.assertIn("needs the name", self.refused("vague", " ", ask="container:toolkit"))
        tools.decide(self.base, "vague", "qa", ask="container:toolkit")
        self.assertIn("is not rewritten", self.refused("vague", "qa"))

    def test_only_a_held_notice_takes_a_decision(self):
        tools.triage(self.base, "named")
        self.assertIn("not held", self.refused("named", "qa"))


class TestPreflight(AgentDirectory):
    def test_it_passes_with_a_graph_a_notice_and_a_working_clew(self):
        tools.preflight("clew")

    def test_an_empty_inbox_stops_the_run(self):
        for path in (self.base / "inbox").glob("*.txt"):
            path.unlink()
        with self.assertRaises(SystemExit) as stopped:
            tools.preflight("clew")
        self.assertIn("no notices", str(stopped.exception))

    def test_a_missing_directory_setting_stops_the_run(self):
        with mock.patch.dict(os.environ):
            del os.environ["CLEW_AGENT_DIR"]
            with self.assertRaises(SystemExit):
                tools.preflight("clew")


if __name__ == "__main__":
    unittest.main()


class TestPipelineKinds(unittest.TestCase):
    """A notice about a domain's own kind: the adapter and its file reach both commands."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        (self.base / "inbox").mkdir()
        fixture = ROOT / "tests" / "fixtures" / "pantheon_vina"
        from clew.extract.runs import Runs
        (self.base / "graph.json").write_text(json.dumps(Runs(str(fixture)).load()))
        (self.base / "inbox" / "withdrawn.txt").write_text("caffeine was withdrawn from the library\n")
        env = {k: v for k, v in os.environ.items() if k not in ("TYPESAFE_API_KEY", "CLEW_DSN")}
        env.update(CLEW_AGENT_DIR=str(self.base), CLEW_TRIAGE_BACKEND="name", PYTHONPATH=str(ROOT),
                   CLEW_PIPELINE="vina-docking",
                   CLEW_ADAPTER_ARGS=f"--ligands {fixture / 'ligands.smi'}")
        patched = mock.patch.dict(os.environ, env, clear=True)
        patched.start()
        self.addCleanup(patched.stop)

    def test_a_domain_notice_is_triaged_and_planned_under_the_same_adapter(self):
        sorted_as = tools.triage(self.base, "withdrawn")
        self.assertEqual((sorted_as["outcome"], sorted_as["trigger"]), ("ask", "ligand:caffeine"))
        plan = tools.impact(self.base, "withdrawn")
        # Impact words a removal in its own way; the subject is what must carry over.
        self.assertIn("caffeine", plan["trigger"])
        self.assertGreater(plan["tasks_affected"], 0)
        self.assertIn("caffeine", tools.options(self.base))
