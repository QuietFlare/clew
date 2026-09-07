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

from clew.graph import triggers
from clew.graph import graph as core_graph

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


class TestContainerMatching(unittest.TestCase):
    """
    A needle and an image are read as (name, version) before they are
    compared. Substring matching let "samtools:1.21" miss every Wave
    image, whose tag is a hash, and reported 46 affected tasks instead of
    72 with no warning.
    """

    def test_image_forms_parse_to_name_and_version(self):
        cases = {
            "quay.io/biocontainers/samtools:1.21--h50ea8bc_0": ("samtools", "1.21--h50ea8bc_0"),
            "community.wave.seqera.io/library/bwa_htslib_samtools:56c9f8d5201889a4":
                ("bwa_htslib_samtools", None),
            "quay.io/biocontainers/mulled-v2-093691b47d719890dc19ac0c13c4528e9776897f:"
            "27211b8c38006480d69eb1be3ef09a7bf0a49d76-0":
                ("mulled-v2-093691b47d719890dc19ac0c13c4528e9776897f", None),
            "wf.wgs@sha256:0123abcd": ("wf.wgs", None),
            "gatk_haplotypecaller@app-gatk": ("gatk_haplotypecaller", None),
            "conda@1234abcd": ("conda", None),
            "quay.io-biocontainers-samtools-1.21--h50ea8bc_0.img":
                ("quay.io-biocontainers-samtools", "1.21--h50ea8bc_0"),
            "toolkit-2.1": ("toolkit", "2.1"),
            "ubuntu:latest": ("ubuntu", None),
            "docker://quay.io/biocontainers/gatk4:4.5.0.0--py36hdfd78af_0@sha256:ff":
                ("gatk4", "4.5.0.0--py36hdfd78af_0"),
        }
        for image, (name, version) in cases.items():
            with self.subTest(image=image):
                got_name, _, got_version = core_graph.parse_image(image)
                self.assertEqual((got_name, got_version), (name, version))

    def test_a_tool_inside_a_wave_image_is_a_component(self):
        _, components, _ = core_graph.parse_image(
            "community.wave.seqera.io/library/bwa_htslib_samtools:56c9f8d5201889a4")
        self.assertEqual(components, {"bwa_htslib_samtools", "bwa", "htslib", "samtools"})

    def test_versions_must_agree_when_both_carry_one(self):
        image = "quay.io/biocontainers/samtools:1.21--h50ea8bc_0"
        self.assertEqual(core_graph.container_match("samtools:1.21", image), "exact")
        self.assertEqual(core_graph.container_match("samtools", image), "exact")
        self.assertIsNone(core_graph.container_match("samtools:1.22", image))
        # A prefix ending mid-number is not a version match.
        self.assertIsNone(core_graph.container_match("samtools:1.2", image))

    def test_a_versionless_image_matches_on_name_only(self):
        image = "community.wave.seqera.io/library/bwa_htslib_samtools:56c9f8d5201889a4"
        self.assertEqual(core_graph.container_match("samtools:1.21", image), "name-only")
        self.assertEqual(core_graph.container_match("samtools", image), "exact")

    def test_a_different_tool_does_not_match_on_substring(self):
        self.assertIsNone(core_graph.container_match("gatk", "quay.io/gatk4:4.2.1"))
        self.assertIsNone(core_graph.container_match("bwa", "quay.io/bwakit:0.7"))

    def test_the_trigger_kind_uses_the_same_rule(self):
        graph = {"tasks": {
            "a": {"container": "quay.io/biocontainers/samtools:1.21--h50ea8bc_0"},
            "b": {"container": "quay.io/biocontainers/samtools:1.16.1--h6899075_1"},
            "c": {"container": "community.wave.seqera.io/library/bwa_htslib_samtools:56c9f8d5201889a4"},
        }, "edges": [], "outputs": {}}
        self.assertEqual(triggers.resolve(graph, "container", "samtools:1.21"),
                         {"container:samtools:1.21": ["a", "c"]})
        self.assertEqual(core_graph.container_matches(graph, "samtools:1.21"),
                         {"a": "exact", "c": "name-only"})


class TestInputCompanions(unittest.TestCase):
    """
    An index or dictionary is regenerated with the file it belongs to, so
    "genome.fasta" reaches a task that consumed only "genome.fasta.fai".
    """

    GRAPH = {"tasks": {"a": {}, "b": {}, "c": {}, "d": {}}, "edges": [
        {"consumer": "a", "producer": "EXTERNAL", "filename": "genome.fasta"},
        {"consumer": "b", "producer": "EXTERNAL", "filename": "3/genome.fasta.fai"},
        {"consumer": "c", "producer": "EXTERNAL", "filename": "genome.dict"},
        {"consumer": "d", "producer": "EXTERNAL", "filename": "bwa"},
    ], "outputs": {}}

    def test_exact_and_dotted_companions_are_entry_nodes(self):
        self.assertEqual(core_graph.external_input_entry_nodes(self.GRAPH, "genome.fasta"),
                         {"input:genome.fasta": ["a", "b"]})
        self.assertEqual(triggers.resolve(self.GRAPH, "input", "genome.fasta"),
                         {"input:genome.fasta": ["a", "b"]})

    def test_the_extra_files_are_named(self):
        self.assertEqual(core_graph.external_input_matches(self.GRAPH, "genome.fasta"),
                         {"genome.fasta": ["a"], "genome.fasta.fai": ["b"]})

    def test_a_directory_input_matches_by_its_own_name(self):
        self.assertEqual(core_graph.external_input_entry_nodes(self.GRAPH, "bwa"),
                         {"input:bwa": ["d"]})


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
