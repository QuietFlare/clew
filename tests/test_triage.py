"""
Triage: a written notice becomes a trigger, or is held.

The classifier is never called here. Its answers are written out by hand,
which is also how an answer made elsewhere arrives, so the same checks
are exercised. The words in the graph appear nowhere else in the source.
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from types import SimpleNamespace

from clew.contracts import REMOVE, Trigger
from clew.contracts import trigger as triggers_contract
from clew.graph import blast_radius as core
from clew.graph import triggers
from clew.intake import classifier, triage

GRAPH = {
    "tasks": {
        "a": {"hash": "a", "container": "registry.example/lib/toolkit_helper:2.1",
              "process": "FLOW:PREP", "labels": {"site": "north"}},
        "b": {"hash": "b", "container": "registry.example/lib/toolkit_helper:2.1",
              "process": "FLOW:RUN", "labels": {"site": "south"}},
        "c": {"hash": "c", "container": "other-1.0", "process": "JOIN", "labels": {}},
    },
    "edges": [
        {"consumer": "a", "producer": "EXTERNAL", "filename": "/in/reference.dat"},
        {"consumer": "a", "producer": "EXTERNAL", "filename": "/in/reference.dat.idx"},
        {"consumer": "b", "producer": "a", "filename": "mid.dat",
         "labels": {"batch": "017"}},
        {"consumer": "c", "producer": "b", "filename": "out.dat"},
    ],
    "outputs": {"a": ["mid.dat"], "b": ["out.dat"], "c": []},
}

SHIPPED = Path(__file__).resolve().parent.parent / "clew" / "data" / "graph5.json"
MODEL = triage.V1["model"]


def answer(choice, confidence, model=MODEL):
    return {"model": model,
            "answers": {"trigger": {"type": "choice", "choice": choice,
                                    "confidence": confidence,
                                    "probabilities": {choice: 1.0}}}}


def sorted_as(choice, confidence):
    offered = triage.options(GRAPH)
    criteria = triage.request("n", offered, triage.V1)["questions"]["trigger"]["criteria"]
    checked = classifier.checked(answer(choice, confidence), criteria, MODEL)
    return triage.triage("n", offered, triage.V1, checked, "supplied")


class TestOptions(unittest.TestCase):
    def test_every_option_resolves_to_a_task(self):
        """An option nothing answers to would be a trigger that cannot be asked."""
        for graph in (GRAPH, core.load_graph(str(SHIPPED))):
            for option, trigger, _ in triage.options(graph):
                kind, value = triggers.parse(trigger)
                found = triggers.resolve(graph, kind, value)[trigger]
                self.assertTrue(found, f"{option} -> {trigger} reaches nothing")

    def test_a_joined_image_offers_each_tool(self):
        offered = {trigger for _, trigger, _ in triage.options(GRAPH)}
        self.assertLessEqual({"container:toolkit", "container:helper",
                              "container:other"}, offered)

    def test_a_companion_file_is_not_offered_beside_its_file(self):
        offered = {trigger for _, trigger, _ in triage.options(GRAPH)}
        self.assertIn("input:reference.dat", offered)
        self.assertNotIn("input:reference.dat.idx", offered)

    def test_labels_are_offered_under_their_own_key(self):
        offered = {trigger for _, trigger, _ in triage.options(GRAPH)}
        self.assertLessEqual({"site:north", "site:south", "batch:017"}, offered)

    def test_kinds_narrow_what_is_offered(self):
        offered = triage.options(GRAPH, kinds=("container",))
        self.assertEqual({t.split(":")[0] for _, t, _ in offered}, {"container"})

    def test_a_word_two_kinds_share_is_told_apart(self):
        graph = json.loads(json.dumps(GRAPH))
        graph["tasks"]["c"]["labels"] = {"site": "toolkit"}
        options = {option for option, _, _ in triage.options(graph)}
        self.assertLessEqual({"container toolkit", "site toolkit"}, options)

    def test_a_kind_with_many_values_is_offered_only_where_the_notice_names_one(self):
        graph = json.loads(json.dumps(GRAPH))
        for n in range(triage.NAMED_ONLY_ABOVE + 10):
            graph["tasks"][f"t{n}"] = {"hash": f"t{n}", "labels": {"lot": f"L{n}"}}
        notes = []
        offered = triage.options(graph, notice="lot L7 was recalled", notes=notes)
        lots = [trigger for _, trigger, _ in offered if trigger.startswith("lot:")]
        self.assertEqual(lots, ["lot:L7"])
        self.assertTrue(any("lot: 60 values" in note for note in notes), notes)
        # The small kinds are untouched.
        self.assertIn("container:toolkit", {t for _, t, _ in offered})

    def test_too_many_options_is_said_not_truncated(self):
        graph = json.loads(json.dumps(GRAPH))
        for key in range(8):
            for n in range(40):
                graph["tasks"][f"t{key}-{n}"] = {"hash": f"t{key}-{n}",
                                                 "labels": {f"key{key}": f"v{key}-{n}"}}
        with self.assertRaises(SystemExit) as stopped:
            triage.options(graph)
        self.assertIn("--kind", str(stopped.exception))

    def test_the_shipped_run_offers_its_twelve_tools(self):
        offered = triage.options(core.load_graph(str(SHIPPED)), kinds=("container",))
        self.assertEqual(len(offered), 12)


class ListedKind(Trigger):
    """A pipeline's own kind: ids a site knows, each entering at named tasks."""
    mode = REMOVE
    about = "unit named by the site"

    def __init__(self, entries):
        self.entries = entries

    def resolve(self, graph, value, args):
        return self.entries


