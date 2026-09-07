"""
Evidence bundles: sealing, and the four checks that make one worth having.

The tests that matter here are the forgeries. A bundle that verifies when
nothing is wrong is unremarkable. What has to hold is that a bundle someone
has quietly improved cannot pass — including the careful forgery, where the
manifest is rebuilt so every hash matches and only the conclusion changed.
That one is caught by replay, which is the check most evidence packages do
not have.

None of this needs a database. That is deliberate and it is the point: an
auditor holding a bundle is exactly the person who has no credentials.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.ledger import bundle
from clew.ledger import eventlog
from clew.ledger import policy as policy_module

ROOT = Path(__file__).resolve().parent.parent
T0 = "2026-01-01T00:00:00+00:00"


def log_entry(seq, prev_hash, subject="s"):
    fields = {
        "seq": seq, "effective_from": T0, "recorded_at": T0,
        "actor": "tester", "event_type": "Thing", "subject": subject,
        "body": eventlog.canonical({"i": seq}), "prev_hash": prev_hash,
    }
    fields["hash"] = eventlog.event_hash(fields)
    return fields


def log_chain(n):
    entries, prev = [], eventlog.GENESIS
    for i in range(1, n + 1):
        entries.append(log_entry(i, prev))
        prev = entries[-1]["hash"]
    return entries


def a_plan(policy_document=None):
    """A small plan whose verdicts really do follow from the policy."""
    policy_document = policy_document or policy_module.DEFAULT
    facts = [
        ("t1", "REGENERABLE", "WRITABLE", False, False),
        ("t2", "REGENERABLE", "DESTROYED", False, False),
        ("t3", "REGENERABLE", "WRITABLE", True, False),
        ("t4", "IRREDUCIBLE", "WRITABLE", False, True),
        # Published AND destroyed: the one combination v1 and v2 disagree on,
        # so a plan without it would replay happily under either table and
        # the wrong-policy check would pass for the wrong reason.
        ("t5", "REGENERABLE", "DESTROYED", False, True),
    ]
    items = []
    for task, klass, storage, exclusive, terminal in facts:
        decision = policy_module.decide(klass, storage=storage,
                                        exclusive=exclusive, terminal=terminal,
                                        policy=policy_document)
        items.append({
            "task": task, "process": "P", "name": task,
            "action": decision["action"], "rule": decision["rule"],
            "because": decision["because"], "contribution": klass,
            "storage": storage, "exclusive": exclusive, "terminal": terminal,
            "reason": "test",
        })
    return {
        "clew_plan_version": 1,
        **policy_module.identify(policy_document),
        "trigger": "test:trigger",
        "tasks_total": 10,
        "tasks_affected": len(items),
        "actions": action_counts(items),
        "plan": items,
        "caveats": ["a stated limit"],
    }


def action_counts(items):
    counts = {}
    for item in items:
        key = item["action"] or policy_module.UNDETERMINED
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


class BundleTestCase(unittest.TestCase):
    def seal(self, plan=None, events=None, policy_document=None,
             log_head=None, previous=None, previous_head=None, since=0,
             destination=None, force=False):
        policy_document = policy_document or policy_module.DEFAULT
        plan = plan if plan is not None else a_plan(policy_document)
        events = log_chain(3) if events is None else events
        if log_head is None:
            log_head = ({"seq": events[-1]["seq"], "hash": events[-1]["hash"]}
                        if events else {"seq": 0, "hash": eventlog.GENESIS})
        destination = destination or Path(self.tmp) / "bundle"
        return bundle.build(
            destination,
            {"plan.json": plan, "policy.json": policy_document,
             "events.json": events, "inputs.json": {}},
            log_head=log_head, previous_bundle=previous,
            previous_log_head=previous_head, since=since,
            coverage=plan.get("caveats", []), force=force)

    def rewrite_manifest(self, directory, change):
        """Edit the manifest the way a careful forger would: hashes intact."""
        path = Path(directory) / bundle.MANIFEST
        manifest = json.loads(path.read_text())
        change(manifest)
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        return manifest

    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = holder.name

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "clew.ledger.evidence", *args],
            capture_output=True, text=True)


class TestSealing(BundleTestCase):
    def test_a_bundle_contains_what_it_claims(self):
        manifest, _ = self.seal()
        self.assertEqual(
            set(manifest["files"]),
            {"plan.json", "policy.json", "events.json", "inputs.json",
             "ro-crate-metadata.json", "HOW-TO-VERIFY.txt"})

    def test_the_manifest_never_lists_itself_or_its_signature(self):
        # It cannot hash itself, and the signature is made over it afterwards.
        manifest, _ = self.seal()
        self.assertNotIn(bundle.MANIFEST, manifest["files"])
        self.assertNotIn(bundle.SIGNATURE, manifest["files"])

    def test_sealing_is_clock_free_and_reproducible(self):
        # A timestamp inside would change the hash on every build and quietly
        # destroy the reproducibility claim. Time lives in the log instead.
        _, first = self.seal(destination=Path(self.tmp) / "a")
        _, second = self.seal(destination=Path(self.tmp) / "b")
        self.assertEqual(first, second)

    def test_any_change_to_any_document_changes_the_bundle_hash(self):
        _, original = self.seal(destination=Path(self.tmp) / "a")
        altered = a_plan()
        altered["trigger"] = "something:else"
        _, changed = self.seal(plan=altered, destination=Path(self.tmp) / "b")
        self.assertNotEqual(original, changed)

    def test_bundles_chain_to_one_another(self):
        _, first = self.seal(destination=Path(self.tmp) / "a")
        manifest, _ = self.seal(destination=Path(self.tmp) / "b",
                                previous=first)
        self.assertEqual(manifest["anchors"]["previous_bundle"], first)

    def test_the_bundle_is_also_an_ro_crate(self):
        # Adopted rather than invented: one fewer format for a reader to
        # learn, and it survives tooling that knows nothing about Clew.
        self.seal()
        crate = json.loads(
            (Path(self.tmp) / "bundle" / bundle.CRATE).read_text())
        self.assertEqual(crate["@context"],
                         "https://w3id.org/ro/crate/1.1/context")
        described = {e["@id"] for e in crate["@graph"]}
        self.assertIn("./", described)
        self.assertIn("plan.json", described)


class TestFileCheck(BundleTestCase):
    def test_an_intact_bundle_passes(self):
        manifest, _ = self.seal()
        check = bundle.verify_files(Path(self.tmp) / "bundle", manifest)
        self.assertTrue(check["ok"])

    def test_an_edited_file_is_caught(self):
        manifest, _ = self.seal()
        (Path(self.tmp) / "bundle" / "plan.json").write_text('{"plan": []}')
        check = bundle.verify_files(Path(self.tmp) / "bundle", manifest)
        self.assertFalse(check["ok"])
        self.assertIn("plan.json", check["detail"])

    def test_a_removed_file_is_caught(self):
        manifest, _ = self.seal()
        (Path(self.tmp) / "bundle" / "inputs.json").unlink()
        check = bundle.verify_files(Path(self.tmp) / "bundle", manifest)
        self.assertFalse(check["ok"])
        self.assertIn("missing", check["detail"])

    def test_an_unlisted_extra_file_is_caught(self):
        # Not harmless: a reader who opens the directory sees every file in
        # it, listed or not, so the manifest has to account for all of them.
        manifest, _ = self.seal()
        (Path(self.tmp) / "bundle" / "note.txt").write_text("trust me")
        check = bundle.verify_files(Path(self.tmp) / "bundle", manifest)
        self.assertFalse(check["ok"])
        self.assertIn("not listed", check["detail"])


class TestLogCheck(BundleTestCase):
    def test_an_intact_chain_ending_at_the_recorded_head_passes(self):
        events = log_chain(3)
        manifest, _ = self.seal(events=events)
        self.assertTrue(bundle.verify_log(events, manifest, eventlog)["ok"])

    def test_an_edited_entry_is_caught(self):
        events = log_chain(3)
        manifest, _ = self.seal(events=events)
        events[1]["actor"] = "someone-else"
        check = bundle.verify_log(events, manifest, eventlog)
        self.assertFalse(check["ok"])
        self.assertIn("seq 2", check["detail"])

    def test_entries_added_after_sealing_are_caught(self):
        # The bundle witnesses a head. Anything appended to the copy inside
        # the bundle no longer ends where the manifest says it ended.
        events = log_chain(3)
        manifest, _ = self.seal(events=events)
        events.append(log_entry(4, events[-1]["hash"]))
        check = bundle.verify_log(events, manifest, eventlog)
        self.assertFalse(check["ok"])

    def test_a_dropped_tail_is_caught_because_the_bundle_remembers(self):
        # This is the gap the log alone cannot close: a truncated chain is
        # internally consistent, and only an outside witness notices.
        events = log_chain(4)
        manifest, _ = self.seal(events=events)
        check = bundle.verify_log(events[:3], manifest, eventlog)
        self.assertFalse(check["ok"])
        self.assertIn("head", check["detail"])

    def test_a_bundle_claiming_a_head_with_no_entries_is_caught(self):
        manifest, _ = self.seal(events=log_chain(2))
        check = bundle.verify_log([], manifest, eventlog)
        self.assertFalse(check["ok"])

    def test_an_empty_log_is_legitimately_empty(self):
        manifest, _ = self.seal(events=[])
        self.assertTrue(bundle.verify_log([], manifest, eventlog)["ok"])


class TestWitness(BundleTestCase):
    """
    The check that closes the log's open gap.

    A truncated chain is internally consistent, so verify() on the log alone
    passes — nothing inside a database can notice something that is no longer
    in it. A bundle notices, because it left the building carrying the head
    it saw.
    """

    def a_log(self, entries):
        by_seq = {e["seq"]: e["hash"] for e in entries}
        return lambda seq: by_seq.get(seq)

    def test_an_untouched_log_matches_its_bundle(self):
        events = log_chain(3)
        manifest, _ = self.seal(events=events)
        check = bundle.verify_against_log(manifest, self.a_log(events))
        self.assertTrue(check["ok"])

    def test_a_truncated_log_is_caught(self):
        events = log_chain(3)
        manifest, _ = self.seal(events=events)
        # The log alone still verifies at this point; that is the whole point.
        self.assertTrue(eventlog.verify_entries(events[:1])["ok"])
        check = bundle.verify_against_log(manifest, self.a_log(events[:1]))
        self.assertFalse(check["ok"])
        self.assertIn("removed from the end", check["detail"])

    def test_a_rewritten_log_is_caught(self):
        events = log_chain(3)
        manifest, _ = self.seal(events=events)
        forged = [log_entry(1, eventlog.GENESIS, subject="forged")]
        for i in (2, 3):
            forged.append(log_entry(i, forged[-1]["hash"], subject="forged"))
        # Internally consistent, and entirely different.
        self.assertTrue(eventlog.verify_entries(forged)["ok"])
        check = bundle.verify_against_log(manifest, self.a_log(forged))
        self.assertFalse(check["ok"])
        self.assertIn("rewritten", check["detail"])

    def test_growth_since_sealing_is_fine(self):
        # A bundle witnesses a head, not the end of history. Later entries
        # are normal operation, not tampering.
        events = log_chain(3)
        manifest, _ = self.seal(events=events)
        events.append(log_entry(4, events[-1]["hash"]))
        self.assertTrue(
            bundle.verify_against_log(manifest, self.a_log(events))["ok"])

    def test_a_bundle_with_no_anchor_witnesses_nothing(self):
        manifest, _ = self.seal(events=[])
        check = bundle.verify_against_log(manifest, self.a_log([]))
        self.assertIsNone(check["ok"])
        self.assertIn("cannot detect a truncation", check["detail"])


class TestPolicyCheck(BundleTestCase):
    def test_matching_policy_passes(self):
        plan = a_plan()
        check = bundle.verify_policy(plan, policy_module.DEFAULT)
        self.assertTrue(check["ok"])

    def test_a_swapped_policy_is_caught(self):
        # The plan and the table it was decided under cannot drift apart.
        plan = a_plan(policy_module.V2)
        check = bundle.verify_policy(plan, policy_module.V1)
        self.assertFalse(check["ok"])
        self.assertIn("hashes to", check["detail"])


class TestReplay(BundleTestCase):
    """The check most evidence packages do not have."""

    def test_a_faithful_plan_replays(self):
        check = bundle.verify_replay(a_plan(), policy_module.DEFAULT)
        self.assertTrue(check["ok"])

    def test_a_changed_verdict_is_caught(self):
        plan = a_plan()
        plan["plan"][0]["action"] = "ALREADY_GONE"
        check = bundle.verify_replay(plan, policy_module.DEFAULT)
        self.assertFalse(check["ok"])
        self.assertIn("t1", check["detail"])

    def test_a_changed_fact_is_caught(self):
        # Editing the input rather than the output does not help: the verdict
        # then no longer follows from the facts as stated.
        plan = a_plan()
        plan["plan"][1]["storage"] = "WRITABLE"
        self.assertFalse(
            bundle.verify_replay(plan, policy_module.DEFAULT)["ok"])

    def test_a_decorative_rule_citation_is_caught(self):
        plan = a_plan()
        plan["plan"][0]["rule"] = "R1"
        check = bundle.verify_replay(plan, policy_module.DEFAULT)
        self.assertFalse(check["ok"])
        self.assertIn("rule", check["detail"])

    def test_replaying_under_the_wrong_policy_is_caught(self):
        # A v1 plan does not reproduce under v2. Bundling today's table with
        # yesterday's plan would produce a bundle that passes for the wrong
        # reason, which is worse than no bundle.
        plan = a_plan(policy_module.V1)
        self.assertFalse(bundle.verify_replay(plan, policy_module.V2)["ok"])


class TestEndToEnd(BundleTestCase):
    """Through the CLI, with stock Python and no database."""

    def test_build_then_verify(self):
        plan_path = Path(self.tmp) / "plan.json"
        plan_path.write_text(json.dumps(a_plan()))
        out = Path(self.tmp) / "b"

        built = self.run_cli("build", "--out", str(out),
                             "--plan", str(plan_path))
        self.assertEqual(built.returncode, 0, built.stderr)

        checked = self.run_cli("verify", str(out))
        self.assertEqual(checked.returncode, 0, checked.stdout)
        for check in ("files", "log", "policy", "replay"):
            self.assertIn(check, checked.stdout)

    def test_a_bundle_with_no_log_says_so_in_its_coverage(self):
        # Never claim completeness. A bundle anchored to no log head cannot
        # detect a later truncation of anything, and must say that.
        plan_path = Path(self.tmp) / "plan.json"
        plan_path.write_text(json.dumps(a_plan()))
        out = Path(self.tmp) / "b"
        self.run_cli("build", "--out", str(out), "--plan", str(plan_path))
        manifest = json.loads((out / bundle.MANIFEST).read_text())
        self.assertTrue(any("no event log" in note
                            for note in manifest["coverage"]))

    def test_a_careful_forgery_still_fails(self):
        # The manifest is rebuilt so every hash matches. Only the conclusion
        # changed. Replay is what catches it.
        plan_path = Path(self.tmp) / "plan.json"
        plan_path.write_text(json.dumps(a_plan()))
        out = Path(self.tmp) / "b"
        self.run_cli("build", "--out", str(out), "--plan", str(plan_path))

        plan = json.loads((out / "plan.json").read_text())
        plan["plan"][0]["action"] = "ALREADY_GONE"
        (out / "plan.json").write_text(json.dumps(plan, indent=2,
                                                  sort_keys=True) + "\n")
        manifest = json.loads((out / bundle.MANIFEST).read_text())
        manifest["files"]["plan.json"] = bundle.sha256_file(out / "plan.json")
        (out / bundle.MANIFEST).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n")

        checked = self.run_cli("verify", str(out))
        self.assertEqual(checked.returncode, 1)
        self.assertIn("ok   files", checked.stdout)
        self.assertIn("FAIL replay", checked.stdout)

    def test_sealing_a_plan_under_a_policy_it_did_not_use_is_refused(self):
        plan = a_plan(policy_module.V1)
        plan_path = Path(self.tmp) / "plan.json"
        plan_path.write_text(json.dumps(plan))
        built = self.run_cli("build", "--out", str(Path(self.tmp) / "b"),
                             "--plan", str(plan_path), "--policy", "v2")
        self.assertNotEqual(built.returncode, 0)
        self.assertIn("policy mismatch", built.stderr)


class TestChainAnchoring(BundleTestCase):
    """
    The chain is checked from where the manifest says it starts, never
    from the first entry's own prev_hash. A chain anchored to itself
    verifies whatever it was forged to say.
    """

    def test_a_chain_forged_from_a_non_genesis_start_is_caught(self):
        forged = [log_entry(1, "f" * 64)]
        for i in (2, 3):
            forged.append(log_entry(i, forged[-1]["hash"]))
        manifest, _ = self.seal(events=forged)
        check = bundle.verify_log(forged, manifest, eventlog)
        self.assertFalse(check["ok"])
        self.assertIn("seq 1", check["detail"])

    def test_a_window_with_no_recorded_start_is_read_as_genesis(self):
        # A version 1 manifest records no start. Entries 40-42 cannot
        # chain from genesis, so the window cannot pass on its own say-so.
        events = log_chain(42)[39:]
        manifest, _ = self.seal(events=events)
        del manifest["anchors"]["since"]
        manifest["clew_bundle_version"] = 1
        self.assertFalse(bundle.verify_log(events, manifest, eventlog)["ok"])

    def test_a_window_verifies_against_the_previous_bundles_head(self):
        events = log_chain(6)
        first, first_hash = self.seal(events=events[:3],
                                      destination=Path(self.tmp) / "a")
        manifest, _ = self.seal(events=events[3:], since=3,
                                previous=first_hash,
                                previous_head=first["anchors"]["log_head"],
                                destination=Path(self.tmp) / "b")
        self.assertEqual(manifest["anchors"]["since"],
                         {"seq": 3, "hash": events[2]["hash"]})
        self.assertEqual(manifest["anchors"]["previous_log_head"],
                         {"seq": 3, "hash": events[2]["hash"]})
        check = bundle.verify_log(events[3:], manifest, eventlog)
        self.assertTrue(check["ok"], check["detail"])

    def test_a_window_from_a_different_log_is_caught(self):
        ours = log_chain(6)
        first, first_hash = self.seal(events=ours[:3],
                                      destination=Path(self.tmp) / "a")
        theirs = [log_entry(4, "e" * 64, subject="other")]
        for i in (5, 6):
            theirs.append(log_entry(i, theirs[-1]["hash"], subject="other"))
        manifest, _ = self.seal(events=theirs, since=3, previous=first_hash,
                                previous_head=first["anchors"]["log_head"],
                                destination=Path(self.tmp) / "b")
        check = bundle.verify_log(theirs, manifest, eventlog)
        self.assertFalse(check["ok"])
        self.assertIn("seq 4", check["detail"])

    def test_a_start_that_disagrees_with_the_previous_head_is_caught(self):
        events = log_chain(6)
        first, first_hash = self.seal(events=events[:3],
                                      destination=Path(self.tmp) / "a")
        self.seal(events=events[3:], since=3, previous=first_hash,
                  previous_head=first["anchors"]["log_head"],
                  destination=Path(self.tmp) / "b")
        manifest = self.rewrite_manifest(
            Path(self.tmp) / "b",
            lambda m: m["anchors"]["since"].update(hash="c" * 64))
        check = bundle.verify_log(events[3:], manifest, eventlog)
        self.assertFalse(check["ok"])
        self.assertIn("previous bundle", check["detail"])

    def test_a_window_cannot_be_built_without_the_bundle_it_continues(self):
        events = log_chain(6)
        with self.assertRaises(ValueError):
            self.seal(events=events[3:], since=3)
        with self.assertRaises(ValueError):
            self.seal(events=events[3:], since=3, previous="a" * 64,
                      previous_head={"seq": 2, "hash": events[1]["hash"]})

    def test_a_window_where_the_log_did_not_grow_is_fine(self):
        events = log_chain(3)
        first, first_hash = self.seal(events=events,
                                      destination=Path(self.tmp) / "a")
        manifest, _ = self.seal(events=[], since=3, previous=first_hash,
                                previous_head=first["anchors"]["log_head"],
                                log_head=first["anchors"]["log_head"],
                                destination=Path(self.tmp) / "b")
        self.assertTrue(bundle.verify_log([], manifest, eventlog)["ok"])


class TestDirectoryHygiene(BundleTestCase):
    def test_a_subdirectory_is_caught(self):
        # The easiest place to put something a reader will find and the
        # manifest never covered.
        manifest, _ = self.seal()
        (Path(self.tmp) / "bundle" / "sub").mkdir()
        (Path(self.tmp) / "bundle" / "sub" / "hidden.txt").write_text("x")
        check = bundle.verify_files(Path(self.tmp) / "bundle", manifest)
        self.assertFalse(check["ok"])
        self.assertIn("sub", check["detail"])

    def test_a_manifest_naming_a_path_outside_the_bundle_is_caught(self):
        outside = Path(self.tmp) / "outside.json"
        outside.write_text("{}")
        manifest, _ = self.seal()
        for name in ("../outside.json", "a/b.json", "a\\b.json", ".."):
            forged = dict(manifest, files=dict(
                manifest["files"], **{name: bundle.sha256_file(outside)}))
            check = bundle.verify_files(Path(self.tmp) / "bundle", forged)
            self.assertFalse(check["ok"], name)
            self.assertIn("outside the bundle", check["detail"])

    def test_the_store_skips_a_bundle_naming_a_path_outside_itself(self):
        from clew.ledger import bundlestore
        self.seal()
        self.rewrite_manifest(
            Path(self.tmp) / "bundle",
            lambda m: m["files"].update({"../plan.json": "0" * 64}))
        bundles, _, _ = bundlestore.load_store(self.tmp)
        self.assertEqual(bundles, [])

    def test_a_non_empty_destination_is_refused_without_force(self):
        target = Path(self.tmp) / "bundle"
        target.mkdir()
        (target / "leftover.txt").write_text("from last time")
        with self.assertRaises(FileExistsError):
            self.seal(destination=target)
        self.assertTrue((target / "leftover.txt").exists())

    def test_force_replaces_the_contents(self):
        target = Path(self.tmp) / "bundle"
        target.mkdir()
        (target / "leftover.txt").write_text("from last time")
        (target / "old").mkdir()
        manifest, _ = self.seal(destination=target, force=True)
        self.assertFalse((target / "leftover.txt").exists())
        self.assertFalse((target / "old").exists())
        self.assertTrue(bundle.verify_files(target, manifest)["ok"])


class TestReplayCoversTheWholePlan(BundleTestCase):
    def test_a_narrowed_possible_map_is_caught(self):
        # An undetermined item's candidates are its whole content. "One of
        # three" quietly becoming "one of one" reads as settled.
        decision = policy_module.decide("REGENERABLE", storage=None,
                                        exclusive=True)
        plan = a_plan()
        plan["plan"].append({
            "task": "t6", "process": "P", "name": "t6", "action": None,
            "rule": None, "because": decision["because"],
            "possible": {"ALREADY_GONE": "R1"},
            "contribution": "REGENERABLE", "storage": None,
            "exclusive": True, "terminal": False, "reason": "test"})
        plan["tasks_affected"] = 6
        plan["actions"] = action_counts(plan["plan"])
        check = bundle.verify_replay(plan, policy_module.DEFAULT)
        self.assertFalse(check["ok"])
        self.assertIn("t6", check["detail"])
        self.assertIn("possible", check["detail"])

    def test_a_null_terminal_cannot_replay_to_a_settled_verdict(self):
        plan = a_plan()
        plan["plan"][0].update(terminal=None, storage=None,
                               action="NOTIFY_ONLY", rule="R2")
        check = bundle.verify_replay(plan, policy_module.DEFAULT)
        self.assertFalse(check["ok"])
        self.assertIn("t1", check["detail"])

    def test_an_impossible_fact_value_is_caught(self):
        plan = a_plan()
        plan["plan"][0]["storage"] = "writable"
        check = bundle.verify_replay(plan, policy_module.DEFAULT)
        self.assertFalse(check["ok"])
        self.assertIn("not a possible value", check["detail"])

    def test_a_wrong_tasks_affected_is_caught(self):
        plan = a_plan()
        plan["tasks_affected"] = 1
        check = bundle.verify_replay(plan, policy_module.DEFAULT)
        self.assertFalse(check["ok"])
        self.assertIn("tasks_affected", check["detail"])

    def test_wrong_action_counts_are_caught(self):
        plan = a_plan()
        plan["actions"] = {"ALREADY_GONE": 5}
        check = bundle.verify_replay(plan, policy_module.DEFAULT)
        self.assertFalse(check["ok"])
        self.assertIn("actions", check["detail"])

    def test_missing_counts_are_caught(self):
        plan = a_plan()
        del plan["actions"]
        self.assertFalse(bundle.verify_replay(plan, policy_module.DEFAULT)["ok"])


class TestEndToEndForgeries(BundleTestCase):
    def write_plan(self):
        plan_path = Path(self.tmp) / "plan.json"
        plan_path.write_text(json.dumps(a_plan()))
        return plan_path

    def test_a_log_head_with_no_entries_file_fails(self):
        out = Path(self.tmp) / "b"
        built = self.run_cli("build", "--out", str(out),
                             "--plan", str(self.write_plan()))
        self.assertEqual(built.returncode, 0, built.stderr)

        # Drop the entries, claim a head anyway, and rebuild every hash.
        (out / "events.json").unlink()
        crate = json.loads((out / bundle.CRATE).read_text())
        crate["@graph"] = [e for e in crate["@graph"]
                           if e["@id"] != "events.json"]
        (out / bundle.CRATE).write_text(json.dumps(crate))
        manifest = json.loads((out / bundle.MANIFEST).read_text())
        del manifest["files"]["events.json"]
        manifest["files"][bundle.CRATE] = bundle.sha256_file(out / bundle.CRATE)
        manifest["anchors"]["log_head"] = {"seq": 57, "hash": "ab" * 32}
        (out / bundle.MANIFEST).write_text(json.dumps(manifest))

        checked = self.run_cli("verify", str(out))
        self.assertEqual(checked.returncode, 1)
        self.assertIn("ok   files", checked.stdout)
        self.assertIn("FAIL log", checked.stdout)

    def test_a_non_empty_out_is_refused_and_force_allows_it(self):
        plan_path = self.write_plan()
        out = Path(self.tmp) / "b"
        out.mkdir()
        (out / "notes.txt").write_text("stale")
        refused = self.run_cli("build", "--out", str(out),
                               "--plan", str(plan_path))
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("not empty", refused.stderr)
        forced = self.run_cli("build", "--out", str(out),
                              "--plan", str(plan_path), "--force")
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertFalse((out / "notes.txt").exists())
        self.assertEqual(self.run_cli("verify", str(out)).returncode, 0)

    def test_inputs_are_recorded_by_content_not_by_path(self):
        source = Path(self.tmp) / "graph.json"
        source.write_text("{}")
        nested = Path(self.tmp) / "elsewhere"
        nested.mkdir()
        copy = nested / "graph.json"
        copy.write_text("{}")
        plan_path = self.write_plan()

        hashes = []
        for name, given in (("one", str(source)), ("two", str(copy))):
            out = Path(self.tmp) / name
            built = self.run_cli("build", "--out", str(out), "--plan",
                                 str(plan_path), "--input", given)
            self.assertEqual(built.returncode, 0, built.stderr)
            manifest = json.loads((out / bundle.MANIFEST).read_text())
            hashes.append(bundle.bundle_hash(manifest))
        self.assertEqual(hashes[0], hashes[1])

        digest = bundle.sha256_file(source)
        inputs = json.loads((Path(self.tmp) / "one" / "inputs.json").read_text())
        self.assertEqual(list(inputs), [digest])
        self.assertEqual(inputs[digest]["name"], "graph.json")
        self.assertNotIn("path", inputs[digest])

    def test_the_same_content_under_two_names_is_refused(self):
        a = Path(self.tmp) / "a.csv"
        b = Path(self.tmp) / "b.csv"
        a.write_text("x")
        b.write_text("x")
        built = self.run_cli("build", "--out", str(Path(self.tmp) / "o"),
                             "--plan", str(self.write_plan()),
                             "--input", str(a), "--input", str(b))
        self.assertNotEqual(built.returncode, 0)
        self.assertIn("same content", built.stderr)

    def test_since_without_previous_is_refused(self):
        built = self.run_cli("build", "--out", str(Path(self.tmp) / "o"),
                             "--plan", str(self.write_plan()), "--since", "3")
        self.assertNotEqual(built.returncode, 0)
        self.assertIn("--previous", built.stderr)


if __name__ == "__main__":
    unittest.main()


class TestPreviousBundle(BundleTestCase):
    """--previous must name a sealed bundle, and say so when it does not."""

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "clew.ledger.evidence", *args],
            capture_output=True, text=True)

    def test_a_directory_with_no_manifest_is_refused_with_a_message(self):
        plan_path = Path(self.tmp) / "plan.json"
        plan_path.write_text(json.dumps(a_plan()))
        empty = Path(self.tmp) / "not-a-bundle"
        empty.mkdir()
        result = self.run_cli("build", "--out", str(Path(self.tmp) / "b"),
                              "--plan", str(plan_path),
                              "--previous", str(empty))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("not a sealed bundle", result.stderr)
