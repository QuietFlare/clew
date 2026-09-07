"""
Attributing a Snakemake job to a sample: the id lives in the output path
that names the job, nowhere else.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.domains import snakemake as dom
from clew.graph import blast_radius as core

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ("sample_1", "sample_2", "sample_3")


def job(rule, path):
    node = f"{rule}/{path}"
    return node, {"hash": node, "name": f"{rule} ({path})", "process": rule,
                  "container": "", "status": "COMPLETED", "script": "sh",
                  "workdir": "/data/wf"}


def snk_run_graph():
    """trim -> align -> stats per sample, every stats file into multiqc."""
    tasks, edges, outputs = {}, [], {}
    report, task = job("multiqc", "report/multiqc.txt")
    tasks[report] = task
    outputs[report] = ["multiqc.txt"]
    for s in SAMPLES:
        trim, t = job("trim", f"trimmed/{s}.fq"); tasks[trim] = t
        align, t = job("align", f"aligned/{s}.bam"); tasks[align] = t
        stats, t = job("stats", f"stats/{s}.stats"); tasks[stats] = t
        outputs.update({trim: [f"{s}.fq"], align: [f"{s}.bam"], stats: [f"{s}.stats"]})
        edges += [
            {"consumer": trim, "producer": "EXTERNAL", "filename": f"{s}.fq", "target": f"raw/{s}.fq"},
            {"consumer": align, "producer": trim, "filename": f"{s}.fq", "target": f"trimmed/{s}.fq"},
            {"consumer": align, "producer": "EXTERNAL", "filename": "ref.fa", "target": "ref.fa"},
            {"consumer": stats, "producer": align, "filename": f"{s}.bam", "target": f"aligned/{s}.bam"},
            {"consumer": report, "producer": stats, "filename": f"{s}.stats", "target": f"stats/{s}.stats"},
        ]
    return {"tasks": tasks, "edges": edges, "outputs": outputs}


class Mentions(unittest.TestCase):
    def test_bounded_by_separators_or_the_ends(self):
        self.assertTrue(dom.mentions("trimmed/sample_1.fq", "sample_1"))
        self.assertTrue(dom.mentions("sample_1", "sample_1"))
        self.assertTrue(dom.mentions("merged/sample_1_sample_2.bam", "sample_2"))
        self.assertTrue(dom.mentions("out/sample_1/x.txt", "sample_1"))

    def test_a_longer_id_is_not_matched_by_its_prefix(self):
        self.assertFalse(dom.mentions("trimmed/sample_10.fq", "sample_1"))
        self.assertFalse(dom.mentions("trimmed/sample_1b.fq", "sample_1"))
        self.assertFalse(dom.mentions("trimmed/xsample_1.fq", "sample_1"))


class EntryNodes(unittest.TestCase):
    def setUp(self):
        self.graph = snk_run_graph()

    def test_a_sample_enters_at_its_own_trim_align_and_stats(self):
        entry = dom.subject_entry_nodes(self.graph, {s: [] for s in SAMPLES})
        self.assertEqual(entry["sample_1"], [
            "align/aligned/sample_1.bam", "stats/stats/sample_1.stats",
            "trim/trimmed/sample_1.fq"])
        self.assertEqual(len(entry["sample_2"]), 3)

    def test_sample_1_does_not_swallow_sample_10(self):
        node, task = job("trim", "trimmed/sample_10.fq")
        self.graph["tasks"][node] = task
        entry = dom.subject_entry_nodes(self.graph, {"sample_1": [], "sample_10": []})
        self.assertNotIn(node, entry["sample_1"])
        self.assertEqual(entry["sample_10"], [node])

    def test_multiqc_is_reached_as_shared(self):
        entry = dom.subject_entry_nodes(self.graph, {s: [] for s in SAMPLES})
        radius = core.blast_radius(self.graph, entry)["sample_1"]
        self.assertEqual(radius["shared"], {"multiqc/report/multiqc.txt"})
        self.assertEqual(len(radius["exclusive"]), 3)


class Samplesheet(unittest.TestCase):
    def test_the_sample_column_is_read_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            sheet = Path(tmp, "samplesheet.csv")
            sheet.write_text("sample,fastq_1\nsample_1,raw/sample_1.fq\nsample_9,raw/sample_9.fq\n")
            self.assertEqual(dom.load_subjects(sheet), {"sample_1": [], "sample_9": []})


class Cli(unittest.TestCase):
    def test_impact_takes_the_snakemake_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = Path(tmp, "graph.json")
            graph.write_text(json.dumps(snk_run_graph()))
            sheet = Path(tmp, "samplesheet.csv")
            sheet.write_text("sample\n" + "".join(f"{s}\n" for s in SAMPLES))
            result = subprocess.run(
                [sys.executable, "-m", "clew.questions.impact", "--graph", str(graph),
                 "--pipeline", "snakemake", "--samplesheet", str(sheet),
                 "--subject", "sample_1", "--json", "-"],
                capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout[result.stdout.index("{"):])
        self.assertEqual(plan["tasks_affected"], 4)
        by_task = {i["task"]: i for i in plan["plan"]}
        self.assertFalse(by_task["multiqc/report/multiqc.txt"]["exclusive"])
        self.assertTrue(by_task["trim/trimmed/sample_1.fq"]["exclusive"])


if __name__ == "__main__":
    unittest.main()