class NeedsAFile(Trigger):
    def resolve(self, graph, value, args):
        raise SystemExit("--sheet is required")


def pipeline(**kinds):
    return SimpleNamespace(name="site", triggers=kinds, load_bearing_inputs=("reference.dat",))


class TestPipelineKinds(unittest.TestCase):
    def test_a_declared_kind_is_offered_with_what_it_names_and_where_it_enters(self):
        site = pipeline(unit=ListedKind({"U1": ["a"], "U2": ["b"]}))
        offered = {trigger: meaning for _, trigger, meaning in triage.options(GRAPH, adapter=site)}
        self.assertEqual(offered["unit:U1"], "unit named by the site, entering at PREP")
        self.assertIn("unit:U2", offered)

    def test_what_is_offered_resolves_through_the_lookup_impact_uses(self):
        site = pipeline(unit=ListedKind({"U1": ["a"]}))
        for _, trigger, _ in triage.options(GRAPH, adapter=site):
            kind, value = triggers_contract.parse(trigger)
            found = triggers_contract.lookup(site, kind, GRAPH).resolve(GRAPH, value, None)
            self.assertTrue(any(found.values()), trigger)

    def test_an_id_that_reaches_no_task_is_left_out_and_said(self):
        notes = []
        site = pipeline(unit=ListedKind({"U1": ["a"], "U9": []}))
        offered = {t for _, t, _ in triage.options(GRAPH, adapter=site, notes=notes)}
        self.assertNotIn("unit:U9", offered)
        self.assertTrue(any("1 of 2 reach no task" in note for note in notes), notes)

    def test_a_kind_that_cannot_list_its_values_is_said_not_dropped_silently(self):
        notes = []
        offered = triage.options(GRAPH, adapter=pipeline(unit=NeedsAFile()), notes=notes)
        self.assertFalse([t for _, t, _ in offered if t.startswith("unit:")])
        self.assertEqual(notes, ["unit: not offered. --sheet is required"])

    def test_a_declared_kind_shadows_the_engine_kind_of_the_same_name(self):
        site = pipeline(container=ListedKind({"U1": ["a"]}))
        offered = {t for _, t, _ in triage.options(GRAPH, adapter=site)}
        self.assertIn("container:U1", offered)
        self.assertNotIn("container:toolkit", offered)

    def test_an_input_the_pipeline_depends_on_is_marked(self):
        offered = {t: m for _, t, m in triage.options(GRAPH, adapter=pipeline())}
        self.assertIn("depends on", offered["input:reference.dat"])

    def test_the_record_names_the_pipeline_and_carries_the_notes(self):
        site = pipeline(unit=ListedKind({"U1": ["a"]}))
        offered = triage.options(GRAPH, adapter=site)
        answer = {"choice": "U1", "confidence": 0.9, "probabilities": {}, "model": MODEL}
        record = triage.triage("n", offered, triage.V1, answer, "supplied",
                               pipeline="site", notes=["a note"])
        self.assertEqual((record["pipeline"], record["notes"], record["trigger"]),
                         ("site", ["a note"], "unit:U1"))


