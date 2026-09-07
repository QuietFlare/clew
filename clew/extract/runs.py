"""Read runs straight from the engine's record; Clew keeps only a digest sidecar beside it."""

import json
from pathlib import Path

from clew.extract import horus, nextflow_store

SIDECAR_DIR = ".clew"


class Runs:
    def __init__(self, path):
        self.path = Path(path)
        if (self.path / ".lineage").is_dir():
            self.path = self.path / ".lineage"
        self.kind = self._detect()

    def _detect(self):
        if (self.path / ".history").is_dir():
            return "nextflow"
        if (self.path / horus.PLAN).is_file():
            return "horus-run"
        if any((child / horus.PLAN).is_file() for child in self.path.iterdir() if child.is_dir()):
            return "horus"
        if any(child.suffix == ".json" for child in self.path.iterdir()):
            return "graphs"
        raise SystemExit(f"{self.path} is not a .lineage store, a horus-lineage root, "
                         "or a directory of graph JSON files")

    def names(self):
        """[(name, id, timestamp)] oldest first."""
        if self.kind == "nextflow":
            return [(r["name"], r["run_hash"], r["timestamp"])
                    for r in nextflow_store.load_history(self.path)]
        if self.kind == "horus-run":
            return [(self.path.name, self.path.name, "")]
        if self.kind == "horus":
            dirs = sorted((c for c in self.path.iterdir() if (c / horus.PLAN).is_file()),
                          key=lambda c: c.stat().st_mtime)
            return [(c.name, c.name, "") for c in dirs]
        files = sorted((c for c in self.path.iterdir() if c.suffix == ".json"),
                       key=lambda c: c.stat().st_mtime)
        return [(c.stem, c.stem, "") for c in files]

    def resolve(self, wanted=None):
        """(name, id) for a run name or id prefix; the latest when None."""
        names = self.names()
        if not names:
            raise SystemExit(f"no runs under {self.path}")
        if wanted is None:
            return names[-1][:2]
        matches = [n for n in names if n[0] == wanted or n[1].startswith(wanted)]
        if len(matches) != 1:
            raise SystemExit(f"--run {wanted!r} matched {len(matches)} runs; known: "
                             + ", ".join(n[0] for n in names))
        return matches[0][:2]

    def load(self, wanted=None):
        """The graph of one run, with any sidecar digests merged in."""
        name, run_id = self.resolve(wanted)
        if self.kind == "nextflow":
            run = nextflow_store.pick_run(nextflow_store.load_history(self.path), run_id)
            graph = nextflow_store.extract(self.path, run["session_id"])
        elif self.kind == "horus-run":
            graph = horus.extract(self.path)
        elif self.kind == "horus":
            graph = horus.extract(self.path / run_id)
        else:
            graph = json.loads((self.path / f"{run_id}.json").read_text())
        graph["run"] = {"name": name, "id": run_id}
        sidecar = self.sidecar_path(run_id)
        if sidecar.is_file():
            merge_sidecar(graph, json.loads(sidecar.read_text()))
        return graph

    def sidecar_path(self, run_id):
        base = self.path.parent if self.kind == "horus-run" else self.path
        return base / SIDECAR_DIR / f"{run_id}.digests.json"

    def save_sidecar(self, graph):
        """Keep the sha256 digests of a graph beside the engine's record."""
        outputs = {}
        for task_hash, details in graph.get("output_details", {}).items():
            kept = {d["file"]: d["digest"] for d in details
                    if (d.get("digest") or "").startswith("sha256:")}
            if kept:
                outputs[task_hash] = kept
        sidecar = {"clew_sidecar_version": 1, "outputs": outputs,
                   "published": graph.get("published", {})}
        path = self.sidecar_path(graph["run"]["id"])
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(sidecar, indent=2))
        return path


def merge_sidecar(graph, sidecar):
    """Digests from the sidecar fill in what the engine did not record."""
    details = graph.setdefault("output_details", {})
    for task_hash, files in sidecar.get("outputs", {}).items():
        entries = details.setdefault(task_hash, [])
        known = {d["file"]: d for d in entries}
        for name, digest in files.items():
            if name not in known:
                known[name] = {"file": name}
                entries.append(known[name])
            if not (known[name].get("digest") or "").startswith("sha256:"):
                known[name]["digest"] = digest
    if sidecar.get("published"):
        graph["published"] = sidecar["published"]
    return graph
