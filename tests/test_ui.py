"""
The local page and its server, with no agent and no model.

The agent launch is replaced by a function that writes the files an agent
run leaves, so what is tested is the server's own work: who may call it,
what it reads from a folder, and that a step counts as done only when its
file exists.
"""

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clew.ui import app as ui
from clew.ui.page import PAGE

RUNS = ROOT / "tests" / "fixtures" / "horus_run"

RECORD = {"outcome": "ask", "choice": "shell", "confidence": 0.9, "trigger": "container:shell",
          "reason": "0.90, at or above the asking bar 0.6", "backend": "jev",
          "model": "jev-1.13.0", "options": {"shell": "container:shell"},
          "notice": {"sha256": "0" * 64, "text": "n"}}
PLAN = {"trigger": "container:shell", "policy_version": "v1", "tasks_affected": 1,
        "tasks_total": 4, "actions": {"REGENERATE": 1},
        "plan": [{"process": "analyse", "action": "REGENERATE", "rule": "R8"}]}


def leaves(outcome="ask", plan=True, review=None):
    """A stand-in for the agent: it writes what a run with this outcome leaves behind."""
    def launch(job, home, roots):
        out = job.folder / "out" / ui.NOTICE
        out.mkdir(parents=True)
        (out / "triage.json").write_text(json.dumps(dict(RECORD, outcome=outcome)))
        if plan and outcome == "ask":
            (out / "plan.json").write_text(json.dumps(PLAN))
        if review:
            (out / "review.json").write_text(json.dumps(review))
        job.turns, job.cost = 12, 0.03
    return launch


class Served(unittest.TestCase):
    launch = staticmethod(leaves())

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.server, self.link = ui.serve(self.tmp.name, port=0, token="t0ken")
        self.server.app.launch = self.launch
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, path, body=None, token="t0ken", host=None, method="POST"):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", method=method,
            data=None if method == "GET" else json.dumps(body or {}).encode(),
            headers={"X-Clew-Token": token, "Content-Type": "application/json"})
        if host:
            request.add_header("Host", host)
        try:
            with urllib.request.urlopen(request, timeout=10) as reply:
                raw = reply.read()
                return reply.status, raw if method == "GET" else json.loads(raw)
        except urllib.error.HTTPError as bad:
            return bad.code, json.loads(bad.read())

    def finished(self, name):
        for _ in range(200):
            status, job = self.call("/api/job", {"job": name})
            if job["state"] != "running":
                return job
            threading.Event().wait(0.02)
        self.fail("the job never finished")

    def start(self, **over):
        body = dict({"path": str(RUNS), "run": "horus_run", "notice": "shell is broken"}, **over)
        return self.call("/api/run", body)


class TestWhoMayCall(Served):
    def test_the_page_is_served_and_fetches_nothing_from_outside(self):
        status, page = self.call("/", method="GET")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>Clew</title>", page)
        for outside in ("http://", "https://", "//cdn", "@import"):
            self.assertNotIn(outside, PAGE)

    def test_a_call_without_the_token_is_refused(self):
        self.assertEqual(self.call("/api/state", token="")[0], 403)
        self.assertEqual(self.call("/api/state", token="wrong")[0], 403)

    def test_a_call_addressed_to_another_host_is_refused(self):
        self.assertEqual(self.call("/api/state", host="evil.example:80")[0], 403)

    def test_the_link_carries_the_token(self):
        self.assertTrue(self.link.endswith("/?t=t0ken"))


class TestFolders(Served):
    def test_a_plain_folder_lists_its_subfolders_and_no_runs(self):
        (Path(self.tmp.name) / "alpha").mkdir()
        (Path(self.tmp.name) / ".hidden").mkdir()
        status, found = self.call("/api/browse", {"path": self.tmp.name})
        self.assertEqual((status, found["engine"], found["runs"]), (200, None, []))
        self.assertIn("alpha", found["folders"])
        self.assertNotIn(".hidden", found["folders"])

    def test_a_run_folder_names_its_engine_and_runs(self):
        status, found = self.call("/api/browse", {"path": str(RUNS)})
        self.assertEqual(found["engine"], "horus")
        self.assertEqual([r["name"] for r in found["runs"]], ["horus_run"])

    def test_a_folder_that_cannot_be_read_is_said(self):
        status, found = self.call("/api/browse", {"path": "/no/such/folder/anywhere"})
        self.assertEqual(status, 400)
        self.assertIn("cannot read", found["error"])

    def test_state_says_which_engines_can_be_picked_by_folder(self):
        engines = {e["name"]: e["folder"] for e in self.call("/api/state")[1]["engines"]}
        self.assertTrue(engines["horus"] and engines["nextflow"])
        self.assertFalse(engines["cromwell"])


