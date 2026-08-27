"""
The versioned remediation table.

Two jobs here. The first is the decision table itself, tested exhaustively —
these assertions moved over from test_contribution.py unchanged when the
table became data, which is the point: turning an if-ladder into rules had to
change nothing about what Clew decides.

The second is everything versioning buys: a hash that changes when anything
changes, validation that refuses a policy rather than quietly mis-deciding
under it, and the ability to replay an old plan under an old table.
"""

import json
import sys
import tempfile
import unittest
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.core import contribution as c
from clew.core import policy as p

# Every combination the engine can present, including classes it should never
# see. 4 x 3 x 2 x 2.
SPACE = list(product(list(c.CLASSES) + ["GARBAGE"], c.STORAGE,
                     (True, False), (True, False)))


class TestDecisionTable(unittest.TestCase):
    """Unchanged semantics. Same assertions as before the table became data."""

    def test_destroyed_storage_wins_over_everything_except_publication(self):
        # The v1 -> v2 change. Deleting your copy of a published artifact does
        # not un-publish it, so the disclosure obligation outlives the bytes.
        for klass, exclusive in product(c.CLASSES, (True, False)):
            self.assertEqual(
                p.remediate(klass, storage=c.DESTROYED, exclusive=exclusive,
                            terminal=False),
                c.ALREADY_GONE)
            self.assertEqual(
                p.remediate(klass, storage=c.DESTROYED, exclusive=exclusive,
                            terminal=True),
                c.NOTIFY_ONLY)

    def test_terminal_wins_over_class_and_exclusivity(self):
        # Published history is immutable regardless of how removable the
        # contribution is. Remediation stops; notification does not.
        for klass, exclusive in product(c.CLASSES, (True, False)):
            self.assertEqual(
                p.remediate(klass, exclusive=exclusive, terminal=True),
                c.NOTIFY_ONLY)

    def test_exclusive_writable_is_destroyed(self):
        for klass in c.CLASSES:
            self.assertEqual(p.remediate(klass, exclusive=True), c.DESTROY)

    def test_exclusive_worm_is_quarantined(self):
        for klass in c.CLASSES:
            self.assertEqual(
                p.remediate(klass, storage=c.WORM, exclusive=True),
                c.QUARANTINE)

    def test_separable_shared(self):
        self.assertEqual(p.remediate(c.SEPARABLE), c.PURGE)
        self.assertEqual(p.remediate(c.SEPARABLE, storage=c.WORM), c.REGENERATE)

    def test_regenerable_shared(self):
        self.assertEqual(p.remediate(c.REGENERABLE), c.REGENERATE)
        self.assertEqual(p.remediate(c.REGENERABLE, storage=c.WORM),
                         c.REGENERATE)

    def test_irreducible_shared(self):
        self.assertEqual(p.remediate(c.IRREDUCIBLE), c.QUARANTINE)

    def test_unknown_class_is_quarantined_not_purged(self):
        # Failing closed: an unrecognised class lands on the cautious action,
        # not the convenient one.
        self.assertEqual(p.remediate("MYSTERY"), c.QUARANTINE)

    def test_every_combination_yields_one_known_action_and_a_rule(self):
        for klass, storage, exclusive, terminal in SPACE:
            decision = p.decide(klass, storage=storage, exclusive=exclusive,
                                terminal=terminal)
            self.assertIn(decision["action"], p.ACTIONS)
            self.assertTrue(decision["rule"])
            self.assertNotEqual(c.explain(decision["action"]), "unknown action")


class TestRulesAreReachable(unittest.TestCase):
    def test_no_rule_is_dead(self):
        # A rule that can never match is indistinguishable from a deleted one,
        # except that the file still shows it and everyone believes it applies.
        reached = {p.decide(k, storage=s, exclusive=e, terminal=t)["rule"]
                   for k, s, e, t in SPACE}
        declared = {rule["id"] for rule in p.V1["rules"]}
        self.assertEqual(declared - reached, set())

    def test_the_builtin_table_never_falls_through(self):
        # The fallthrough guard is for policies that are wrong. Ours must not
        # be relying on it.
        reached = {p.decide(k, storage=s, exclusive=e, terminal=t)["rule"]
                   for k, s, e, t in SPACE}
        self.assertNotIn(p.FALLTHROUGH_RULE, reached)


