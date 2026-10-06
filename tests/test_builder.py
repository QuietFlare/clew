"""
An adapter an agent wrote: the judge, the install and the approval that lets it load.

No agent runs here. The adapter on trial is written by hand for the
docking fixture (fixtures/builder/adapter.py), and each failing variant
changes one thing about it.
The judge and `clew providers` run as their own processes, so no adapter
on trial is ever registered in the process that runs the tests.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clew.builder import adapter as builder
from clew.contracts.registry import LOCAL_VARIABLE, TRIAL_VARIABLE, approval_of
from clew.provider.horus import extractor_lineage as horus

FIXTURE = ROOT / "tests" / "fixtures" / "pantheon_vina"
SHEET = FIXTURE / "ligands.smi"
NAME = "site-ligands"

ADAPTER = (ROOT / "tests" / "fixtures" / "builder" / "adapter.py").read_text()

SHADOW = '''

class Shadow(Adapter):
    name = "sarek"
'''


class Built(unittest.TestCase):
    """A work folder as the agent leaves it, and the run's graph beside it."""

    @classmethod
    def setUpClass(cls):
        cls.graph_text = json.dumps(horus.extract(FIXTURE))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name) / "work"
        self.work.mkdir()
        self.graph = Path(self.tmp.name) / "graph.json"
        self.graph.write_text(self.graph_text)
        self.providers = Path(self.tmp.name) / "providers"
        # The judge and the commands it runs are fresh processes: they find
        # this checkout by path, and no provider folder of the machine's own.
        clean = {k: v for k, v in os.environ.items() if k not in (LOCAL_VARIABLE, TRIAL_VARIABLE)}
        patched = mock.patch.dict(os.environ, dict(clean, PYTHONPATH=str(ROOT)), clear=True)
        patched.start()
        self.addCleanup(patched.stop)

    def wrote(self, adapter=ADAPTER, tests=True):
        (self.work / "adapter.py").write_text(adapter)
        if tests:
            (self.work / "test_adapter.py").write_text("import unittest\n")

    def verdict(self, name=NAME, sheet=SHEET):
        return builder.judged(self.work, self.graph, name, sheet)

    def failed(self, verdict):
        return [check["name"] for check in verdict["checks"] if not check["passed"]]


class TestJudge(Built):
    def test_an_adapter_clew_can_use_passes_every_check(self):
        self.wrote()
        verdict = self.verdict()
        self.assertEqual(self.failed(verdict), [])
        self.assertTrue(verdict["passed"])
        kind, = verdict["kinds"]
        self.assertEqual((kind["kind"], kind["mode"], kind["ids"], kind["reached"], kind["unreached"]),
                         ("ligand", "remove", 3, 3, []))
        asked = [check["name"] for check in verdict["checks"]]
        self.assertIn("triage offers its ids", asked)
        self.assertIn("impact answers for ligand:aspirin", asked)

    def test_no_adapter_is_the_only_check_that_runs(self):
        verdict = self.verdict()
        self.assertFalse(verdict["passed"])
        self.assertEqual(self.failed(verdict), ["adapter.py is there"])

    def test_an_adapter_that_does_not_import_says_why(self):
        self.wrote("import no_such_module_anywhere\n")
        verdict = self.verdict()
        self.assertEqual(self.failed(verdict), ["the adapter imports"])
        self.assertIn("no_such_module_anywhere", verdict["checks"][-1]["detail"])

    def test_another_name_than_the_one_asked_for_fails(self):
        self.wrote(ADAPTER.replace('name = "site-ligands"', 'name = "other"'))
        self.assertEqual(self.failed(self.verdict()), [f"registers an adapter named {NAME}"])

    def test_a_name_an_installed_adapter_has_is_refused(self):
        self.wrote(ADAPTER.replace('name = "site-ligands"', 'name = "sarek"'))
        self.assertEqual(self.failed(self.verdict(name="sarek")), ["the name sarek is free"])

    def test_a_file_that_replaces_another_provider_fails(self):
        self.wrote(ADAPTER + SHADOW)
        verdict = self.verdict()
        self.assertEqual(self.failed(verdict), ["registers nothing else"])
        self.assertIn("sarek", verdict["checks"][-1]["detail"])

    def test_answering_for_an_id_nobody_has_fails(self):
        self.wrote(ADAPTER.replace("if value is not None and value not in ids:", "if False:"))
        self.assertIn("ligand: an unknown id is refused", self.failed(self.verdict()))

    def test_naming_a_task_the_run_does_not_have_fails(self):
        self.wrote(ADAPTER.replace("list(entry) for", 'list(entry) + ["feedface"] for'))
        self.assertIn("ligand: every task it names is in the run", self.failed(self.verdict()))

    def test_ids_that_reach_no_task_fail_and_are_named(self):
        self.wrote(ADAPTER.replace("library = Path(args.sheet).name", 'library = "absent.smi"'))
        verdict = self.verdict()
        self.assertIn("ligand: at least one id reaches a task", self.failed(verdict))
        self.assertEqual(verdict["kinds"][0]["unreached"], ["aspirin", "caffeine", "imatinib"])

    def test_listing_other_ids_than_it_resolves_fails(self):
        self.wrote(ADAPTER.replace("        return self.ids(args)\n\n\nclass",
                                   "        return self.ids(args)[:1]\n\n\nclass"))
        self.assertIn("ligand: lists the same ids it resolves", self.failed(self.verdict()))

    def test_an_adapter_without_its_own_tests_fails(self):
        self.wrote(tests=False)
        self.assertEqual(self.failed(self.verdict()), ["the agent wrote its own tests"])

    def test_a_judge_that_cannot_run_is_a_failed_verdict(self):
        self.wrote()
        verdict = builder.judged(self.work, Path(self.tmp.name) / "absent.json", NAME, SHEET)
        self.assertFalse(verdict["passed"])
        self.assertEqual(self.failed(verdict), ["the judge ran"])