class TestRemovalsMustBeNamed(unittest.TestCase):
    """An id that takes a source away is asked only when the notice writes it out."""

    def sorted_as(self, notice, choice, confidence=0.99):
        site = pipeline(unit=ListedKind({"U3": ["a"]}))
        offered = triage.options(GRAPH, adapter=site)
        answer = {"choice": choice, "confidence": confidence, "probabilities": {}, "model": MODEL}
        return triage.triage(notice, offered, triage.V1, answer, "jev", removes={"unit"})

    def test_a_removal_the_notice_names_is_asked(self):
        record = self.sorted_as("U3 withdrew", "U3")
        self.assertEqual((record["outcome"], record["trigger"]), ("ask", "unit:U3"))

    def test_a_confident_guess_at_a_removal_is_held(self):
        record = self.sorted_as("the third unit withdrew", "U3")
        self.assertEqual((record["outcome"], record["trigger"]), ("held", None))
        self.assertIn("does not name it", record["reason"])

    def test_a_described_tool_is_still_asked(self):
        record = self.sorted_as("the preparation toolkit is broken", "toolkit", 0.85)
        self.assertEqual(record["trigger"], "container:toolkit")

    def test_the_record_says_which_kinds_must_be_named(self):
        self.assertEqual(self.sorted_as("U3 withdrew", "U3")["named_only"], ["unit"])


class TestDismissalIsRefused(unittest.TestCase):
    """None of the options is only worth what the options covered."""

    NAMED = dict(GRAPH, tasks=dict(GRAPH["tasks"], a=dict(GRAPH["tasks"]["a"], name="PREP (unit_003)")))

    def sorted_as(self, notice, graph, **given):
        answer = {"choice": "none", "confidence": 0.99, "probabilities": {}, "model": MODEL}
        return triage.triage(notice, triage.options(graph), triage.V1, answer, "jev",
                             named=triage.named_in_run(notice, graph), **given)

    def test_an_id_the_run_contains_and_no_option_covers_is_held(self):
        record = self.sorted_as("unit_003 withdrew on 2 October", self.NAMED)
        self.assertEqual(record["outcome"], "held")
        self.assertEqual(record["named_in_run"], ["unit_003"])
        self.assertIn("Choose the pipeline's adapter", record["reason"])

    def test_a_notice_about_nothing_in_the_run_is_still_dismissed(self):
        record = self.sorted_as("gadget 2.7.11 corrupts its output, see ticket AB12", self.NAMED)
        self.assertEqual(record["outcome"], "dismissed")

    def test_plain_words_and_bare_versions_are_not_ids(self):
        self.assertEqual(triage.named_in_run("PREP version 2.1 of the step", self.NAMED), [])

    def test_an_id_inside_a_file_name_counts(self):
        graph = json.loads(json.dumps(GRAPH))
        graph["edges"][0]["filename"] = "/in/unit_003-L1_1.dat"
        self.assertEqual(triage.named_in_run("recall of unit_003", graph), ["unit_003"])

    def test_a_kind_that_could_not_be_offered_blocks_dismissal(self):
        record = self.sorted_as("something unrelated", GRAPH, unoffered=["unit"])
        self.assertEqual(record["outcome"], "held")
        self.assertIn("unit could not be offered", record["reason"])


