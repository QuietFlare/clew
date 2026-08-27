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

from clew.core import evidence
from clew.core import eventlog
from clew.core import policy as policy_module

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
        "plan": items,
        "caveats": ["a stated limit"],
    }


class BundleTestCase(unittest.TestCase):
    def seal(self, plan=None, events=None, policy_document=None,
             log_head=None, previous=None, destination=None):
        policy_document = policy_document or policy_module.DEFAULT
        plan = plan if plan is not None else a_plan(policy_document)
        events = log_chain(3) if events is None else events
        if log_head is None:
            log_head = ({"seq": events[-1]["seq"], "hash": events[-1]["hash"]}
                        if events else {"seq": 0, "hash": eventlog.GENESIS})
        destination = destination or Path(self.tmp) / "bundle"
        return evidence.build(
            destination,
            {"plan.json": plan, "policy.json": policy_document,
             "events.json": events, "inputs.json": {}},
            log_head=log_head, previous_bundle=previous,
            coverage=plan.get("caveats", []))

    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = holder.name


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
        self.assertNotIn(evidence.MANIFEST, manifest["files"])
        self.assertNotIn(evidence.SIGNATURE, manifest["files"])

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
            (Path(self.tmp) / "bundle" / evidence.CRATE).read_text())
        self.assertEqual(crate["@context"],
                         "https://w3id.org/ro/crate/1.1/context")
        described = {e["@id"] for e in crate["@graph"]}
        self.assertIn("./", described)
        self.assertIn("plan.json", described)


class TestFileCheck(BundleTestCase):
    def test_an_intact_bundle_passes(self):
        manifest, _ = self.seal()
        check = evidence.verify_files(Path(self.tmp) / "bundle", manifest)
        self.assertTrue(check["ok"])

    def test_an_edited_file_is_caught(self):
        manifest, _ = self.seal()
        (Path(self.tmp) / "bundle" / "plan.json").write_text('{"plan": []}')
        check = evidence.verify_files(Path(self.tmp) / "bundle", manifest)
        self.assertFalse(check["ok"])
        self.assertIn("plan.json", check["detail"])

    def test_a_removed_file_is_caught(self):
        manifest, _ = self.seal()
        (Path(self.tmp) / "bundle" / "inputs.json").unlink()
        check = evidence.verify_files(Path(self.tmp) / "bundle", manifest)
        self.assertFalse(check["ok"])
        self.assertIn("missing", check["detail"])

    def test_an_unlisted_extra_file_is_caught(self):
        # Not harmless: a reader who opens the directory sees every file in
        # it, listed or not, so the manifest has to account for all of them.
        manifest, _ = self.seal()
        (Path(self.tmp) / "bundle" / "note.txt").write_text("trust me")
        check = evidence.verify_files(Path(self.tmp) / "bundle", manifest)
        self.assertFalse(check["ok"])
        self.assertIn("not listed", check["detail"])


