"""
Trigger resolution: locating where something bad enters a graph.

The point of the registry is that a domain adds a vocabulary word by
labelling its artifacts, not by editing Clew. These tests use words that
appear nowhere else in the source.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.core import triggers

GRAPH = {
    "tasks": {
        "a": {"hash": "a", "container": "toolkit-2.1", "script": "prep.py",
              "process": "PREP", "labels": {"site": "north"}},
        "b": {"hash": "b", "container": "toolkit-2.1", "script": "run.py",
              "process": "RUN", "labels": {"site": "south"}},
        "c": {"hash": "c", "container": "other-1.0", "script": "",
              "process": "JOIN", "labels": {}},
    },
    "edges": [
        {"consumer": "a", "producer": "EXTERNAL", "filename": "reference.dat"},
        {"consumer": "b", "producer": "a", "filename": "mid.dat",
         "labels": {"batch": "017"}},
        {"consumer": "c", "producer": "b", "filename": "out.dat"},
    ],
    "outputs": {"a": ["mid.dat"], "b": ["out.dat"], "c": []},
}


class TestBuiltInKinds(unittest.TestCase):
    def test_container_matches_on_substring(self):
        """Versions vary, so a needle need not be the whole string."""
        found = triggers.resolve(GRAPH, "container", "toolkit")
        self.assertEqual(found, {"container:toolkit": ["a", "b"]})

    def test_script_finds_the_tasks_that_ran_it(self):
        """The trigger for a defect found in code after the fact."""
        found = triggers.resolve(GRAPH, "script", "prep.py")
        self.assertEqual(found, {"script:prep.py": ["a"]})

    def test_input_finds_consumers_of_an_external_file(self):
        """A file the run did not produce, invalidated upstream."""
        found = triggers.resolve(GRAPH, "input", "reference.dat")
        self.assertEqual(found, {"input:reference.dat": ["a"]})

    def test_a_producer_is_not_an_external_input(self):
        """mid.dat has a producer, so it is not an entry point."""
        found = triggers.resolve(GRAPH, "input", "mid.dat")
        self.assertEqual(found, {"input:mid.dat": []})


class TestLabelFallthrough(unittest.TestCase):
    """
    An unknown kind is a label key. This is what lets a domain add a word
    without Clew learning it.
    """

    def test_a_node_label_resolves(self):
        found = triggers.resolve(GRAPH, "site", "north")
        self.assertEqual(found, {"site:north": ["a"]})

    def test_an_edge_label_reaches_both_ends(self):
        """
        Labels sit on artifacts, so a labelled edge implicates the task
        that made it and the task that read it.
        """
        found = triggers.resolve(GRAPH, "batch", "017")
        self.assertEqual(found, {"batch:017": ["a", "b"]})

    def test_a_label_the_graph_does_not_carry_finds_nothing(self):
        """
        Empty rather than an error: the caller decides what that means.
        """
        found = triggers.resolve(GRAPH, "stain", "h-and-e")
        self.assertEqual(found, {"stain:h-and-e": []})

    def test_a_wrong_value_for_a_known_key_finds_nothing(self):
        """Labels match exactly; only container matching is fuzzy."""
        found = triggers.resolve(GRAPH, "site", "nor")
        self.assertEqual(found, {"site:nor": []})


class TestParsing(unittest.TestCase):
    def test_kind_and_value_split_on_the_first_colon(self):
        self.assertEqual(triggers.parse("container:toolkit"),
                         ("container", "toolkit"))

    def test_a_value_may_contain_colons(self):
        """Image references carry a tag."""
        self.assertEqual(triggers.parse("container:repo/img:1.2"),
                         ("container", "repo/img:1.2"))

    def test_a_spec_without_a_colon_is_refused(self):
        with self.assertRaises(SystemExit):
            triggers.parse("nonsense")

    def test_an_empty_half_is_refused(self):
        for spec in (":value", "kind:"):
            with self.assertRaises(SystemExit):
                triggers.parse(spec)


if __name__ == "__main__":
    unittest.main()