class TestRequest(unittest.TestCase):
    def test_the_request_carries_the_pinned_model_and_none(self):
        asked = triage.request("a notice", triage.options(GRAPH), triage.V1)
        self.assertEqual(asked["model"], MODEL)
        self.assertEqual(asked["state"], {"notice": "a notice"})
        self.assertIn(classifier.NONE, asked["questions"]["trigger"]["criteria"])


class TestDeciding(unittest.TestCase):
    def test_a_confident_choice_is_asked(self):
        record = sorted_as("toolkit", 0.92)
        self.assertEqual(record["outcome"], triage.ASK)
        self.assertEqual(record["trigger"], "container:toolkit")
        self.assertIn("PREP", record["meaning"])

    def test_an_unsure_choice_is_held_with_no_trigger(self):
        record = sorted_as("toolkit", 0.54)
        self.assertEqual(record["outcome"], triage.HELD)
        self.assertIsNone(record["trigger"])

    def test_dismissing_needs_more_than_asking(self):
        """0.75 would ask a trigger and must not dismiss a notice."""
        self.assertEqual(sorted_as("toolkit", 0.75)["outcome"], triage.ASK)
        self.assertEqual(sorted_as("none", 0.75)["outcome"], triage.HELD)
        self.assertEqual(sorted_as("none", 0.98)["outcome"], triage.DISMISSED)

    def test_the_record_names_the_settings_and_the_question(self):
        record = sorted_as("toolkit", 0.92)
        self.assertEqual(record["settings"],
                         {"version": "v1", "hash": triage.fingerprint(triage.V1)})
        self.assertEqual(len(record["request_sha256"]), 64)
        self.assertEqual(record["model"], MODEL)


class TestByName(unittest.TestCase):
    WORDS = {"toolkit": "toolkit", "helper": "helper", "other": "other"}

    def decided(self, notice):
        return triage.decide(classifier.by_name(notice, self.WORDS), triage.V1)[0]

    def test_one_name_is_asked(self):
        self.assertEqual(self.decided("Toolkit 2.1 truncates indexes"), triage.ASK)

    def test_no_name_is_held_never_dismissed(self):
        self.assertEqual(self.decided("the preparation step is wrong"), triage.HELD)

    def test_two_names_are_held(self):
        self.assertEqual(self.decided("toolkit and helper both changed"), triage.HELD)

    def test_a_longer_hyphenated_name_is_another_thing(self):
        """toolkit-next is not toolkit, the way bwa-mem2 is not bwa."""
        self.assertEqual(self.decided("toolkit-next 2.3 changes its index format"), triage.HELD)
        self.assertEqual(self.decided("the new-toolkit fork is out"), triage.HELD)

    def test_a_name_inside_a_longer_word_is_not_a_match(self):
        self.assertEqual(self.decided("toolkits in general"), triage.HELD)


class TestAnswersFromElsewhere(unittest.TestCase):
    CRITERIA = {"toolkit": "", "none": ""}

    def refused(self, document):
        with self.assertRaises(classifier.Refused) as stopped:
            classifier.checked(document, self.CRITERIA, MODEL)
        return str(stopped.exception)

    def test_a_choice_that_was_not_offered_is_refused(self):
        self.assertIn("not one of the options", self.refused(answer("invented", 0.9)))

    def test_an_alias_is_refused(self):
        self.assertIn("alias", self.refused(answer("toolkit", 0.9, "jev-latest")))

    def test_another_build_is_refused(self):
        self.assertIn("settings name", self.refused(answer("toolkit", 0.9, "jev-9.9.9")))

    def test_an_answer_with_no_model_is_refused(self):
        document = answer("toolkit", 0.9)
        del document["model"]
        self.assertIn("names no model", self.refused(document))

    def test_a_confidence_that_is_not_a_number_is_refused(self):
        self.assertIn("confidence", self.refused(answer("toolkit", True)))

    def test_the_bare_answer_is_accepted_with_its_model(self):
        bare = dict(answer("toolkit", 0.9)["answers"]["trigger"], model=MODEL)
        self.assertEqual(classifier.checked(bare, self.CRITERIA, MODEL)["choice"], "toolkit")


