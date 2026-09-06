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
CLEAN = ["graph", "ledger"]
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
    "domains": {"graph"},
    "ledger": {"graph"},
    "extract": {"graph", "domains"},
    "views": {"graph", "ledger"},
    "questions": {"graph", "domains", "ledger", "extract", "views"},
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


if __name__ == "__main__":
    unittest.main()
