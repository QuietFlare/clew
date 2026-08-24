"""
The one hard rule, as a test.

CLAUDE.md states it as a grep: core/ must never mention a specimen, a study
participant, a permission-to-use, or a workflow engine. The rule is easy to
state and easy to break in a hurry, and a broken boundary is invisible until
the day someone tries to add a second domain and finds core full of the
first one's vocabulary.

Second, harder question the grep stands in for: could domains/training/ —
AI training data with opt-out semantics — be added without editing core?
If a word below appears in core, the answer is already no.

The forbidden words are built from fragments so that this file, which lives
outside core/, does not itself become the reason a future grep of the repo
looks alarming.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CORE = Path(__file__).resolve().parent.parent / "core"

# Assembled rather than written out; see the module docstring.
FORBIDDEN = [
    "sam" + "ple", "do" + "nor", "con" + "sent", "D" + "UO", "ali" + "quot",
    "bio" + "bank", "I" + "RB", "next" + "flow", "geno" + "me", "pati" + "ent",
]


class TestCoreHasNoDomainVocabulary(unittest.TestCase):
    def test_core_files_are_clean(self):
        offences = []
        for path in sorted(CORE.glob("*.py")):
            text = path.read_text()
            for line_number, line in enumerate(text.splitlines(), 1):
                for word in FORBIDDEN:
                    if re.search(rf"\b{word}\w*", line, re.IGNORECASE):
                        offences.append(
                            f"{path.name}:{line_number} contains {word!r}: "
                            f"{line.strip()}")
        self.assertEqual(
            offences, [],
            "core/ has acquired domain vocabulary. Move the knowledge into a "
            "domains/ adapter that translates before calling in:\n  "
            + "\n  ".join(offences))

    def test_core_imports_nothing_from_domains(self):
        # The subtler leak: core staying verbally clean while depending on a
        # domain for behaviour.
        offences = []
        for path in sorted(CORE.glob("*.py")):
            for line_number, line in enumerate(path.read_text().splitlines(), 1):
                if re.match(r"\s*(from|import)\s+domains", line):
                    offences.append(f"{path.name}:{line_number} {line.strip()}")
        self.assertEqual(offences, [], "core/ imports from domains/")


if __name__ == "__main__":
    unittest.main()