class TestRun(Served):
    def test_a_run_extracts_and_reports_each_step_from_its_file(self):
        status, started = self.start()
        self.assertEqual(status, 200)
        job = self.finished(started["job"])
        self.assertEqual(job["state"], "finished")
        self.assertEqual(job["extract"]["tasks"], 4)
        states = {s["key"]: s["state"] for s in job["steps"]}
        # No bundle was written, so evidence is not done whatever the agent said.
        self.assertEqual(states, {"extract": "done", "triage": "done", "impact": "done",
                                  "evidence": "skipped", "act": "skipped"})
        self.assertEqual(job["plan"]["actions"], {"REGENERATE": 1})
        self.assertEqual((job["turns"], job["cost"]), (12, 0.03))
        folder = Path(job["folder"])
        self.assertTrue((folder / "graph.json").is_file())
        self.assertEqual((folder / "inbox" / "notice.txt").read_text(), "shell is broken\n")

    def test_an_empty_notice_or_an_unknown_run_is_refused(self):
        self.assertEqual(self.start(notice="  ")[0], 400)
        self.assertEqual(self.start(run="absent")[0], 400)
        self.assertEqual(self.start(path=self.tmp.name)[0], 400)
        self.assertEqual(self.start(work_root="/no/such/folder")[0], 400)


class TestHeld(Served):
    launch = staticmethod(leaves("held", review={
        "verdict": "dismiss", "trigger": None, "reason": "another tool",
        "status": "recommendation, not a decision", "recommended_by": "clew"}))

    def test_a_held_notice_shows_a_recommendation_and_no_plan(self):
        job = self.finished(self.start()[1]["job"])
        states = {s["key"]: s["state"] for s in job["steps"]}
        self.assertEqual(states["impact"], "skipped")
        self.assertEqual(states["review"], "done")
        self.assertIsNone(job["plan"])
        self.assertEqual(job["review"]["verdict"], "dismiss")


class TestFailure(Served):
    @staticmethod
    def launch(job, home, roots):
        raise ui.Refused("Mainsheet is not installed in this environment")

    def test_a_failed_launch_ends_in_a_state_the_page_can_show(self):
        job = self.finished(self.start()[1]["job"])
        self.assertEqual(job["state"], "failed")
        self.assertIn("Mainsheet is not installed", job["error"])
        self.assertEqual({s["key"]: s["state"] for s in job["steps"]}["triage"], "failed")


class TestOneAtATime(Served):
    gate = threading.Event()

    @classmethod
    def launch(cls, job, home, roots):
        cls.gate.wait(5)

    def test_a_second_run_waits_for_the_first(self):
        first = self.start()[1]["job"]
        self.assertEqual(self.start()[0], 409)
        self.gate.set()
        self.finished(first)


if __name__ == "__main__":
    unittest.main()


class TestPicking(unittest.TestCase):
    """The system dialog is never opened here: only which command would run, and what comes back."""

    def test_each_system_gets_its_own_dialog(self):
        from unittest import mock
        with mock.patch.object(ui.sys, "platform", "darwin"):
            command = ui.picker("/some/where")
            self.assertEqual(command[0], "osascript")
            # The folder is an argument after --, so no path can become script text.
            self.assertEqual(command[-2:], ["--", "/some/where"])
            self.assertNotIn("/some/where", " ".join(command[:-1]))
        with mock.patch.object(ui.sys, "platform", "win32"):
            self.assertEqual(ui.picker("C:/x")[0], "powershell")
        with mock.patch.object(ui.sys, "platform", "linux"), \
                mock.patch.object(ui.shutil, "which", lambda name: name == "zenity"):
            self.assertEqual(ui.picker("/x")[0], "zenity")

    def test_no_dialog_at_all_is_said(self):
        from unittest import mock
        with mock.patch.object(ui, "picker", lambda start: None):
            self.assertEqual(ui.pick("/"), {"available": False, "path": None})

    def test_a_cancelled_dialog_gives_no_path(self):
        from unittest import mock
        done = mock.Mock(stdout="\n")
        with mock.patch.object(ui, "picker", lambda start: ["x"]), \
                mock.patch.object(ui.subprocess, "run", return_value=done):
            self.assertEqual(ui.pick("/"), {"available": True, "path": None})

    def test_a_chosen_folder_comes_back(self):
        from unittest import mock
        done = mock.Mock(stdout=str(RUNS) + "/\n")
        with mock.patch.object(ui, "picker", lambda start: ["x"]), \
                mock.patch.object(ui.subprocess, "run", return_value=done):
            self.assertEqual(Path(ui.pick("/")["path"]), RUNS)


