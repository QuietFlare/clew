"""Read runs straight from the engine's record; Clew keeps only a digest sidecar beside it."""

import json
import sys
from pathlib import Path

from clew.contracts import Extractor, discover

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
    """
    An engine's record on disk. The extractor that recognises the path
    lists its runs and loads one; a directory of graph JSON files needs
    no extractor. Everything else here is engine-neutral: ordering,
    resolving a name, the sidecar.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.extractor = None
        for extractor in discover(Extractor).values():
            found = extractor.records(self.path)
            if found is not None:
                self.extractor, self.root = extractor, Path(found["root"])
                self.kind = extractor.name
                self._runs = found["runs"]
                return
        if self.path.is_dir() and any(c.suffix == ".json" for c in self.path.iterdir()):
            self.kind, self.root = "graphs", self.path
            self._runs = [{"name": c.stem, "id": c.stem,
                           "timestamp": recorded_timestamp(c),
                           "mtime": c.stat().st_mtime}
                          for c in self.path.iterdir() if c.suffix == ".json"]
            return
        raise SystemExit(f"{self.path} is not an engine record any installed extractor "
                         "recognises, or a directory of graph JSON files")

    def records(self):
        """[{name, id, timestamp, session, by_mtime}] oldest first."""
        found = []
        for r in self._runs:
            stamp = r.get("timestamp") or ""
            found.append({"name": r["name"], "id": r["id"], "timestamp": stamp,
                          "session": r.get("session"), "by_mtime": not stamp,
                          "_mtime": r.get("mtime", 0)})
        # Timestamps and mtimes do not compare, so recorded ones sort
        # among themselves and the rest fall in by mtime after them.
        found.sort(key=lambda r: (r["by_mtime"], r["timestamp"], r["_mtime"]))
        for r in found:
            del r["_mtime"]
        return found

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

    def session_of(self, run_id):
        return next((r["session"] for r in self.records() if r["id"] == run_id), None)

    def load(self, wanted=None):
        """The graph of one run, with any sidecar digests merged in."""
        name, run_id = self.resolve(wanted)
        if self.extractor:
            graph = self.extractor.load(self.root, run_id)
        else:
            graph = json.loads((self.root / f"{run_id}.json").read_text())
        graph["run"] = {"name": name, "id": run_id}
        for sidecar in self.sidecar_paths(run_id):
            if sidecar.is_file():
                merge_sidecar(graph, json.loads(sidecar.read_text()))
        session = self.session_of(run_id)
        if session:
            # The graph is the chain's, not the run's: two runs of one
            # session load the same graph, and a caller comparing them
            # needs to know that.
            graph["run"]["session"] = session
        return graph

    def sidecar_key(self, run_id):
        """
        A resumed chain's digests belong to the session, not to whichever
        run name was typed; a digest written under one must be found under
        the other. Engines without sessions key by run.
        """
        return self.session_of(run_id) or run_id

    def sidecar_paths(self, run_id):
        """The sidecar under the current key, then any an earlier Clew filed under a run of the same chain."""
        paths = [self.sidecar_path(run_id)]
        session = self.session_of(run_id)
        if session:
            for r in self.records():
                if r["session"] == session:
                    legacy = self.root / SIDECAR_DIR / f"{r['id']}.digests.json"
                    if legacy not in paths:
                        paths.append(legacy)
        return paths

    def sidecar_path(self, run_id):
        return self.root / SIDECAR_DIR / f"{self.sidecar_key(run_id)}.digests.json"

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
