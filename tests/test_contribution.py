"""
The remediation decision table, exhaustively.

Every (contribution, storage, exclusive, terminal) combination must resolve
to exactly one action, and the fail-closed rule must hold: anything
unrecognised is treated as IRREDUCIBLE, never silently as something better.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import contribution as c


class TestFailClosed(unittest.TestCase):
    def test_unknown_class_becomes_irreducible(self):
        self.assertEqual(c.normalise("SOMETHING_NEW"), c.IRREDUCIBLE)
        self.assertEqual(c.normalise(None), c.IRREDUCIBLE)
        self.assertEqual(c.normalise(""), c.IRREDUCIBLE)

    def test_known_classes_pass_through(self):
        for klass in c.CLASSES:
            self.assertEqual(c.normalise(klass), klass)

    def test_unknown_class_is_quarantined_not_purged(self):
        # The whole point of failing closed: an unrecognised class must land
        # on the cautious action, not the convenient one.
        self.assertEqual(c.remediate("MYSTERY"), c.QUARANTINE)


class TestDecisionTable(unittest.TestCase):
    def test_destroyed_storage_wins_over_everything(self):
        for klass in c.CLASSES:
            for terminal in (True, False):
                for exclusive in (True, False):
                    self.assertEqual(
                        c.remediate(klass, storage=c.DESTROYED,
                                    exclusive=exclusive, terminal=terminal),
                        c.ALREADY_GONE,
                    )

    def test_terminal_wins_over_class_and_exclusivity(self):
        # Published history is immutable regardless of how removable the
        # contribution is. Remediation stops; notification does not.
        for klass in c.CLASSES:
            for exclusive in (True, False):
                self.assertEqual(
                    c.remediate(klass, exclusive=exclusive, terminal=True),
                    c.NOTIFY_ONLY,
                )

    def test_exclusive_writable_is_destroyed(self):
        for klass in c.CLASSES:
            self.assertEqual(c.remediate(klass, exclusive=True), c.DESTROY)

    def test_exclusive_worm_is_quarantined(self):
        # The artifact should go, but the bytes cannot be changed.
        for klass in c.CLASSES:
            self.assertEqual(
                c.remediate(klass, storage=c.WORM, exclusive=True), c.QUARANTINE
            )

    def test_separable_shared(self):
        self.assertEqual(c.remediate(c.SEPARABLE), c.PURGE)
        # Separable but unwritable: cannot subtract in place, so recompute.
        self.assertEqual(c.remediate(c.SEPARABLE, storage=c.WORM), c.REGENERATE)

    def test_regenerable_shared(self):
        self.assertEqual(c.remediate(c.REGENERABLE), c.REGENERATE)
        self.assertEqual(c.remediate(c.REGENERABLE, storage=c.WORM), c.REGENERATE)

    def test_irreducible_shared(self):
        self.assertEqual(c.remediate(c.IRREDUCIBLE), c.QUARANTINE)

    def test_every_combination_yields_exactly_one_known_action(self):
        actions = {c.PURGE, c.REGENERATE, c.QUARANTINE, c.DESTROY,
                   c.NOTIFY_ONLY, c.ALREADY_GONE}
        for klass in c.CLASSES + ("GARBAGE",):
            for storage in c.STORAGE:
                for exclusive in (True, False):
                    for terminal in (True, False):
                        action = c.remediate(klass, storage=storage,
                                             exclusive=exclusive, terminal=terminal)
                        self.assertIn(action, actions)
                        self.assertNotEqual(c.explain(action), "unknown action")


if __name__ == "__main__":
    unittest.main()
