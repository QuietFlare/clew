"""
clew digest reads each file once and records a sha256 the graph can use
in place of the file. It fills in what the engine did not record and
leaves what it did.
"""

import hashlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.extract import digest


def sha(content):
    return "sha256:" + hashlib.sha256(content).hexdigest()


class Digest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.work = self.root / "work" / "aa" / "000001"
        self.work.mkdir(parents=True)
        (self.work / "a.txt").write_bytes(b"alpha")
        (self.work / "b.txt").write_bytes(b"beta")
        self.results = self.root / "results"
        (self.results / "sub").mkdir(parents=True)
        (self.results / "sub" / "a.txt").write_bytes(b"alpha")
        (self.results / "link.txt").symlink_to(self.work / "b.txt")
        self.graph = {
            "tasks": {"aa/000001": {"hash": "aa/000001", "workdir": "/elsewhere/aa/000001"}},
            "edges": [],
            "outputs": {"aa/000001": ["a.txt", "b.txt", "gone.txt"]},
            "output_details": {"aa/000001": [
                {"file": "a.txt", "size": 5, "digest": "sha256:keep-what-the-engine-said"},
            ]},
        }

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_outputs_are_hashed_and_listed_outputs_gain_details(self):
        counts = digest.digest_outputs(self.graph, self.root / "work")
        details = {d["file"]: d for d in self.graph["output_details"]["aa/000001"]}
        self.assertEqual(details["a.txt"]["digest"], "sha256:keep-what-the-engine-said")
        self.assertEqual(details["b.txt"], {"file": "b.txt", "size": 4, "digest": sha(b"beta")})
        self.assertNotIn("digest", details["gone.txt"])
        self.assertEqual(counts, {"hashed": 1, "kept": 1, "missing": 1, "bytes": 4})

    def test_an_engine_digest_of_another_algorithm_is_kept(self):
        # A deep-mode hash is what joins this run to another holding the
        # same hash. Replacing it with a sha256 would break that join.
        self.graph["output_details"]["aa/000001"][0]["digest"] = "nextflow-deep:abc"
        counts = digest.digest_outputs(self.graph, self.root / "work")
        self.assertEqual(self.graph["output_details"]["aa/000001"][0]["digest"], "nextflow-deep:abc")
        self.assertEqual(counts["kept"], 1)

    def test_results_are_hashed_and_symlinks_skipped(self):
        counts = digest.digest_results(self.graph, self.results)
        self.assertEqual(self.graph["published"],
                         {"sub/a.txt": {"size": 5, "digest": sha(b"alpha")}})
        self.assertEqual(counts, {"hashed": 1, "links": 1, "bytes": 5})

    def test_main_writes_the_graph_back(self):
        import json
        path = self.root / "graph.json"
        path.write_text(json.dumps(self.graph))
        digest.main(["--graph", str(path), "--work-root", str(self.root / "work"),
                     "--results", str(self.results)])
        written = json.loads(path.read_text())
        self.assertIn("published", written)
        self.assertEqual(len(written["output_details"]["aa/000001"]), 3)


if __name__ == "__main__":
    unittest.main()