class TestInstall(Built):
    def setUp(self):
        super().setUp()
        self.wrote()
        self.passed = {"passed": True, "checks": [{"name": "c", "passed": True, "detail": ""}],
                       "kinds": [{"kind": "ligand"}]}

    def install(self, **over):
        given = dict({"work": self.work, "providers": self.providers, "name": NAME,
                      "actor": "qa.lead@example.org", "verdict": self.passed,
                      "at": "2026-10-04T12:00:00+00:00"}, **over)
        return builder.install(**given)

    def listed(self, trial=False):
        """What `clew providers` says, with this folder as the local one."""
        env = dict(os.environ, **{LOCAL_VARIABLE: str(self.providers)},
                   **({TRIAL_VARIABLE: "1"} if trial else {}))
        ran = subprocess.run([sys.executable, "-m", "clew", "providers"], env=env,
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        return ran.stdout

    def test_an_install_leaves_the_file_and_who_approved_which_hash(self):
        record = self.install()
        target = self.providers / "site_ligands.py"
        self.assertEqual(target.read_text(), ADAPTER)
        self.assertEqual(json.loads(target.with_suffix(".approval.json").read_text()), record)
        self.assertEqual((record["name"], record["actor"], record["approved_at"]),
                         (NAME, "qa.lead@example.org", "2026-10-04T12:00:00+00:00"))
        self.assertEqual(record["judge"], {"passed": True, "checks": 1, "kinds": [{"kind": "ligand"}]})
        self.assertIn("agent adapter-builder", record["written_by"])
        self.assertEqual(approval_of(target), (record, None))

    def test_an_installed_adapter_loads_and_is_listed_with_its_approver(self):
        self.install()
        text = self.listed()
        self.assertIn("site_ligands.py  approved by qa.lead@example.org\n    adapter site-ligands\n", text)

    def test_a_file_changed_after_approval_is_refused(self):
        self.install()
        target = self.providers / "site_ligands.py"
        target.write_text(ADAPTER + "\n# one more line\n")
        text = self.listed()
        self.assertIn("site_ligands.py  REFUSED: changed since qa.lead@example.org approved it", text)
        self.assertNotIn("adapter site-ligands", text)

    def test_a_file_with_no_approval_is_refused(self):
        self.providers.mkdir()
        (self.providers / "site_ligands.py").write_text(ADAPTER)
        text = self.listed()
        self.assertIn("site_ligands.py  REFUSED: no approval record beside it", text)
        self.assertNotIn("adapter site-ligands", text)

    def test_an_approval_that_names_nobody_is_refused(self):
        self.install()
        record = self.providers / "site_ligands.approval.json"
        record.write_text(json.dumps(dict(json.loads(record.read_text()), actor="")))
        self.assertIn("REFUSED: its approval names nobody", self.listed())

    def test_a_file_refused_after_a_change_stops_answering_in_a_running_process(self):
        self.install()
        script = f"""
import json, os
from pathlib import Path
from clew.contracts import Adapter, discover
target = Path({str(self.providers / "site_ligands.py")!r})
print("site-ligands" in discover(Adapter))
target.write_text(target.read_text() + "\\n# later\\n")
print("site-ligands" in discover(Adapter))
"""
        env = dict(os.environ, **{LOCAL_VARIABLE: str(self.providers)})
        ran = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(ran.stdout.split(), ["True", "False"], ran.stderr)

    def test_only_a_trial_loads_a_file_nobody_approved(self):
        self.providers.mkdir()
        (self.providers / "site_ligands.py").write_text(ADAPTER)
        self.assertIn("on trial\n    adapter site-ligands\n", self.listed(trial=True))

    def test_an_install_needs_a_name_a_passed_verdict_and_a_free_place(self):
        for bad in ({"actor": "  "}, {"verdict": dict(self.passed, passed=False)},
                    {"verdict": None}, {"work": self.providers}):
            with self.assertRaises(builder.Refused, msg=bad):
                self.install(**bad)
        self.assertFalse(self.providers.exists() and any(self.providers.iterdir()))
        self.install()
        with self.assertRaises(builder.Refused):
            self.install()


class TestApprove(Built):
    """A person approves from the judge's verdict alone, by command, under their name."""

    def setUp(self):
        super().setUp()
        self.wrote()
        self.verdict = Path(self.tmp.name) / "verdict.json"
        self.verdict.write_text(json.dumps(builder.judged(self.work, self.graph, NAME, SHEET)))

    def approve(self, *extra):
        env = dict(os.environ, **{LOCAL_VARIABLE: str(self.providers)})
        return subprocess.run([sys.executable, "-m", "clew", "providers", "--approve", str(self.verdict),
                               "--actor", "qa.lead@example.org", *extra],
                              env=env, capture_output=True, text=True, timeout=120)

    def test_the_verdict_says_what_it_judged(self):
        verdict = json.loads(self.verdict.read_text())
        self.assertEqual((verdict["name"], verdict["written"], Path(verdict["work"])),
                         (NAME, "adapter.py", self.work.resolve()))
        self.assertEqual(len(verdict["sha256"]), 64)

    def test_approving_by_command_installs_under_the_approver(self):
        ran = self.approve()
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("approved by qa.lead@example.org", ran.stdout)
        record = json.loads((self.providers / "site_ligands.approval.json").read_text())
        self.assertEqual(record["actor"], "qa.lead@example.org")
        self.assertEqual((self.providers / "site_ligands.py").read_text(), ADAPTER)

    def test_a_file_changed_since_the_verdict_is_refused(self):
        (self.work / "adapter.py").write_text(ADAPTER + "\n# later\n")
        ran = self.approve()
        self.assertNotEqual(ran.returncode, 0)
        self.assertIn("changed after the judge saw it", ran.stderr)
        self.assertFalse(self.providers.exists())

    def test_a_failed_verdict_is_refused(self):
        (self.work / "test_adapter.py").unlink()
        self.verdict.write_text(json.dumps(builder.judged(self.work, self.graph, NAME, SHEET)))
        ran = self.approve()
        self.assertNotEqual(ran.returncode, 0)
        self.assertIn("did not pass", ran.stderr)

    def test_without_the_variable_the_default_folder_is_used(self):
        env = {k: v for k, v in os.environ.items() if k != LOCAL_VARIABLE}
        env["HOME"] = self.tmp.name
        ran = subprocess.run([sys.executable, "-m", "clew", "providers", "--approve", str(self.verdict),
                              "--actor", "qa"], env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertTrue((Path(self.tmp.name) / ".clew" / "providers" / "site_ligands.py").is_file())


class TestBrief(unittest.TestCase):
    def test_a_name_and_a_kind_are_checked_before_anything_runs(self):
        self.assertEqual(builder.check_brief("site-ligands", "ligand"), ["ligand"])
        self.assertEqual(builder.check_brief("site", "ligand, batch,lane, ligand"), ["ligand", "batch", "lane"])
        for name, kind in (("", "ligand"), ("Site", "ligand"), ("a", "ligand"), ("x" * 33, "ligand"),
                           ("../up", "ligand"), ("site", ""), ("site", "two words"), ("site", "a-b")):
            with self.assertRaises(builder.Refused, msg=(name, kind)):
                builder.check_brief(name, kind)

    def brief(self, **over):
        given = dict({"job": "/home/jobs/j1", "home": "/home/mainsheet", "python": "/venv/bin/python",
                      "name": "site-ligands",
                      "kind": "ligand", "removable": True, "notes": "", "sheet": "/home/jobs/j1/sheet.csv"},
                     **over)
        return builder.definition(**given)

    def test_the_task_carries_what_the_person_said(self):
        task = self.brief(notes="ids are in column two")["task"]
        for said in ('name = "site-ligands"', 'kind "ligand"', "REMOVE", "/home/jobs/j1/graph.json",
                     "/home/jobs/j1/sheet.csv", "ids are in column two",
                     "/venv/bin/python -m unittest test_adapter"):
            self.assertIn(said, task)
        bare = self.brief(removable=False, sheet=None)["task"]
        self.assertIn("TRACE", bare)
        self.assertIn("no launch sheet", bare)
        self.assertNotIn("--sheet", bare)
        self.assertNotIn("SEPARABLE", bare)
        several = self.brief(kind="patient, batch", separable=True)["task"]
        self.assertIn('each of the kinds "patient", "batch"', several)
        self.assertIn("2 things they call patient, batch", several)
        self.assertIn("returning SEPARABLE", several)

    def test_the_definition_is_one_mainsheet_accepts_and_its_gate_keeps_the_agent_in_its_folders(self):
        try:
            from mainsheet.agent.config import AgentConfig
            from mainsheet.agent.policy import Gate
        except ImportError:
            self.skipTest("Mainsheet is not installed in this environment")
        config = AgentConfig.model_validate(json.loads(json.dumps(self.brief())))
        gate = Gate(config.policy)
        work = "/home/mainsheet/instances/adapter-builder-0a1b2c3d/work"

        def allowed(tool, **args):
            return gate.pre(tool, args).allow

        self.assertTrue(allowed("Read", file_path=str(builder.SOURCE / "contracts" / "adapter.py")))
        self.assertTrue(allowed("Read", file_path=str(builder.GUIDE)))
        self.assertTrue(allowed("Read", file_path="/home/jobs/j1/graph.json"))
        self.assertTrue(allowed("Read", file_path="/home/jobs/j1/sheet.csv"))
        self.assertTrue(allowed("Write", file_path=f"{work}/adapter.py"))
        self.assertTrue(allowed("Read", file_path=f"{work}/adapter.py"))
        self.assertTrue(allowed("Edit", file_path=f"{work}/test_adapter.py"))
        self.assertTrue(allowed("Glob", pattern="*.py", path=work))
        self.assertTrue(allowed("Write", file_path="test_adapter.py"))
        self.assertTrue(allowed("Grep", pattern="class", path=str(builder.SOURCE)))
        self.assertTrue(allowed("Bash", command="/venv/bin/python -m unittest test_adapter -v"))
        # Another job, the rest of the machine, the judge and the approval are not its to touch.
        self.assertFalse(allowed("Read", file_path="/home/jobs/j2/graph.json"))
        self.assertFalse(allowed("Read", file_path="/etc/passwd"))
        self.assertFalse(allowed("Read", file_path=str(builder.SOURCE / "builder" / "judge.py")))
        self.assertFalse(allowed("Write", file_path="/home/jobs/j1/graph.json"))
        self.assertFalse(allowed("Write", file_path=str(builder.SOURCE / "contracts" / "adapter.py")))
        self.assertFalse(allowed("Read", file_path="/home/jobs/j1/../j2/graph.json"))
        self.assertFalse(allowed("Read", file_path="/elsewhere/instances/adapter-builder-0a1b2c3d/work/x"))
        self.assertFalse(allowed("Write", file_path="/home/mainsheet/instances/clew-0a1b2c3d/work/x"))
        self.assertFalse(allowed("Bash", command="cat /home/providers/x.approval.json"))
        self.assertFalse(allowed("Bash", command="cat ../../../trail/key"))
        # A command may name no absolute path outside what the agent was given and the system.
        self.assertTrue(allowed("Bash", command=f"cd {work} && /venv/bin/python -m unittest -v"))
        self.assertTrue(allowed("Bash", command="ls /home/jobs/j1/sheet 2>/dev/null | head"))
        self.assertTrue(allowed("Bash", command=f"grep -rn Trigger {builder.SOURCE}/contracts/"))
        self.assertTrue(allowed("Bash", command="cat /etc/hosts; ls ./here there/x https://x.y/z"))
        self.assertFalse(allowed("Bash", command="ls /home/jobs/j2"))
        self.assertFalse(allowed("Bash", command="ls -la \"/Users/someone/Documents\" | head"))
        self.assertFalse(allowed("Bash", command="cat /home/mainsheet/trail/receipts.jsonl"))
        self.assertFalse(allowed("Bash", command="ls /"))
        self.assertFalse(allowed("Bash", command="pip install something"))


if __name__ == "__main__":
    unittest.main()
