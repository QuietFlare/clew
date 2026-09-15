"""
Pantheon W-02, AutoDock Vina docking, run under Horus with horus-lineage on
2026-09-11: three ligands, three tasks. The fixture is that run's record.
"""

import argparse
import io
import json
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from clew.graph import blast_radius as core
from clew.provider.horus import extractor_lineage as hz
from clew.provider.horus.adapter_vina_docking import VinaDocking
from clew.questions import impact

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "pantheon_vina"
LIGANDS = str(FIXTURE / "ligands.smi")


def run_impact(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        impact.main(["--pipeline", "vina-docking", "--graph", str(FIXTURE / "graph.json"), *argv])
    text = out.getvalue()
    return {i["task"].split("/")[1]: i for i in json.loads(text[text.index("{"):])["plan"]}


class TestVinaRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = hz.extract(FIXTURE)
        (FIXTURE / "graph.json").write_text(json.dumps(cls.graph))
        cls.adapter = VinaDocking()
        cls.kind = cls.adapter.triggers["ligand"]

    @classmethod
    def tearDownClass(cls):
        (FIXTURE / "graph.json").unlink(missing_ok=True)

    def test_three_tasks_and_the_library_enters_at_prep(self):
        by_process = {t["process"]: h for h, t in self.graph["tasks"].items()}
        self.assertEqual(sorted(by_process), ["dock", "prep", "summary"])
        entry = self.kind.resolve(self.graph, "caffeine", argparse.Namespace(ligands=LIGANDS))
        self.assertEqual(sorted(entry), ["aspirin", "caffeine", "imatinib"])
        self.assertEqual(entry["caffeine"], [by_process["prep"]])

    def test_every_ligand_reaches_everything_and_owns_nothing(self):
        entry = self.kind.resolve(self.graph, None, argparse.Namespace(ligands=LIGANDS))
        radius = core.blast_radius(self.graph, entry)
        for ligand, r in radius.items():
            self.assertEqual(len(r["affected"]), 3, ligand)
            self.assertEqual(r["exclusive"], set(), ligand)

    def test_withdrawing_a_ligand_is_separable_everywhere(self):
        items = run_impact("--trigger", "ligand:caffeine", "--ligands", LIGANDS, "--json", "-")
        for process, item in items.items():
            self.assertEqual(item["contribution"], "SEPARABLE", process)
            self.assertEqual(item["class_asserted_by"], "vina-docking", process)

    def test_a_defect_in_a_step_is_not_per_ligand(self):
        items = run_impact("--trigger", "process:dock", "--json", "-")
        self.assertEqual(items["dock"]["contribution"], "REGENERABLE")
        self.assertNotIn("class_asserted_by", items["dock"])

    def test_recorded_run_time_reaches_the_plan(self):
        by_process = {t["process"]: t for t in self.graph["tasks"].values()}
        self.assertAlmostEqual(by_process["dock"]["metrics"]["duration_s"], 28.89, places=1)
        items = run_impact("--trigger", "process:dock", "--json", "-")
        self.assertAlmostEqual(items["dock"]["metrics"]["duration_s"], 28.89, places=1)

    def test_a_step_defect_costs_the_recorded_time_of_what_is_rerun(self):
        # Storage must be verified for a verdict to settle to REGENERATE, so
        # stand in for the run's work tree with the recorded workpaths.
        import tempfile
        with tempfile.TemporaryDirectory() as work:
            for task in self.graph["tasks"].values():
                (Path(work) / task["workpath"]).mkdir(parents=True)
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                impact.main(["--pipeline", "vina-docking", "--graph", str(FIXTURE / "graph.json"),
                             "--trigger", "process:dock", "--work-root", work, "--json", "-"])
        text = out.getvalue()
        cost = json.loads(text[text.index("{"):])["cost"]
        bucket = cost["by_action"]["REGENERATE"]
        self.assertEqual(bucket["tasks"], 2)          # dock and summary
        self.assertAlmostEqual(bucket["metrics"]["duration_s"], 28.96, places=1)
        self.assertEqual(bucket["missing"], {"duration_s": 0})
        self.assertIn("COST OF THIS PLAN", text)

    def test_the_gate_lists_the_ligands(self):
        self.assertEqual(self.kind.values(argparse.Namespace(ligands=LIGANDS)),
                         ["aspirin", "caffeine", "imatinib"])


if __name__ == "__main__":
    unittest.main()
