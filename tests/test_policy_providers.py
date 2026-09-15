"""A provider may register its own policy table by name, and override a shipped one."""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew.ledger import policy


class FakeEntry:
    def __init__(self, value):
        self.value = value

    def load(self):
        return self.value


def table(version, **changes):
    t = copy.deepcopy(policy.V1)
    t["version"] = version
    t.update(changes)
    return t


def registered(*entries):
    """Patch the entry-point reader with (name, dist, entry) rows."""
    return mock.patch("clew.contracts.registry.entry_points", return_value=list(entries))


class TestPolicyProviders(unittest.TestCase):
    def test_a_registered_table_resolves_by_name(self):
        with registered(("qbc-v1", "clew-qbc", FakeEntry(table("qbc-v1")))):
            found = policy.resolve_or_load("qbc-v1")
        self.assertEqual(found["version"], "qbc-v1")
        self.assertEqual(policy.identify(found)["policy_version"], "qbc-v1")

    def test_a_table_may_be_a_json_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "site.json"
            path.write_text(json.dumps(table("site-v3")))
            with registered(("site-v3", "clew-site", FakeEntry(str(path)))):
                self.assertEqual(policy.resolve("site-v3")["version"], "site-v3")

    def test_a_registered_name_overrides_a_shipped_version(self):
        mine = table("v2", description="the site's own v2")
        with registered(("v2", "clew-site", FakeEntry(mine))):
            self.assertEqual(policy.resolve("v2")["description"], "the site's own v2")
        self.assertNotEqual(policy.resolve("v2")["description"], "the site's own v2")

    def test_an_invalid_table_is_refused_naming_the_provider(self):
        broken = table("bad-v1")
        broken["rules"][0]["action"] = "EXPLODE"
        with registered(("bad-v1", "clew-site", FakeEntry(broken))):
            with self.assertRaises(policy.InvalidPolicy) as stop:
                policy.available()
        self.assertIn("clew-site", str(stop.exception))
        self.assertIn("EXPLODE", str(stop.exception))

    def test_name_and_version_must_agree(self):
        with registered(("qbc-v1", "clew-qbc", FakeEntry(table("qbc-v9")))):
            with self.assertRaises(policy.InvalidPolicy) as stop:
                policy.available()
        self.assertIn("must agree", str(stop.exception))


if __name__ == "__main__":
    unittest.main()
