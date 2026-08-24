"""
The vocabulary: the classes, the actions, and the fail-closed normalisation.

Deciding is not tested here — it moved to core/policy.py, where the table has
a version and a hash. See tests/test_policy.py. The split is deliberate: the
words have to be stable for Clew to mean anything, while the table has to be
versioned so a plan from March can be replayed under March's table.
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


class TestVocabulary(unittest.TestCase):
    def test_the_class_enum_is_closed(self):
        # A fourth class would leave core unable to traverse: it would not
        # know what removability meant for the new one.
        self.assertEqual(set(c.CLASSES),
                         {c.SEPARABLE, c.REGENERABLE, c.IRREDUCIBLE})

    def test_every_action_has_an_explanation(self):
        for action in (c.PURGE, c.REGENERATE, c.QUARANTINE, c.DESTROY,
                       c.NOTIFY_ONLY, c.ALREADY_GONE):
            self.assertNotEqual(c.explain(action), "unknown action")

    def test_an_unrecognised_action_says_so(self):
        self.assertEqual(c.explain("INVENTED"), "unknown action")

    def test_contribution_module_does_not_decide(self):
        # Guards the split. If remediate() reappears here, two tables exist
        # and one of them will rot.
        self.assertFalse(hasattr(c, "remediate"))


if __name__ == "__main__":
    unittest.main()
