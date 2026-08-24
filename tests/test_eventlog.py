"""
The log's arithmetic, with no database in sight.

Hashing and chain verification are pure functions on plain dicts, and they
are kept that way for a reason: an auditor checking an exported evidence
bundle has a JSON file and a Python interpreter. If verifying our claims
required installing a database driver and standing up a server, "anyone can
check this without us" would be a slogan rather than a fact.

Storage behaviour — the role grants, the triggers, concurrent appends — lives
in test_eventlog_postgres.py and needs a server.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import eventlog as el

T0 = "2026-01-01T00:00:00+00:00"


def entry(seq, prev_hash, **overrides):
    """One well-formed entry, hash included, built without a database."""
    fields = {
        "seq": seq,
        "effective_from": T0,
        "recorded_at": T0,
        "actor": "tester",
        "event_type": "Thing",
        "subject": f"s{seq}",
        "body": el.canonical({"i": seq}),
        "prev_hash": prev_hash,
    }
    fields.update(overrides)
    fields["hash"] = el.event_hash(fields)
    return fields


def chain(n):
    entries, prev = [], el.GENESIS
    for i in range(1, n + 1):
        entries.append(entry(i, prev))
        prev = entries[-1]["hash"]
    return entries


class TestCanonical(unittest.TestCase):
    def test_key_order_does_not_matter(self):
        self.assertEqual(el.canonical({"x": 1, "y": 2}),
                         el.canonical({"y": 2, "x": 1}))

    def test_no_incidental_whitespace(self):
        self.assertEqual(el.canonical({"a": 1, "b": [1, 2]}),
                         '{"a":1,"b":[1,2]}')

    def test_preimage_is_pure_ascii(self):
        # Removes any question of which encoding produced a given digest.
        self.assertTrue(el.canonical({"actor": "Zoë"}).isascii())


class TestHash(unittest.TestCase):
    def test_every_field_is_covered(self):
        # No field is outside the hash. If one were, that field would be the
        # quiet place to edit.
        base = entry(1, el.GENESIS)
        for field in ("seq", "effective_from", "recorded_at", "actor",
                      "event_type", "subject", "body", "prev_hash"):
            altered = dict(base)
            altered[field] = "changed" if isinstance(base[field], str) else 99
            self.assertNotEqual(
                el.event_hash(base), el.event_hash(altered),
                f"changing {field} left the hash unchanged")

    def test_body_hashes_the_same_parsed_or_raw(self):
        # Closes the trap that would otherwise break bundle verification:
        # the exported body arrives as a structure, the stored one as text.
        base = entry(1, el.GENESIS)
        parsed = dict(base, body={"i": 1})
        self.assertEqual(el.event_hash(base), el.event_hash(parsed))


class TestVerifyEntries(unittest.TestCase):
    def test_intact_chain_verifies(self):
        result = el.verify_entries(chain(5))
        self.assertTrue(result["ok"])
        self.assertEqual(result["entries"], 5)
        self.assertIsNone(result["broken_at"])

    def test_empty_chain_verifies(self):
        result = el.verify_entries([])
        self.assertTrue(result["ok"])
        self.assertEqual(result["entries"], 0)
        self.assertEqual(result["head"], el.GENESIS)

    def test_first_entry_must_link_to_genesis(self):
        rogue = el.verify_entries([entry(1, "f" * 64)])
        self.assertFalse(rogue["ok"])
        self.assertEqual(rogue["broken_at"], 1)

    def test_edited_content_is_caught(self):
        entries = chain(4)
        entries[1]["actor"] = "someone-else"   # hash left untouched
        result = el.verify_entries(entries)
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken_at"], 2)
        self.assertIn("edited", result["reason"])

    def test_backdating_is_caught(self):
        # The specific fraud the two clocks invite: making a fact look like it
        # was known earlier than it was.
        entries = chain(3)
        entries[2]["recorded_at"] = "2020-01-01T00:00:00+00:00"
        self.assertEqual(el.verify_entries(entries)["broken_at"], 3)

    def test_removed_entry_is_caught(self):
        entries = chain(4)
        del entries[1]
        result = el.verify_entries(entries)
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken_at"], 3)
        self.assertIn("removed", result["reason"])

    def test_reordering_is_caught(self):
        entries = chain(4)
        entries[1], entries[2] = entries[2], entries[1]
        self.assertFalse(el.verify_entries(entries)["ok"])

    def test_first_failure_only_is_reported(self):
        # A chain is broken from its first bad link onwards. Listing every
        # later entry as "also wrong" would bury where the edit happened.
        entries = chain(6)
        entries[1]["actor"] = "x"
        entries[4]["actor"] = "y"
        self.assertEqual(el.verify_entries(entries)["broken_at"], 2)


class TestWindowAnchoring(unittest.TestCase):
    """
    A slice of the chain is only verifiable against what came before it.
    Slice 3's bundles cover ranges, so this is the property they rest on.
    """

    def test_window_verifies_against_its_anchor(self):
        entries = chain(6)
        window = entries[3:]
        result = el.verify_entries(window, start_seq=4,
                                   start_prev=entries[2]["hash"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["entries"], 3)

    def test_window_fails_against_the_wrong_anchor(self):
        entries = chain(6)
        result = el.verify_entries(entries[3:], start_seq=4,
                                   start_prev="a" * 64)
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken_at"], 4)

    def test_unanchored_window_does_not_pass_as_a_whole_log(self):
        # The default anchor is genesis. A window handed over without its
        # anchor must fail rather than look like a complete history.
        entries = chain(6)
        self.assertFalse(el.verify_entries(entries[3:])["ok"])


class TestHonestLimits(unittest.TestCase):
    """
    Asserted, not left implied. Nobody should later read ok=True as
    "nothing was lost".
    """

    def test_truncated_tail_still_verifies(self):
        full = chain(4)
        remembered_head = el.verify_entries(full)["head"]
        shorter = el.verify_entries(full[:3])
        self.assertTrue(shorter["ok"])
        self.assertNotEqual(shorter["head"], remembered_head)

    def test_wholly_rebuilt_chain_still_verifies(self):
        original_head = el.verify_entries(chain(3))["head"]
        forged = []
        prev = el.GENESIS
        for i in range(1, 4):
            forged.append(entry(i, prev, actor="forger"))
            prev = forged[-1]["hash"]
        rebuilt = el.verify_entries(forged)
        self.assertTrue(rebuilt["ok"])
        self.assertNotEqual(rebuilt["head"], original_head)


if __name__ == "__main__":
    unittest.main()
