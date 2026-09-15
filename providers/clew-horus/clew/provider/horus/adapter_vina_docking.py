"""
Pantheon W-02, AutoDock Vina docking on Horus: prep -> dock -> summary.

Every ligand enters at prep through the library file, and every output is
per-ligand: a PDBQT in the inputs archive, a pose file in the docking
archive, rows in the two CSVs. Withdrawing a ligand is a removal whose
artifacts are all separable.
"""

from pathlib import Path

from clew.contracts import REMOVE, Adapter, Trigger
from clew.graph.contribution import SEPARABLE
from clew.graph.graph import EXTERNAL

DEFAULT_LIBRARY = "ligands.smi"
PER_LIGAND_STEPS = ("prep", "dock", "summary")


def read_library(path):
    """{name: []} from a SMILES file, one 'SMILES name' per line."""
    names = {}
    for line in Path(path).read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2 and not line.startswith("#"):
            names.setdefault(parts[1], [])
    return names


class LigandKind(Trigger):
    mode = REMOVE

    def add_arguments(self, parser):
        parser.add_argument("--ligands", metavar="SMI",
                            help=f"the ligand library the run docked; default {DEFAULT_LIBRARY} beside the run")

    def library(self, args):
        path = getattr(args, "ligands", None)
        if not path:
            raise SystemExit("--ligands is required: ligand names come from the library's second column")
        return path

    def ids(self, path):
        return read_library(path)

    def entries(self, graph, ids, library_name=DEFAULT_LIBRARY):
        # The library is one external file consumed by prep, so every ligand
        # enters at the same task. Nothing is exclusive; the class hook is
        # what makes a single ligand removable.
        entry = sorted(e["consumer"] for e in graph["edges"]
                       if e["producer"] == EXTERNAL and e["filename"] == library_name)
        return {name: list(entry) for name in ids}

    def resolve(self, graph, value, args):
        path = self.library(args)
        entry = self.entries(graph, self.ids(path), Path(path).name)
        if value is not None and value not in entry:
            raise SystemExit(f"unknown ligand {value!r}; the library names: {', '.join(sorted(entry))}")
        return entry

    def values(self, args, graph=None):
        return sorted(self.ids(self.library(args)))


class VinaDocking(Adapter):
    name = "vina-docking"
    triggers = {"ligand": LigandKind()}
    load_bearing_inputs = ("receptor.pdb",)

    def contribution(self, graph, task_hash, kind):
        # prep, dock and summary each write one entry per ligand, so one
        # ligand's contribution can be dropped in place. A defect in the step
        # itself is not per-ligand, so any other kind keeps the engine's answer.
        process = graph["tasks"].get(task_hash, {}).get("process")
        return SEPARABLE if kind == "ligand" and process in PER_LIGAND_STEPS else None
