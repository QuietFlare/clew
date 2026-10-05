"""
`clew build`, with a stand-in for the agent.

The stand-in leaves the files a real agent would, in the folder Mainsheet
would have made for it. Everything after that is real: the files are
collected, the conformance check runs as its own process, and the verdict
it prints is one a person can approve from.
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clew.builder import adapter as builder
from clew.builder import build
from clew.contracts.registry import LOCAL_VARIABLE, TRIAL_VARIABLE
from clew.provider.horus import extractor_lineage as horus

DOCKING = ROOT / "tests" / "fixtures" / "pantheon_vina"
ADAPTER = (ROOT / "tests" / "fixtures" / "builder" / "adapter.py").read_text()


def writes(adapter=ADAPTER, tests=True, seen=None):
    def launch(definition, agent_home, say):
        if seen is not None:
            seen.append(Path(definition))
        work = Path(agent_home) / "instances" / "adapter-builder-0a1b2c3d" / "work"
        work.mkdir(parents=True)
        if adapter:
            (work / "adapter.py").write_text(adapter)
        if tests:
            (work / "test_adapter.py").write_text("import unittest\n")
        say("[adapter-builder-0a1b2c3d] turns: 30  cost_usd: 1.2\n")
        return 0
    return launch


class Built(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name).resolve() / "home"
        self.graph = Path(self.tmp.name) / "graph.json"
        self.graph.write_text(json.dumps(horus.extract(DOCKING)))
        clean = {k: v for k, v in os.environ.items() if k not in (LOCAL_VARIABLE, TRIAL_VARIABLE)}
        patched = mock.patch.dict(os.environ, dict(clean, PYTHONPATH=str(ROOT)), clear=True)
        patched.start()
        self.addCleanup(patched.stop)

    def run_build(self, *argv, launch=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = build.main([*argv, "--home", str(self.home)], launch=launch or writes())
            except SystemExit as stopped:
                code = stopped.code
        return code, out.getvalue(), err.getvalue()

    def adapter(self, *extra, launch=None):
        return self.run_build("adapter", "--graph", str(self.graph), "--name", "site-ligands",
                              "--kind", "ligand", "--sheet", str(DOCKING / "ligands.smi"),
                              "--removable", *extra, launch=launch)

    def job(self):
        folder, = (self.home / "jobs").iterdir()
        return folder


class TestBuildAdapter(Built):
    def test_a_build_prints_the_checks_and_how_to_approve_and_installs_nothing(self):
        code, out, err = self.adapter()
        self.assertEqual(code, 0, out + err)
        self.assertIn("16 of 16 conformance checks passed", out)
        self.assertIn("ok    impact answers for ligand:aspirin", out)
        self.assertIn("30 agent turns, $1.200", err)
        verdict = self.job() / "verdict.json"
        self.assertIn(f"clew providers --approve {verdict} --actor", out)
        self.assertEqual((self.job() / "work" / "adapter.py").read_text(), ADAPTER)
        self.assertFalse((self.home / "providers").exists())

    def test_the_verdict_it_leaves_is_one_a_person_can_approve_from(self):
        self.adapter()
        record = builder.approve(self.job() / "verdict.json", self.home / "providers", "qa.lead@example.org")
        self.assertEqual((record["name"], record["actor"]), ("site-ligands", "qa.lead@example.org"))
        self.assertEqual((self.home / "providers" / "site_ligands.py").read_text(), ADAPTER)
        # The name is taken from then on.
        self.assertIn("already installed", str(self.adapter()[0]))

    def test_the_agent_is_briefed_from_the_build_folder(self):
        seen = []
        self.adapter("--notes", "ids are in column two", launch=writes(seen=seen))
        brief = json.loads(seen[0].read_text())
        self.assertEqual(seen[0], self.job() / "agent.yaml")
        self.assertEqual(brief["name"], "adapter-builder")
        for said in (str(self.job() / "graph.json"), str(self.job() / "sheet" / "ligands.smi"),
                     "ids are in column two", "REMOVE"):
            self.assertIn(said, brief["task"])
        self.assertNotIn(str(DOCKING), json.dumps(brief))

    def test_a_failed_check_exits_one_and_offers_no_approval(self):
        broken = ADAPTER.replace("if value is not None and value not in ids:", "if False:")
        code, out, err = self.adapter(launch=writes(broken))
        self.assertEqual(code, 1)
        self.assertIn("FAIL  ligand: an unknown id is refused", out)
        self.assertIn("15 of 16 conformance checks passed", out)
        self.assertNotIn("--approve", out)

    def test_an_agent_that_writes_nothing_exits_two(self):
        code, out, err = self.adapter(launch=writes(adapter=None))
        self.assertEqual(code, 2)
        self.assertIn("wrote no adapter", err)
        self.assertFalse((self.job() / "verdict.json").exists())

    def test_a_brief_is_refused_before_anything_starts(self):
        for argv in (("adapter", "--graph", str(self.graph), "--name", "Site", "--kind", "ligand"),
                     ("adapter", "--graph", str(self.graph), "--name", "sarek", "--kind", "ligand"),
                     ("adapter", "--graph", "/no/such.json", "--name", "site", "--kind", "ligand"),
                     ("adapter", "--graph", str(self.graph), "--name", "site", "--kind", "two words"),
                     ("extractor", "--record", str(DOCKING), "--name", "runlog"),
                     ("extractor", "--record", str(Path.home()), "--name", "runlog"),
                     ("extractor", "--record", "/no/such/folder", "--name", "runlog")):
            code, out, err = self.run_build(*argv)
            self.assertIn("clew build:", str(code), argv)
        self.assertFalse((self.home / "jobs").exists())


class TestBuildExtractor(Built):
    def test_an_extractor_build_briefs_its_own_agent_on_the_record_where_it_lies(self):
        record = Path(self.tmp.name).resolve() / "record"
        record.mkdir()
        (record / "runlog.jsonl").write_text("{}\n")
        seen = []

        def launch(definition, agent_home, say):
            seen.append(json.loads(Path(definition).read_text()))
            return 0
        code, out, err = self.run_build("extractor", "--record", str(record), "--name", "runlog",
                                        launch=launch)
        self.assertEqual(code, 2)
        self.assertIn("wrote no extractor", err)
        self.assertEqual(seen[0]["name"], "extractor-builder")
        self.assertIn(str(record), seen[0]["task"])
        self.assertIn('name = "runlog"', seen[0]["task"])


if __name__ == "__main__":
    unittest.main()