class TestFingerprint(unittest.TestCase):
    def test_identical_policies_hash_identically(self):
        self.assertEqual(p.fingerprint(p.V1), p.fingerprint(json.loads(
            json.dumps(p.V1))))

    def test_changing_an_action_changes_the_hash(self):
        altered = json.loads(json.dumps(p.V1))
        altered["rules"][0]["action"] = c.QUARANTINE
        self.assertNotEqual(p.fingerprint(p.V1), p.fingerprint(altered))

    def test_changing_only_a_rationale_changes_the_hash(self):
        # The prose is hashed too. Two policies that decide identically but
        # justify differently are not the same policy: the rationale is what
        # an assessor reads, and editing it changes what the organisation is
        # on record as having meant.
        altered = json.loads(json.dumps(p.V1))
        altered["rules"][0]["because"] = "because I said so"
        self.assertNotEqual(p.fingerprint(p.V1), p.fingerprint(altered))

    def test_reordering_rules_changes_the_hash(self):
        # Order is semantics here — first match wins.
        altered = json.loads(json.dumps(p.V1))
        altered["rules"][0], altered["rules"][1] = (altered["rules"][1],
                                                   altered["rules"][0])
        self.assertNotEqual(p.fingerprint(p.V1), p.fingerprint(altered))

    def test_identify_carries_both_label_and_proof(self):
        stamp = p.identify()
        self.assertEqual(stamp["policy_version"], p.DEFAULT["version"])
        self.assertEqual(len(stamp["policy_hash"]), 64)


class TestValidation(unittest.TestCase):
    """Every failure is a refusal to load, never a warning."""

    def valid(self):
        return json.loads(json.dumps(p.V1))

    def assertRejected(self, policy, fragment):
        with self.assertRaises(p.InvalidPolicy) as caught:
            p.validate(policy)
        self.assertIn(fragment, str(caught.exception))

    def test_the_builtin_policy_validates(self):
        self.assertIs(p.validate(p.V1), p.V1)

    def test_unknown_action_is_rejected(self):
        bad = self.valid()
        bad["rules"][0]["action"] = "SHRED"
        self.assertRejected(bad, "unknown action")

    def test_unknown_dimension_is_rejected(self):
        # The important one. A typo'd dimension would otherwise load cleanly
        # and silently never match.
        bad = self.valid()
        bad["rules"][0]["when"] = {"storgae": c.DESTROYED}
        self.assertRejected(bad, "unknown dimension")

    def test_impossible_value_is_rejected(self):
        bad = self.valid()
        bad["rules"][0]["when"] = {"storage": "SHREDDED"}
        self.assertRejected(bad, "not a")

    def test_a_rule_cannot_test_an_unrecognised_class(self):
        # Normalisation happens first, so such a rule could never fire.
        bad = self.valid()
        bad["rules"][0]["when"] = {"contribution": "MYSTERY"}
        self.assertRejected(bad, "not a")

    def test_duplicate_rule_ids_are_rejected(self):
        bad = self.valid()
        bad["rules"][1]["id"] = bad["rules"][0]["id"]
        self.assertRejected(bad, "duplicate rule id")

    def test_a_rule_without_a_rationale_is_rejected(self):
        bad = self.valid()
        bad["rules"][0]["because"] = "  "
        self.assertRejected(bad, "rationale")

    def test_the_fallthrough_name_is_reserved(self):
        bad = self.valid()
        bad["rules"][0]["id"] = p.FALLTHROUGH_RULE
        self.assertRejected(bad, "reserved")

    def test_a_policy_without_a_version_is_rejected(self):
        bad = self.valid()
        bad["version"] = ""
        self.assertRejected(bad, "version")

    def test_a_policy_without_rules_is_rejected(self):
        bad = self.valid()
        bad["rules"] = []
        self.assertRejected(bad, "non-empty list of rules")


