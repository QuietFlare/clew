"""Read runs straight from the engine's record; Clew keeps only a digest sidecar beside it."""

import json
import sys
from pathlib import Path

from clew.extract import horus, nextflow_store

SIDECAR_DIR = ".clew"

# Where a record may say when it was made, for engines whose record is a
# JSON file. Read before file mtime, which `touch` or a copy rewrites.
TIMESTAMP_KEYS = ("timestamp", "started_at", "created_at", "start_time")


def recorded_timestamp(path):
    """A timestamp from inside a JSON record, or "" when it carries none."""
    try:
        record = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return ""
    for holder in (record, record.get("run") or {}):
        if not isinstance(holder, dict):
            continue
        for key in TIMESTAMP_KEYS:
            if isinstance(holder.get(key), str) and holder[key]:
                return holder[key]
    return ""


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

    def records(self):
        """
        [{name, id, timestamp, session, by_mtime}] oldest first. A run
        whose record carries no timestamp is ordered by file mtime and
        says so, since a copy or a touch reorders those.
        """
        if self.kind == "nextflow":
            return [{"name": r["name"], "id": r["run_hash"], "timestamp": r["timestamp"],
                     "session": r["session_id"], "by_mtime": False}
                    for r in nextflow_store.load_history(self.path)]
        if self.kind == "horus-run":
            return [{"name": self.path.name, "id": self.path.name,
                     "timestamp": recorded_timestamp(self.path / horus.PLAN),
                     "session": None, "by_mtime": False}]
        if self.kind == "horus":
            entries = [(c, c / horus.PLAN) for c in self.path.iterdir()
                       if (c / horus.PLAN).is_file()]
        else:
            entries = [(c, c) for c in self.path.iterdir() if c.suffix == ".json"]
        found = []
        for entry, record in entries:
            stamp = recorded_timestamp(record)
            name = entry.stem if entry.is_file() else entry.name
            found.append(({"name": name, "id": name, "timestamp": stamp,
                           "session": None, "by_mtime": not stamp},
                          entry.stat().st_mtime))
        # Timestamps and mtimes do not compare, so recorded ones sort
        # among themselves and the rest fall in by mtime after them.
        found.sort(key=lambda pair: (pair[0]["by_mtime"], pair[0]["timestamp"], pair[1]))
        return [record for record, _ in found]

    def names(self):
        """[(name, id, timestamp)] oldest first."""
        return [(r["name"], r["id"], r["timestamp"]) for r in self.records()]

    def resolve(self, wanted=None):
        """
        (name, id) for a run name, run-id prefix or session-id prefix; the
        latest when None. A session prefix names a resume chain, whose
        newest run stands for it.
        """
        records = self.records()
        if not records:
            raise SystemExit(f"no runs under {self.path}")
        if wanted is None:
            latest = records[-1]
            if latest["by_mtime"]:
                print(f"note: {latest['name']} taken as the latest run by file "
                      "modification time; its record carries no timestamp",
                      file=sys.stderr)
            return latest["name"], latest["id"]
        matches = [r for r in records
                   if r["name"] == wanted or r["id"].startswith(wanted)
                   or (r["session"] or "").startswith(wanted)]
        sessions = {r["session"] for r in matches}
        if len(matches) > 1 and len(sessions) == 1 and None not in sessions:
            matches = matches[-1:]
        if len(matches) != 1:
            raise SystemExit(f"--run {wanted!r} matched {len(matches)} runs; known: "
                             + ", ".join(r["name"] for r in records))
        return matches[0]["name"], matches[0]["id"]

    def load(self, wanted=None):
        """The graph of one run, with any sidecar digests merged in."""
        name, run_id = self.resolve(wanted)
        session = None
        if self.kind == "nextflow":
            run = nextflow_store.pick_run(nextflow_store.load_history(self.path), run_id)
            session = run["session_id"]
            graph = nextflow_store.extract(self.path, session)
        elif self.kind == "horus-run":
            graph = horus.extract(self.path)
        elif self.kind == "horus":
            graph = horus.extract(self.path / run_id)
        else:
            graph = json.loads((self.path / f"{run_id}.json").read_text())
        graph["run"] = {"name": name, "id": run_id}
        for sidecar in self.sidecar_paths(run_id):
            if sidecar.is_file():
                merge_sidecar(graph, json.loads(sidecar.read_text()))
        if session:
            # The graph is the chain's, not the run's: two runs of one
            # session load the same graph, and a caller comparing them
            # needs to know that.
            graph["run"]["session"] = session
        sidecar = self.sidecar_path(run_id)
        if sidecar.is_file():
            merge_sidecar(graph, json.loads(sidecar.read_text()))
        return graph

    def sidecar_paths(self, run_id):
        """
        Every sidecar that may hold this run's digests: the one filed under
        the current key first, then any filed under a run hash of the same
        chain by an earlier Clew, so digests already on disk keep counting.
        """
        paths = [self.sidecar_path(run_id)]
        if self.kind == "nextflow":
            history = nextflow_store.load_history(self.path)
            session = nextflow_store.pick_run(history, run_id)["session_id"]
            for run in history:
                if run["session_id"] == session:
                    legacy = self.path / SIDECAR_DIR / f"{run['run_hash']}.digests.json"
                    if legacy not in paths:
                        paths.append(legacy)
        return paths

    def sidecar_key(self, run_id):
        """
        What a sidecar is filed under. A store graph is the whole resume
        chain, so its digests belong to the session, not to whichever run
        name was typed; a digest written under one name must be found
        under the other.
        """
        if self.kind == "nextflow":
            run = nextflow_store.pick_run(nextflow_store.load_history(self.path), run_id)
            return run["session_id"]
        return run_id

    def sidecar_path(self, run_id):
        base = self.path.parent if self.kind == "horus-run" else self.path
        return base / SIDECAR_DIR / f"{self.sidecar_key(run_id)}.digests.json"

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
