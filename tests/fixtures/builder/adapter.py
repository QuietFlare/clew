"""Ligands of a docked library. Each enters at the task that read the library."""

from pathlib import Path

from clew.contracts import REMOVE, Adapter, Trigger
from clew.graph.graph import EXTERNAL


class Ligand(Trigger):
    mode = REMOVE
    about = "ligand in the docked library"

    def add_arguments(self, parser):
        parser.add_argument("--sheet", help="the ligand library the run docked")

    def ids(self, args):
        if not getattr(args, "sheet", None):
            raise SystemExit("--sheet is required")
        rows = (line.split() for line in Path(args.sheet).read_text().splitlines())
        return sorted(row[1] for row in rows if len(row) >= 2)

    def resolve(self, graph, value, args):
        ids = self.ids(args)
        if value is not None and value not in ids:
            raise SystemExit(f"unknown ligand {value!r}")
        library = Path(args.sheet).name
        entry = sorted(e["consumer"] for e in graph["edges"]
                       if e["producer"] == EXTERNAL and e["filename"] == library)
        return {one: list(entry) for one in ids}

    def values(self, args, graph=None):
        return self.ids(args)


class SiteLigands(Adapter):
    name = "site-ligands"
    triggers = {"ligand": Ligand()}
