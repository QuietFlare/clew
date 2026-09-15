"""
The hard rules, as tests.

graph/ and ledger/ must never mention a specimen, a study participant, a
permission-to-use, or a workflow engine. The rule is easy to state and easy
to break in a hurry, and a broken boundary is invisible until the day
someone tries to add a second domain and finds the engine full of the
first one's vocabulary.

Second, packages import downward only. graph/ imports nothing from clew,
and each layer above it may reach only the layers listed in ALLOWED.

The forbidden words are built from fragments so that this file, which lives
outside the guarded packages, does not itself become the reason a future
grep of the repo looks alarming.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PACKAGE = Path(__file__).resolve().parent.parent / "clew"
CLEAN = ["graph", "ledger", "contracts"]
# Command modules talk to people and may use their words. Only the library
# modules under a clean package are held to the rule.
COMMANDS = {"evidence.py", "logbook.py", "rulebook.py"}

# Assembled rather than written out; see the module docstring.
FORBIDDEN = [
    "sam" + "ple", "do" + "nor", "con" + "sent", "D" + "UO", "ali" + "quot",
    "bio" + "bank", "I" + "RB", "next" + "flow", "geno" + "me", "pati" + "ent",
]

ALLOWED = {
    "graph": set(),
    "contracts": {"graph"},
    "ledger": {"graph", "contracts"},
    "extract": {"graph", "contracts"},
    "views": {"graph", "ledger"},
    "questions": {"graph", "ledger", "extract", "views", "contracts"},
}

IMPORT = re.compile(r"^\s*(?:from|import)\s+clew\.(\w+)", re.MULTILINE)


class TestBoundary(unittest.TestCase):
    def test_engine_packages_have_no_domain_vocabulary(self):
        offences = []
        for package in CLEAN:
            for path in sorted((PACKAGE / package).glob("*.py")):
                if path.name in COMMANDS:
                    continue
                for number, line in enumerate(path.read_text().splitlines(), 1):
                    for word in FORBIDDEN:
                        if re.search(rf"\b{word}\w*", line, re.IGNORECASE):
                            offences.append(
                                f"{package}/{path.name}:{number} contains "
                                f"{word!r}: {line.strip()}")
        self.assertEqual(
            offences, [],
            "graph/ or ledger/ has acquired domain vocabulary. Move the "
            "knowledge into a domains/ adapter that translates before "
            "calling in:\n  " + "\n  ".join(offences))

    def test_packages_import_downward_only(self):
        offences = []
        for package, allowed in ALLOWED.items():
            for path in sorted((PACKAGE / package).glob("*.py")):
                for target in IMPORT.findall(path.read_text()):
                    if target != package and target not in allowed:
                        offences.append(f"{package}/{path.name} imports clew.{target}")
        self.assertEqual(offences, [], "\n".join(offences))


PROVIDERS = PACKAGE / "provider"
PYPROJECT = PACKAGE.parent / "pyproject.toml"
# What a provider may import from clew: the graph, the contracts, and the
# engine-side extract tools. Not another provider, not the questions.
PROVIDER_ALLOWED = {"graph", "contracts", "extract"}
PROVIDER_IMPORT = re.compile(r"^\s*(?:from|import)\s+clew\.provider\.(\w+)", re.MULTILINE)


def declared_entry_points(pyproject, group):
    """[(name, module)] under one entry-point group. A regex, since tomllib is 3.11+."""
    text = pyproject.read_text()
    section = re.search(rf'^\[project\.entry-points\."{re.escape(group)}"\]\n(.*?)(?=^\[|\Z)',
                        text, re.M | re.S)
    if not section:
        return []
    return re.findall(r'^([\w-]+)\s*=\s*"([^"]+)"', section.group(1), re.M)


class TestProviders(unittest.TestCase):
    """The built-ins are held to what a third-party provider could do."""

    def test_providers_reach_only_the_public_surface(self):
        offences = []
        for package in sorted(p for p in PROVIDERS.iterdir() if p.is_dir()):
            for path in sorted(package.glob("*.py")):
                text = path.read_text()
                for target in IMPORT.findall(text):
                    if target != "provider" and target not in PROVIDER_ALLOWED:
                        offences.append(f"{package.name}/{path.name} imports clew.{target}")
                for other in PROVIDER_IMPORT.findall(text):
                    if other != package.name:
                        offences.append(f"{package.name}/{path.name} imports provider {other}")
        self.assertEqual(offences, [], "\n".join(offences))

    def test_namespace_levels_carry_no_init(self):
        # clew and clew.provider are namespace packages. An __init__.py at
        # either level claims the whole package for this distribution, and a
        # third party's provider installed beside it silently stops registering.
        offences = [str(p.relative_to(PACKAGE.parent))
                    for p in (PACKAGE / "__init__.py", PACKAGE / "provider" / "__init__.py")
                    if p.exists()]
        self.assertEqual(offences, [], "namespace level has an __init__.py:\n  "
                         + "\n  ".join(offences))

    def test_every_provider_is_discoverable(self):
        # Every declared entry point must register, and every provider
        # directory must be declared, or it ships undiscoverable.
        import importlib
        from clew.contracts import Adapter, Extractor
        groups = {"clew.adapters": Adapter, "clew.extractors": Extractor}
        offences, named = [], set()
        for group, contract in groups.items():
            for key, module in declared_entry_points(PYPROJECT, group):
                named.add(module.split(".")[2])
                importlib.import_module(module)
                if key not in contract.registered:
                    offences.append(f"{group} entry {key!r} imported {module} "
                                    "but nothing registered under that name")
        for package in sorted(p for p in PROVIDERS.iterdir() if p.is_dir()):
            try:
                importlib.import_module(f"clew.provider.{package.name}")
            except ImportError as exc:
                offences.append(f"clew.provider.{package.name} not importable: {exc}")
            if package.name not in named:
                offences.append(f"clew.provider.{package.name} has no entry point in pyproject.toml")
        self.assertEqual(offences, [], "\n".join(offences))

    def test_no_provider_code_remains_in_the_engine(self):
        self.assertFalse((PACKAGE / "domains").exists())
        self.assertEqual(sorted(p.name for p in (PACKAGE / "extract").glob("*.py")),
                         ["__init__.py", "digest.py", "runs.py", "stitch.py"])


if __name__ == "__main__":
    unittest.main()
