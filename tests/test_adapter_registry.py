"""
The domain contract: defining a named subclass is the registration.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.contracts import Adapter, discover


class TestRegistry(unittest.TestCase):
    def test_builtins_register_by_import(self):
        names = set(discover(Adapter))
        self.assertTrue({"sarek", "rnaseq", "viralrecon", "snakemake"} <= names)

    def test_a_named_subclass_registers_itself(self):
        class Custom(Adapter):
            name = "custom-test"  # no kinds of its own; the engine's still apply

        try:
            self.assertIsInstance(discover(Adapter)["custom-test"], Custom)
        finally:
            Adapter.registered.pop("custom-test", None)

    def test_a_nameless_subclass_is_a_base_not_a_provider(self):
        class Shared(Adapter):
            pass

        self.assertNotIn(None, Adapter.registered)
        self.assertFalse(any(isinstance(d, Shared) for d in Adapter.registered.values()))

    def test_engine_kinds_resolve_for_a_domain_with_none(self):
        from clew.contracts.trigger import lookup
        class Bare(Adapter):
            name = "bare-test"
        try:
            self.assertIsNotNone(lookup(Bare(), "container"))
            self.assertIsNone(lookup(Bare(), "patient", {"tasks": {}, "edges": []}))
        finally:
            Adapter.registered.pop("bare-test", None)


if __name__ == "__main__":
    unittest.main()