class TestNearby(unittest.TestCase):
    def test_a_folder_inside_a_run_folder_points_up_to_it(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            launch = Path(tmp) / "launch"
            shutil.copytree(RUNS, launch)
            (launch / "work").mkdir()
            found = ui.browse(str(launch / "work"))
        self.assertIsNone(found["engine"])
        self.assertEqual((found["nearby"]["engine"], found["nearby"]["name"]), ("horus", "launch"))


class TestAdapters(Served):
    def test_state_lists_each_adapter_with_the_files_its_kinds_need(self):
        listed = {a["name"]: a for a in self.call("/api/state")[1]["adapters"]}
        self.assertEqual(listed["vina-docking"]["kinds"], ["ligand"])
        self.assertEqual([f["flag"] for f in listed["vina-docking"]["flags"]], ["--ligands"])

    def test_a_run_passes_the_adapter_and_its_file_to_the_agent(self):
        seen = {}

        def launch(job, home, roots):
            seen.update(roots)
            leaves()(job, home, roots)
        self.server.app.launch = launch
        library = ROOT / "tests" / "fixtures" / "pantheon_vina" / "ligands.smi"
        started = self.start(pipeline="vina-docking", adapter_args={"--ligands": str(library)})
        self.finished(started[1]["job"])
        self.assertEqual(seen["CLEW_PIPELINE"], "vina-docking")
        self.assertEqual(seen["CLEW_ADAPTER_ARGS"], f"--ligands {library}")

    def test_an_unknown_adapter_or_setting_or_a_missing_file_is_refused(self):
        self.assertEqual(self.start(pipeline="absent")[0], 400)
        self.assertEqual(self.start(pipeline="vina-docking",
                                    adapter_args={"--other": __file__})[0], 400)
        self.assertEqual(self.start(pipeline="vina-docking",
                                    adapter_args={"--ligands": "/no/such/file"})[0], 400)


class TestDecision(Served):
    """A held notice waits for a person. Their choice is recorded and, when it asks, carried on."""
    launch = staticmethod(leaves("held"))

    def setUp(self):
        super().setUp()
        import os
        from unittest import mock
        clean = {k: v for k, v in os.environ.items() if k not in ("CLEW_DSN", "TYPESAFE_API_KEY")}
        patched = mock.patch.dict(os.environ, dict(clean, PYTHONPATH=str(ROOT)), clear=True)
        patched.start()
        self.addCleanup(patched.stop)
        self.job = self.finished(self.start()[1]["job"])

    def decide(self, **over):
        body = dict({"job": self.job["job"], "action": "ask", "actor": "qa.lead@example.org",
                     "trigger": "container:shell", "reason": "the shell step"}, **over)
        return self.call("/api/decide", body)

    def test_a_held_notice_is_open_and_offers_what_triage_offered(self):
        states = {s["key"]: s["state"] for s in self.job["steps"]}
        self.assertEqual(states["decision"], "open")
        self.assertEqual(states["impact"], "skipped")
        self.assertEqual(self.job["choices"]["triggers"], ["container:shell"])
        self.assertEqual(self.job["state"], "finished")

    def test_asking_a_trigger_records_the_person_and_carries_on_to_a_verified_bundle(self):
        status, job = self.decide()
        self.assertEqual(status, 200)
        job = self.finished(job["job"])
        self.assertEqual(job["state"], "finished", job["error"])
        self.assertEqual((job["decision"]["actor"], job["decision"]["trigger"]),
                         ("qa.lead@example.org", "container:shell"))
        states = {s["key"]: s["state"] for s in job["steps"]}
        self.assertEqual((states["decision"], states["impact"], states["evidence"]),
                         ("done", "done", "done"))
        self.assertTrue(job["verified"])
        self.assertIsNone(job["choices"])

    def test_dismissing_ends_it_with_no_plan(self):
        status, job = self.decide(action="dismiss")
        self.assertEqual((status, job["state"], job["decision"]["decision"]),
                         (200, "finished", "dismiss"))
        states = {s["key"]: s for s in job["steps"]}
        self.assertEqual(states["impact"]["state"], "skipped")
        self.assertIn("a person dismissed", states["impact"]["detail"])

    def test_a_decision_needs_a_name_and_an_offered_trigger(self):
        self.assertEqual(self.decide(actor="  ")[0], 400)
        self.assertEqual(self.decide(trigger="container:invented")[0], 400)
        self.assertEqual(self.decide(action="approve")[0], 400)

    def test_a_second_decision_is_refused(self):
        self.finished(self.decide()[1]["job"])
        self.assertEqual(self.decide(action="dismiss")[0], 400)
