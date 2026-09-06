"""
Reclaim proposes a directory only when the graph and the disk together
prove its bytes are redundant, and identity is by content digest alone.
Every verdict here is pinned against a small synthetic run laid out on a
temporary disk, digested the way `clew digest` would.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.extract import digest
from clew.questions import reclaim


def task(hash_, process, workdir, status="COMPLETED", script="echo", container="img",
         **extra):
    return {"hash": hash_, "process": process, "name": process, "workdir": workdir,
            "status": status, "script": script, "container": container, **extra}


class Disk:
    """A work root with one directory per task and files of known content."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp())
        self.work = self.root / "work"
        self.results = self.root / "results"
        self.external = self.root / "ref.fa"
        self.external.write_bytes(b"ACGT" * 10)
        self.results.mkdir()

    def task_dir(self, hash_, files):
        path = self.work / hash_
        path.mkdir(parents=True)
        for name, content in files.items():
            (path / name).write_bytes(content)
        (path / ".command.sh").write_text("echo")
        return "/elsewhere/work/" + hash_

    def publish(self, name, content, subdir="out"):
        (self.results / subdir).mkdir(exist_ok=True)
        (self.results / subdir / name).write_bytes(content)

    def cleanup(self):
        shutil.rmtree(self.root)


class Run(unittest.TestCase):
    """
    ref.fa (external) -> A (align) -> B (stats) -> C (report, published)
    A's bam is consumed by B only. B's stats are consumed by C only.
    C's report has a published copy. Every task also writes versions.yml.
    """

    def setUp(self):
        self.disk = Disk()
        d = self.disk
        self.graph = {
            "tasks": {
                "aa/000001": task("aa/000001", "ALIGN", d.task_dir("aa/000001", {"s.bam": b"x" * 300, "versions.yml": b"v"})),
                "bb/000002": task("bb/000002", "STATS", d.task_dir("bb/000002", {"s.stats": b"y" * 40, "versions.yml": b"v"})),
                "cc/000003": task("cc/000003", "REPORT", d.task_dir("cc/000003", {"report.html": b"z" * 70, "versions.yml": b"v"})),
            },
            "edges": [
                {"consumer": "aa/000001", "producer": "EXTERNAL", "filename": "ref.fa", "target": str(d.external)},
                {"consumer": "bb/000002", "producer": "aa/000001", "filename": "s.bam", "target": ""},
                {"consumer": "cc/000003", "producer": "bb/000002", "filename": "s.stats", "target": ""},
            ],
            "outputs": {
                "aa/000001": ["s.bam", "versions.yml"],
                "bb/000002": ["s.stats", "versions.yml"],
                "cc/000003": ["report.html", "versions.yml"],
            },
        }
        d.publish("report.html", b"z" * 70)

    def tearDown(self):
        self.disk.cleanup()

    def digested(self):
        digest.digest_outputs(self.graph, self.disk.work)
        digest.digest_results(self.graph, self.disk.results)
        return self.graph

    def verdicts(self, results=True, digested=True, **kw):
        graph = self.digested() if digested else self.graph
        items = reclaim.Reclaimer(graph, self.disk.work,
                                  self.disk.results if results else None, **kw).plan()
        return {i["task"]: i for i in items}

    def test_a_published_task_is_redundant(self):
        v = self.verdicts()
        self.assertEqual(v["cc/000003"]["verdict"], reclaim.REDUNDANT)
        self.assertEqual(v["cc/000003"]["published_copies"][0],
                         {"output": "report.html", "published": ["out/report.html"],
                          "verified": "digest"})

    def test_without_digests_everything_is_kept(self):
        v = self.verdicts(digested=False)
        for item in v.values():
            self.assertEqual(item["verdict"], reclaim.KEEP)
            self.assertIn("no content digest", item["reason"])

    def test_without_results_recorded_copies_are_not_trusted(self):
        v = self.verdicts(results=False)
        self.assertEqual(v["cc/000003"]["verdict"], reclaim.KEEP)
        self.assertIn("--results not given", v["cc/000003"]["reason"])

    def test_a_published_copy_with_different_bytes_is_not_a_copy(self):
        self.disk.publish("report.html", b"w" * 70)
        v = self.verdicts()
        self.assertEqual(v["cc/000003"]["verdict"], reclaim.KEEP)
        self.assertIn("not published: report.html", v["cc/000003"]["reason"])

    def test_a_deleted_published_copy_withholds(self):
        graph = self.digested()
        (self.disk.results / "out" / "report.html").unlink()
        v = {i["task"]: i for i in reclaim.Reclaimer(graph, self.disk.work, self.disk.results).plan()}
        self.assertEqual(v["cc/000003"]["verdict"], reclaim.KEEP)

    def test_a_published_symlink_into_work_withholds(self):
        published = self.disk.results / "out" / "report.html"
        published.unlink()
        published.symlink_to(self.disk.work / "cc/000003" / "report.html")
        graph = self.digested()
        graph["published"]["out/report.html"] = graph["output_details"]["cc/000003"][0] | {}
        v = {i["task"]: i for i in reclaim.Reclaimer(graph, self.disk.work, self.disk.results).plan()}
        self.assertEqual(v["cc/000003"]["verdict"], reclaim.KEEP)
        self.assertIn("symlink into work", v["cc/000003"]["reason"])

    def test_a_published_hardlink_is_redundant(self):
        published = self.disk.results / "out" / "report.html"
        published.unlink()
        os.link(self.disk.work / "cc/000003" / "report.html", published)
        v = self.verdicts()
        self.assertEqual(v["cc/000003"]["verdict"], reclaim.REDUNDANT)
        self.assertEqual(v["cc/000003"]["published_copies"][0]["verified"], "hardlink")

    def test_intermediates_are_kept_unless_asked(self):
        v = self.verdicts()
        self.assertEqual(v["aa/000001"]["verdict"], reclaim.KEEP)
        self.assertEqual(v["bb/000002"]["verdict"], reclaim.KEEP)

    def test_intermediates_with_recipe_and_inputs_are_proposed(self):
        v = self.verdicts(intermediates=True)
        self.assertEqual(v["aa/000001"]["verdict"], reclaim.INTERMEDIATE)
        self.assertEqual(v["bb/000002"]["verdict"], reclaim.INTERMEDIATE)

    def test_bytes_count_regular_files_only(self):
        (self.disk.work / "aa/000001" / "staged.fq").symlink_to(self.disk.external)
        v = self.verdicts()
        self.assertEqual(v["aa/000001"]["bytes"], 300 + 1 + len("echo"))

    def test_bookkeeping_outputs_do_not_block(self):
        v = self.verdicts(ignore=())
        self.assertEqual(v["cc/000003"]["verdict"], reclaim.KEEP)
        self.assertIn("versions.yml", v["cc/000003"]["reason"])

    def test_missing_external_input_withholds_an_intermediate(self):
        self.disk.external.unlink()
        v = self.verdicts(intermediates=True)
        self.assertEqual(v["aa/000001"]["verdict"], reclaim.KEEP)
        self.assertIn("ref.fa", v["aa/000001"]["reason"])
        self.assertEqual(v["bb/000002"]["verdict"], reclaim.INTERMEDIATE)

    def test_a_store_uri_input_is_checked_at_its_path(self):
        self.graph["edges"][0]["target"] = f"file://{self.disk.external}#abc123"
        v = self.verdicts(intermediates=True)
        self.assertEqual(v["aa/000001"]["verdict"], reclaim.INTERMEDIATE)

    def test_a_gone_producer_must_itself_be_recomputable(self):
        graph = self.digested()
        shutil.rmtree(self.disk.work / "aa/000001")
        graph["tasks"]["aa/000001"]["container"] = ""
        v = {i["task"]: i for i in reclaim.Reclaimer(
            graph, self.disk.work, self.disk.results, intermediates=True).plan()}
        self.assertEqual(v["aa/000001"]["verdict"], reclaim.GONE)
        self.assertEqual(v["bb/000002"]["verdict"], reclaim.KEEP)
        self.assertIn("aa/000001", v["bb/000002"]["reason"])

    def test_no_recipe_withholds_an_intermediate(self):
        self.graph["tasks"]["bb/000002"]["script"] = ""
        v = self.verdicts(intermediates=True)
        self.assertEqual(v["bb/000002"]["verdict"], reclaim.KEEP)

    def test_a_terminal_unpublished_output_withholds(self):
        (self.disk.results / "out" / "report.html").unlink()
        v = self.verdicts(intermediates=True)
        self.assertEqual(v["cc/000003"]["verdict"], reclaim.KEEP)
        self.assertIn("report.html", v["cc/000003"]["reason"])

    def test_superseded_and_failed_attempts(self):
        self.graph["tasks"]["aa/000001"]["superseded"] = True
        self.graph["tasks"]["bb/000002"]["status"] = "FAILED"
        self.graph["edges"] = [e for e in self.graph["edges"] if e["consumer"] != "cc/000003"]
        v = self.verdicts()
        self.assertEqual(v["aa/000001"]["verdict"], reclaim.SUPERSEDED)
        self.assertEqual(v["bb/000002"]["verdict"], reclaim.FAILED)

    def test_a_failed_task_that_was_consumed_is_kept(self):
        self.graph["tasks"]["bb/000002"]["status"] = "FAILED"
        v = self.verdicts()
        self.assertEqual(v["bb/000002"]["verdict"], reclaim.KEEP)

    def test_apply_writes_a_receipt_line_before_each_removal(self):
        v = self.verdicts()
        receipt = self.disk.root / "receipt.jsonl"
        removed = reclaim.apply(list(v.values()), self.disk.work, receipt, {reclaim.REDUNDANT})
        self.assertEqual(removed, 1)
        self.assertFalse((self.disk.work / "cc/000003").exists())
        self.assertTrue((self.disk.work / "aa/000001").exists())
        lines = [json.loads(l) for l in receipt.read_text().splitlines()]
        self.assertEqual(lines[0]["task"], "cc/000003")
        self.assertIn("removed_at", lines[0])

    def test_apply_refuses_paths_outside_the_work_root(self):
        v = self.verdicts()
        v["cc/000003"]["dir"] = str(self.disk.results)
        with self.assertRaises(SystemExit):
            reclaim.apply(list(v.values()), self.disk.work, self.disk.root / "r.jsonl",
                          {reclaim.REDUNDANT})
        self.assertTrue(self.disk.results.exists())

    def test_target_restricts_the_plan_to_one_machine(self):
        self.graph["tasks"]["aa/000001"]["target"] = "cluster-a"
        self.graph["tasks"]["bb/000002"]["target"] = "cluster-b"
        self.graph["tasks"]["cc/000003"]["target"] = "cluster-b"
        v = self.verdicts(target="cluster-b")
        self.assertEqual(sorted(v), ["bb/000002", "cc/000003"])

    def test_the_page_shows_kept_next_to_reclaimable(self):
        from clew.views import reclaim_report
        v = self.verdicts()
        built = reclaim.plan_to_dict(list(v.values()), self.graph, self.disk.work,
                                     self.disk.results, False)
        page = reclaim_report.render(built)
        self.assertIn("REDUNDANT", page)
        self.assertIn("What withheld the rest", page)
        self.assertNotIn("<script", page)
        self.assertEqual(page, reclaim_report.render(built))

    def test_plan_json_is_clock_free(self):
        v = self.verdicts()
        args = (list(v.values()), self.graph, self.disk.work, self.disk.results, False)
        self.assertEqual(reclaim.plan_to_dict(*args), reclaim.plan_to_dict(*args))
        self.assertEqual(reclaim.plan_to_dict(*args)["verdicts"], {"KEEP": 2, "REDUNDANT": 1})


if __name__ == "__main__":
    unittest.main()