class TestVersionsAreImmutable(unittest.TestCase):
    """
    A shipped version is a historical record, not a place to fix things.

    Plans cite a version and a hash. If a version can be edited in place, the
    label is a lie and the hash proves nothing — a January plan would replay
    under a table nobody had in January. So the hashes are frozen here as
    literals: editing a shipped policy fails this test, and the fix is to add
    a version, never to change one.
    """

    FROZEN = {
        "v1": "dbb59de6d85fc0f87f4bc7d490b6ce34f8bf9222875386a721ec4e18f3ee0461",
        "v2": "e6ba60ffe6763949106eca86f7888c3cc3e28c920fd999ce275d708153152642",
    }

    def test_shipped_hashes_have_not_moved(self):
        for version, expected in self.FROZEN.items():
            self.assertEqual(
                p.fingerprint(p.resolve(version)), expected,
                f"policy {version} was edited. A shipped version is immutable: "
                f"add a new version instead, and leave {version} alone so "
                f"plans citing it stay replayable.")

    def test_every_registered_version_is_frozen_here(self):
        # Adding a version without freezing its hash would leave it editable.
        self.assertEqual(set(p.REGISTRY), set(self.FROZEN))

    def test_v1_and_v2_are_genuinely_different_tables(self):
        self.assertNotEqual(p.fingerprint(p.V1), p.fingerprint(p.V2))

    def test_rule_ids_are_stable_across_versions(self):
        # Ids identify rules, not positions, so two plans on different
        # versions stay comparable line by line.
        self.assertEqual({r["id"] for r in p.V1["rules"]},
                         {r["id"] for r in p.V2["rules"]})

    def test_only_the_order_of_r1_and_r2_changed(self):
        self.assertEqual([r["id"] for r in p.V1["rules"]],
                         ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"])
        self.assertEqual([r["id"] for r in p.V2["rules"]],
                         ["R2", "R1", "R3", "R4", "R5", "R6", "R7", "R8"])

    def test_v1_still_decides_the_way_it_always_did(self):
        # The point of keeping it: a plan from before the change replays
        # under the table that produced it, not under today's.
        self.assertEqual(
            p.remediate(c.REGENERABLE, storage=c.DESTROYED, terminal=True,
                        policy=p.resolve("v1")),
            c.ALREADY_GONE)
        self.assertEqual(
            p.remediate(c.REGENERABLE, storage=c.DESTROYED, terminal=True,
                        policy=p.resolve("v2")),
            c.NOTIFY_ONLY)


class TestLoadAndResolve(unittest.TestCase):
    def test_a_policy_round_trips_through_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            path.write_text(json.dumps(p.V1))
            loaded = p.load(path)
            self.assertEqual(p.fingerprint(loaded), p.fingerprint(p.V1))

    def test_a_bad_file_is_refused_at_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            path.write_text(json.dumps({"version": "x", "rules": [
                {"id": "R1", "when": {}, "action": "SHRED", "because": "no"}]}))
            with self.assertRaises(p.InvalidPolicy):
                p.load(path)

    def test_a_shipped_version_resolves(self):
        self.assertIs(p.resolve("v1"), p.V1)
        self.assertIs(p.resolve("v2"), p.V2)

    def test_an_unknown_version_raises_rather_than_substituting(self):
        # A plan citing a version this build does not have cannot be replayed
        # here. Say so, rather than recomputing under a different table and
        # presenting the result as the original.
        with self.assertRaises(p.InvalidPolicy):
            p.resolve("v99")


class TestGuardsOutsideTheRules(unittest.TestCase):
    def test_no_match_falls_through_to_quarantine(self):
        narrow = p.validate({
            "version": "narrow",
            "rules": [p.rule("ONLY", c.PURGE, "matches almost nothing",
                             contribution=c.SEPARABLE, storage=c.WORM,
                             exclusive=True, terminal=True)],
        })
        decision = p.decide(c.REGENERABLE, policy=narrow)
        self.assertEqual(decision["action"], c.QUARANTINE)
        self.assertEqual(decision["rule"], p.FALLTHROUGH_RULE)

    def test_normalisation_cannot_be_bypassed_by_a_policy(self):
        # An unrecognised class is IRREDUCIBLE before any rule sees it, so a
        # policy written to catch REGENERABLE cannot accidentally catch it.
        lenient = p.validate({
            "version": "lenient",
            "rules": [p.rule("R1", c.PURGE, "regenerable things are purged",
                             contribution=c.REGENERABLE)],
        })
        self.assertEqual(p.decide("MYSTERY", policy=lenient)["action"],
                         c.QUARANTINE)


class TestAPolicyCanBeWrong(unittest.TestCase):
    """
    Named so nobody later mistakes this for an oversight.

    The guards fix the FACTS, not the VERDICT. A policy that maps IRREDUCIBLE
    to PURGE is expressible, would be wrong, and Clew will run it. That is the
    reason the table is data: wrong logic in an if-ladder is invisible in a
    code review nobody does, while wrong logic in a hashed, versioned,
    rationale-carrying file sits in the open with a rule id on it.

    If a future change hardcodes verdicts back into core to "fix" this, this
    test fails and explains why it was deliberate.
    """

    def test_a_reckless_policy_is_honoured_and_identifiable(self):
        reckless = p.validate({
            "version": "reckless-2026-01",
            "rules": [p.rule("BAD", c.PURGE,
                             "we assert irreducible contributions are "
                             "separable, on grounds we have not written down",
                             contribution=c.IRREDUCIBLE)],
        })
        decision = p.decide(c.IRREDUCIBLE, policy=reckless)
        self.assertEqual(decision["action"], c.PURGE)
        # And it is attributable: version, hash and rule id all name it.
        self.assertEqual(decision["rule"], "BAD")
        self.assertNotEqual(p.fingerprint(reckless), p.fingerprint(p.V1))


class TestUnverifiedStorage(unittest.TestCase):
    """
    storage=None means NOT CHECKED, which is not one of the three values.

    The old behaviour returned DESTROYED whenever a workdir did not resolve —
    on another host, with an unmounted volume, from an extractor that records
    no workdir at all — and DESTROYED becomes ALREADY_GONE, "nothing to do".
    That is the one error direction this project exists not to make, so an
    unchecked dimension now yields no verdict rather than a convenient one.
    """

    def test_disagreement_yields_no_action(self):
        decision = p.decide(c.REGENERABLE, storage=None)
        self.assertIsNone(decision["action"])
        self.assertIsNone(decision["rule"])
        self.assertEqual(sorted(decision["possible"]),
                         [c.ALREADY_GONE, c.REGENERATE])

    def test_the_possible_map_names_the_rule_for_each_candidate(self):
        possible = p.decide(c.REGENERABLE, storage=None,
                            exclusive=True)["possible"]
        by_id = {r["id"]: r for r in p.V1["rules"]}
        for action, rule_id in possible.items():
            self.assertEqual(by_id[rule_id]["action"], action)

    def test_agreement_across_all_storage_states_is_a_real_answer(self):
        # Not a guess: if every possible storage state gives the same verdict,
        # checking the disk would change nothing, so the verdict stands.
        indifferent = p.validate({
            "version": "storage-indifferent",
            "rules": [p.rule("Q", c.QUARANTINE, "block use regardless of "
                                                "where the bytes are")],
        })
        decision = p.decide(c.IRREDUCIBLE, storage=None, policy=indifferent)
        self.assertEqual(decision["action"], c.QUARANTINE)
        self.assertEqual(decision["rule"], "Q")
        self.assertNotIn("possible", decision)
        self.assertIn("every possible state", decision["because"])

    def test_under_v1_nothing_is_decidable_without_checking_storage(self):
        # A consequence of R1 being first: DESTROYED always yields
        # ALREADY_GONE, and no other rule can, so every unchecked item
        # disagrees with itself. Under v1, storage MUST be verified — which
        # is half of why v2 exists.
        for klass, exclusive, terminal in product(c.CLASSES, (True, False),
                                                  (True, False)):
            decision = p.decide(klass, storage=None, exclusive=exclusive,
                                terminal=terminal, policy=p.V1)
            self.assertIsNone(decision["action"],
                              f"{klass} {exclusive} {terminal}")

    def test_under_v2_publication_needs_no_disk_check(self):
        # The verdict genuinely does not depend on the storage state, so it
        # is returned rather than withheld. Not a guess: all three possible
        # states give the same answer.
        for klass, exclusive in product(c.CLASSES, (True, False)):
            decision = p.decide(klass, storage=None, exclusive=exclusive,
                                terminal=True, policy=p.V2)
            self.assertEqual(decision["action"], c.NOTIFY_ONLY)
            self.assertEqual(decision["rule"], "R2")

    def test_under_v2_unpublished_items_still_need_a_disk_check(self):
        # v2 narrows what must be verified; it does not remove the need.
        for klass, exclusive in product(c.CLASSES, (True, False)):
            self.assertIsNone(
                p.decide(klass, storage=None, exclusive=exclusive,
                         terminal=False, policy=p.V2)["action"])

    def test_undetermined_is_not_an_action(self):
        # Code iterating the action set must not find it there and start
        # treating it as a remediation someone could carry out.
        self.assertNotIn(p.UNDETERMINED, p.ACTIONS)

    def test_remediate_returns_none_rather_than_a_default(self):
        self.assertIsNone(p.remediate(c.REGENERABLE, storage=None))


class TestReplay(unittest.TestCase):
    def test_the_same_inputs_under_two_policies_differ_visibly(self):
        # What versioning is for: the verdict changed because the table
        # changed, and both tables can be named.
        strict = p.validate({
            "version": "strict-2026-02",
            "rules": [p.rule("S1", c.QUARANTINE,
                             "this organisation does not purge in place",
                             contribution=c.SEPARABLE)],
        })
        self.assertEqual(p.remediate(c.SEPARABLE), c.PURGE)
        self.assertEqual(p.remediate(c.SEPARABLE, policy=strict), c.QUARANTINE)

    def test_decisions_are_stable_across_repeated_calls(self):
        first = [p.decide(k, storage=s, exclusive=e, terminal=t)
                 for k, s, e, t in SPACE]
        second = [p.decide(k, storage=s, exclusive=e, terminal=t)
                  for k, s, e, t in SPACE]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