class TestLogCheck(BundleTestCase):
    def test_an_intact_chain_ending_at_the_recorded_head_passes(self):
        events = log_chain(3)
        manifest, _ = self.seal(events=events)
        self.assertTrue(evidence.verify_log(events, manifest, eventlog)["ok"])

    def test_an_edited_entry_is_caught(self):
        events = log_chain(3)
        manifest, _ = self.seal(events=events)
        events[1]["actor"] = "someone-else"
        check = evidence.verify_log(events, manifest, eventlog)
        self.assertFalse(check["ok"])
        self.assertIn("seq 2", check["detail"])

    def test_entries_added_after_sealing_are_caught(self):
        # The bundle witnesses a head. Anything appended to the copy inside
        # the bundle no longer ends where the manifest says it ended.
        events = log_chain(3)
        manifest, _ = self.seal(events=events)
        events.append(log_entry(4, events[-1]["hash"]))
        check = evidence.verify_log(events, manifest, eventlog)
        self.assertFalse(check["ok"])

    def test_a_dropped_tail_is_caught_because_the_bundle_remembers(self):
        # This is the gap the log alone cannot close: a truncated chain is
        # internally consistent, and only an outside witness notices.
        events = log_chain(4)
        manifest, _ = self.seal(events=events)
        check = evidence.verify_log(events[:3], manifest, eventlog)
        self.assertFalse(check["ok"])
        self.assertIn("head", check["detail"])

    def test_a_bundle_claiming_a_head_with_no_entries_is_caught(self):
        manifest, _ = self.seal(events=log_chain(2))
        check = evidence.verify_log([], manifest, eventlog)
        self.assertFalse(check["ok"])

    def test_an_empty_log_is_legitimately_empty(self):
        manifest, _ = self.seal(events=[])
        self.assertTrue(evidence.verify_log([], manifest, eventlog)["ok"])


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
        check = evidence.verify_against_log(manifest, self.a_log(events))
        self.assertTrue(check["ok"])

    def test_a_truncated_log_is_caught(self):
        events = log_chain(3)
        manifest, _ = self.seal(events=events)
        # The log alone still verifies at this point; that is the whole point.
        self.assertTrue(eventlog.verify_entries(events[:1])["ok"])
        check = evidence.verify_against_log(manifest, self.a_log(events[:1]))
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
        check = evidence.verify_against_log(manifest, self.a_log(forged))
        self.assertFalse(check["ok"])
        self.assertIn("rewritten", check["detail"])

    def test_growth_since_sealing_is_fine(self):
        # A bundle witnesses a head, not the end of history. Later entries
        # are normal operation, not tampering.
        events = log_chain(3)
        manifest, _ = self.seal(events=events)
        events.append(log_entry(4, events[-1]["hash"]))
        self.assertTrue(
            evidence.verify_against_log(manifest, self.a_log(events))["ok"])

    def test_a_bundle_with_no_anchor_witnesses_nothing(self):
        manifest, _ = self.seal(events=[])
        check = evidence.verify_against_log(manifest, self.a_log([]))
        self.assertIsNone(check["ok"])
        self.assertIn("cannot detect a truncation", check["detail"])


class TestPolicyCheck(BundleTestCase):
    def test_matching_policy_passes(self):
        plan = a_plan()
        check = evidence.verify_policy(plan, policy_module.DEFAULT)
        self.assertTrue(check["ok"])

    def test_a_swapped_policy_is_caught(self):
        # The plan and the table it was decided under cannot drift apart.
        plan = a_plan(policy_module.V2)
        check = evidence.verify_policy(plan, policy_module.V1)
        self.assertFalse(check["ok"])
        self.assertIn("hashes to", check["detail"])


class TestReplay(BundleTestCase):
    """The check most evidence packages do not have."""

    def test_a_faithful_plan_replays(self):
        check = evidence.verify_replay(a_plan(), policy_module.DEFAULT)
        self.assertTrue(check["ok"])

    def test_a_changed_verdict_is_caught(self):
        plan = a_plan()
        plan["plan"][0]["action"] = "ALREADY_GONE"
        check = evidence.verify_replay(plan, policy_module.DEFAULT)
        self.assertFalse(check["ok"])
        self.assertIn("t1", check["detail"])

    def test_a_changed_fact_is_caught(self):
        # Editing the input rather than the output does not help: the verdict
        # then no longer follows from the facts as stated.
        plan = a_plan()
        plan["plan"][1]["storage"] = "WRITABLE"
        self.assertFalse(
            evidence.verify_replay(plan, policy_module.DEFAULT)["ok"])

    def test_a_decorative_rule_citation_is_caught(self):
        plan = a_plan()
        plan["plan"][0]["rule"] = "R1"
        check = evidence.verify_replay(plan, policy_module.DEFAULT)
        self.assertFalse(check["ok"])
        self.assertIn("rule", check["detail"])

    def test_replaying_under_the_wrong_policy_is_caught(self):
        # A v1 plan does not reproduce under v2. Bundling today's table with
        # yesterday's plan would produce a bundle that passes for the wrong
        # reason, which is worse than no bundle.
        plan = a_plan(policy_module.V1)
        self.assertFalse(evidence.verify_replay(plan, policy_module.V2)["ok"])


class TestEndToEnd(BundleTestCase):
    """Through the CLI, with stock Python and no database."""

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "clew.evidence", *args],
            capture_output=True, text=True)

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
        manifest = json.loads((out / evidence.MANIFEST).read_text())
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
        manifest = json.loads((out / evidence.MANIFEST).read_text())
        manifest["files"]["plan.json"] = evidence.sha256_file(out / "plan.json")
        (out / evidence.MANIFEST).write_text(
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


if __name__ == "__main__":
    unittest.main()