class TestSettings(unittest.TestCase):
    def test_the_shipped_settings_are_valid(self):
        self.assertIs(triage.validate(triage.V1), triage.V1)

    def test_an_alias_model_is_rejected(self):
        with self.assertRaises(triage.InvalidSettings):
            triage.validate(dict(triage.V1, model="jev-latest"))

    def test_a_bar_outside_zero_to_one_is_rejected(self):
        with self.assertRaises(triage.InvalidSettings):
            triage.validate(dict(triage.V1, ask_at=1.5))

    def test_changed_wording_is_a_changed_hash(self):
        reworded = dict(triage.V1, instructions="Which one?")
        self.assertNotEqual(triage.fingerprint(reworded), triage.fingerprint(triage.V1))


class TestEvents(unittest.TestCase):
    def test_both_events_share_the_notice_hash(self):
        record = sorted_as("toolkit", 0.92)
        first, second = triage.received("n", "feed"), triage.triaged(record, 7)
        self.assertEqual(first["event_type"], "NoticeReceived")
        self.assertEqual(second["event_type"], "NoticeTriaged")
        self.assertEqual(first["subject"], second["subject"])
        self.assertEqual(first["body"], {"text": "n", "source": "feed"})

    def test_the_triage_event_points_back_and_leaves_the_text_out(self):
        body = triage.triaged(sorted_as("toolkit", 0.92), 7)["body"]
        self.assertEqual(body["received_seq"], 7)
        self.assertNotIn("notice", body)
        self.assertEqual(body["trigger"], "container:toolkit")


class TestCommand(unittest.TestCase):
    def run_triage(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = triage.main(list(argv))
        return code, out.getvalue()

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.graph = str(Path(self.dir.name) / "graph.json")
        Path(self.graph).write_text(json.dumps(GRAPH))

    def test_print_request_sends_nothing_and_exits_clean(self):
        code, out = self.run_triage("--graph", self.graph, "--print-request", "a notice")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["state"], {"notice": "a notice"})

    def test_a_supplied_answer_is_recorded_and_exits_by_outcome(self):
        supplied = Path(self.dir.name) / "answer.json"
        record = Path(self.dir.name) / "record.json"
        for choice, confidence, code in (("toolkit", 0.9, 0), ("toolkit", 0.3, 1),
                                         ("none", 0.99, 3)):
            supplied.write_text(json.dumps(answer(choice, confidence)))
            got, out = self.run_triage("--graph", self.graph, "--answer", str(supplied),
                                       "--json", str(record), "a notice")
            self.assertEqual(got, code, out)
            self.assertEqual(json.loads(record.read_text())["backend"], "supplied")

    def test_a_refused_answer_stops_the_command(self):
        supplied = Path(self.dir.name) / "answer.json"
        supplied.write_text(json.dumps(answer("invented", 0.9)))
        with self.assertRaises(SystemExit) as stopped:
            self.run_triage("--graph", self.graph, "--answer", str(supplied), "a notice")
        self.assertIn("triage refused", str(stopped.exception))

    def test_name_matching_prints_the_next_command(self):
        code, out = self.run_triage("--graph", self.graph, "--backend", "name",
                                    "toolkit 2.1 truncates indexes")
        self.assertEqual(code, 0)
        self.assertIn(f"clew impact --graph {self.graph} --trigger container:toolkit", out)

    def test_an_empty_notice_is_an_error(self):
        with self.assertRaises(SystemExit):
            self.run_triage("--graph", self.graph, "   ")


if __name__ == "__main__":
    unittest.main()
