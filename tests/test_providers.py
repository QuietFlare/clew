"""clew providers: every domain and extractor installed, and where each came from."""

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clew import providers


class TestProviders(unittest.TestCase):
    def test_builtins_are_listed_with_their_package(self):
        with redirect_stdout(io.StringIO()) as out:
            code = providers.main([])
        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("clew.adapters", text)
        self.assertIn("clew.extractors", text)
        for name in ("sarek", "snakemake", "nextflow", "cromwell", "horus", "latch", "dnanexus"):
            self.assertRegex(text, rf"\n  {name}\s+clew-lineage")


if __name__ == "__main__":
    unittest.main()
