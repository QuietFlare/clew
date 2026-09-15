"""
The extractor contract: defining a named subclass is the registration, and
the base checks every graph against the schema before writing it.
"""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.contracts import Extractor, discover


def good_graph():
    return {"tasks": {"a/1": {"hash": "a/1", "name": "x", "process": "x",
                              "container": "", "status": "COMPLETED",
                              "script": "", "workdir": "/w/a/1"}},
            "edges": [], "outputs": {}}


class Fake(Extractor):
    name = "fake-test"
    description = "a test double"
    graph = None

    def add_arguments(self, parser):
        parser.add_argument("--source", required=True)

    def extract(self, args):
        return self.graph


class TestRegistry(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        Extractor.registered.pop("fake-test", None)

    def test_builtins_register_by_import(self):
        names = set(discover(Extractor))
        self.assertTrue({"nextflow", "nextflow-work", "ro-crate", "horus", "cromwell",
                         "snakemake", "dnanexus", "latch"} <= names)

    def test_a_named_subclass_registers_itself(self):
        self.assertIsInstance(discover(Extractor)["fake-test"], Fake)

    def test_a_provider_missing_a_method_fails_at_definition(self):
        with self.assertRaises(TypeError):
            class Bare(Extractor):
                name = "bare-test"

    def test_run_writes_a_conforming_graph(self):
        Fake.graph = good_graph()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "g.json"
            with redirect_stdout(io.StringIO()) as printed:
                code = Fake.main(["--source", "x", "--json-out", str(out)])
            self.assertEqual(code, 0)
            self.assertTrue(out.exists())
            self.assertIn("tasks              : 1", printed.getvalue())

    def test_run_refuses_a_graph_that_breaks_the_contract(self):
        Fake.graph = {"tasks": {"a/1": {"hash": "a/1"}}, "edges": [], "outputs": {}}
        with self.assertRaises(SystemExit) as stop:
            Fake.main(["--source", "x"])
        self.assertIn("breaks the contract", str(stop.exception))


if __name__ == "__main__":
    unittest.main()
