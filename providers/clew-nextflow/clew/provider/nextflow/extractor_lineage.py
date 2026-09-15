"""
Nextflow's lineage store, read into the graph.

Layout (lineage/v1beta1): .history/<run-hash> lists runs; <task-hash>/
.data.json is a TaskRun whose inputs are lid://<producer>/<file> or an
external path with a checksum; <task-hash>/<file>/.data.json is a
FileOutput. Hashes are abbreviated to XX/YYYYYY so graphs from the work tree
and the store compare node for node.

Scoped by session, not run. A cached task keeps the workflowRun of the run
that first executed it, so filtering by run drops every cached task
(nextflow-io/nextflow#7586). A task a resumed run re-executed is kept and
marked superseded rather than dropped, since its outputs may still be on
disk.

The store records no exit status; every graph says so in coverage.
"""

import json
import sys
from pathlib import Path

from clew.graph.graph import STATUS_UNRECORDED
from clew.contracts import Extractor

LID_PREFIX = "lid://"

FORMAT = "lineage/v1beta1"
"""The only store version this adapter claims to read."""

KNOWN_KINDS = frozenset({
    "WorkflowRun", "WorkflowOutput", "TaskRun", "TaskOutput", "FileOutput",
})
"""Record kinds this adapter understands. Anything else is reported."""

CONTENT_MODES = frozenset({"deep", "sha256"})
"""
Checksum modes that hash file content. `standard` and `lenient` hash the
path and metadata instead, so they change when a file is copied and cannot
identify a published copy of an artifact.
"""


def abbreviate(full_hash):
    """Full 32-char store hash -> the 'XX/YYYYYY' form work/ folders use."""
    return f"{full_hash[:2]}/{full_hash[2:8]}"


def load_history(store):
    """
    Parse .history: one run per file, tab-separated
    timestamp, run name, sessionId, lid://hash. Sorted oldest first.
    """
    runs = []
    history = Path(store) / ".history"
    if not history.is_dir():
        return runs
    for entry in history.iterdir():
        line = entry.read_text().strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        timestamp, name, session_id, lid = parts
        runs.append({
            "timestamp": timestamp,
            "name": name,
            "session_id": session_id,
            "run_hash": lid.removeprefix(LID_PREFIX),
        })
    runs.sort(key=lambda r: r["timestamp"])
    return runs


def pick_run(runs, wanted):
    """
    Resolve --run against run name, run-hash prefix, or sessionId prefix.
    No --run means the most recent run: the common case right after a run
    finishes, and the wrong one silently if you meant an older run, which
    is why --list-runs exists.
    """
    if not runs:
        raise SystemExit("no runs recorded in this store (.history is empty)")
    if not wanted:
        return runs[-1]
    matches = [r for r in runs
               if r["name"] == wanted
               or r["run_hash"].startswith(wanted)
               or r["session_id"].startswith(wanted)]
    if len(matches) != 1:
        names = ", ".join(r["name"] for r in runs)
        raise SystemExit(f"--run {wanted!r} matched {len(matches)} runs; known: {names}")
    return matches[0]


def chain_of(runs, session_id):
    """Every run sharing a session, oldest first: one resume chain."""
    return [r for r in runs if r["session_id"] == session_id]


def read_record(path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def iter_store_entries(store):
    """Yield (entry name, record) for every top-level record in the store."""
    for entry in sorted(Path(store).iterdir()):
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        record = read_record(entry / ".data.json")
        if record:
            yield entry.name, record


def iter_task_records(store):
    """Yield (full_hash, spec) for every TaskRun record in the store."""
    for name, record in iter_store_entries(store):
        # "#output" entries are outputs, not tasks.
        if "#" in name or record.get("kind") != "TaskRun":
            continue
        yield name, record.get("spec", {})


def check_version(seen):
    """
    Refuse a store version we do not know rather than guessing at its fields.

    A renamed field inside a version we cannot recognise is the change that
    quietly empties a graph, so an unknown version is a hard stop.
    """
    unknown = {v for v in seen if v and v != FORMAT}
    if unknown:
        raise SystemExit(
            f"clew: unsupported lineage store version {sorted(unknown)}, "
            f"this adapter reads {FORMAT}")


def run_of(spec):
    """The run hash a TaskRun record names, or '' when it names none."""
    return (spec.get("workflowRun") or "").removeprefix(LID_PREFIX)


def consumed_hashes(selected):
    """Full hashes of every task some task in the selection reads from."""
    consumed = set()
    for _full_hash, spec in selected:
        for inp in spec.get("input", []):
            for value in inp.get("value", []) if inp.get("type") == "path" else []:
                if isinstance(value, str) and value.startswith(LID_PREFIX):
                    consumed.add(value.removeprefix(LID_PREFIX).partition("/")[0])
    return consumed


def superseded_tasks(selected, run_order):
    """
    Task hashes replaced by a same-named task from a later run in
    `run_order`. Same-run repeats are shards, not versions; a run the
    history does not list cannot be placed; a version some task still reads
    from stays live. None of those is marked.
    """
    position = {run_hash: i for i, run_hash in enumerate(run_order)}
    consumed = consumed_hashes(selected)
    by_name = {}
    for full_hash, spec in selected:
        by_name.setdefault(spec.get("name", ""), []).append((full_hash, run_of(spec)))

    stale = set()
    for versions in by_name.values():
        if len(versions) < 2 or any(run not in position for _, run in versions):
            continue
        newest = max(position[run] for _, run in versions)
        stale.update(full_hash for full_hash, run in versions
                     if position[run] < newest and full_hash not in consumed)
    return stale


def digest_of(checksum):
    """
    The store's checksum as a Clew digest, or None when it hashes path and
    time rather than content. 'sha256:<hex>' when the mode is sha256, else
    'nextflow-deep:<hex>', which compares only with the same mode.
    """
    checksum = checksum or {}
    mode, value = checksum.get("mode", ""), checksum.get("value", "")
    if not value or mode not in CONTENT_MODES:
        return None
    return f"{'sha256' if mode == 'sha256' else 'nextflow-deep'}:{value}"


def task_edges(task_hash, spec):
    """
    Turn one TaskRun's input list into edges, in the same backwards
    direction the symlink extractor records (consumer <- producer).
    """
    edges = []
    consumer = abbreviate(task_hash)
    for inp in spec.get("input", []):
        if inp.get("type") != "path":
            continue  # val inputs are parameters, not artifacts
        values = inp.get("value", [])
        if not isinstance(values, list):
            # A single path is written bare; iterating a string would read
            # it one character at a time and record nothing.
            values = [values]
        for value in values:
            if isinstance(value, str) and value.startswith(LID_PREFIX):
                producer_hash, _, filename = value.removeprefix(LID_PREFIX).partition("/")
                edges.append({
                    "consumer": consumer,
                    "producer": abbreviate(producer_hash),
                    "filename": filename,
                    "target": value,
                })
            elif isinstance(value, dict) and value.get("path"):
                # External input: a file the pipeline did not produce. The
                # store gives us its checksum, kept in `target`; as `digest`
                # when it is a content hash.
                path = value["path"]
                checksum = (value.get("checksum") or {}).get("value", "")
                edge = {
                    "consumer": consumer,
                    "producer": "EXTERNAL",
                    "filename": Path(path).name,
                    "target": f"{path}#{checksum}" if checksum else path,
                }
                digest = digest_of(value.get("checksum"))
                if digest:
                    edge["digest"] = digest
                edges.append(edge)
    return edges


def task_outputs(store, task_hash):
    """
    The task's FileOutput records as ({relative_path: record}, workdir).
    Records nest under the store entry mirroring the file's path. The
    workdir is recovered from an output's absolute path, the store's only
    source for it.
    """
    task_dir = Path(store) / task_hash
    outputs = {}
    workdir = ""
    for data_json in task_dir.rglob(".data.json"):
        if data_json.parent == task_dir:
            continue  # the TaskRun record itself
        record = read_record(data_json)
        if not record or record.get("kind") != "FileOutput":
            continue
        rel = str(data_json.parent.relative_to(task_dir))
        outputs[rel] = record["spec"]
        absolute = record["spec"].get("path", "")
        if absolute and absolute.endswith(rel):
            workdir = absolute[: -len(rel)].rstrip("/")
    return outputs, workdir


def plural(count, noun):
    """`1 task version` / `3 task versions`, so notes read as English."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def coverage_notes(stale, kinds, modes, dangling):
    """
    What this graph could not see, in the reader's own words.

    Carried on the graph so it reaches evidence bundles and the dashboard,
    which both already render a coverage list. Counts printed to a terminal
    and then discarded are not evidence of anything.
    """
    notes = [
        "The lineage store records no task exit status, so a task that "
        "failed is indistinguishable from one that produced no files.",
    ]
    if stale:
        notes.append(
            f"{plural(len(stale), 'task version')} re-run by a later run in "
            "this resume chain, included and marked `superseded` because "
            "the outputs may still be on disk.")
    weak = sorted(m for m in modes if m and m not in CONTENT_MODES)
    if weak:
        notes.append(
            f"Checksums use Nextflow {', '.join(repr(m) for m in weak)} "
            "mode, which hashes path and metadata rather than content, so "
            "no output carries a content digest. Run clew digest on this "
            "graph before reclaim, drift or stitch, or record the next run "
            "with cache 'deep'.")
    unknown = sorted(k for k in kinds if k and k not in KNOWN_KINDS)
    if unknown:
        notes.append(
            "Record kinds this adapter does not read: "
            f"{', '.join(unknown)}.")
    if dangling:
        notes.append(
            f"{plural(dangling, 'input file')} resolving to a producer "
            "outside this session, reported as external.")
    return notes


def extract(store, session_id):
    """
    The graph for one resume chain in the common schema, plus output_details
    and coverage. Scoped by session so cached tasks, which keep the
    workflowRun that first executed them, stay in the chain that relied on
    them.
    """
    versions, kinds, modes = set(), set(), set()
    selected = []

    for name, record in iter_store_entries(store):
        versions.add(record.get("version", ""))
        kinds.add(record.get("kind", ""))
        if "#" in name or record.get("kind") != "TaskRun":
            continue
        spec = record.get("spec", {})
        if spec.get("sessionId") != session_id:
            continue
        selected.append((name, spec))

    check_version(versions)
    run_order = [r["run_hash"] for r in chain_of(load_history(store), session_id)]
    stale = superseded_tasks(selected, run_order)

    tasks, edges, outputs, output_details = {}, [], {}, {}
    for full_hash, spec in selected:
        abbrev = abbreviate(full_hash)
        task_files, workdir = task_outputs(store, full_hash)

        name = spec.get("name", "")
        # The store has no separate process field; the name is the process
        # plus an optional "(tag)". Strip the tag for `process`, keep the
        # full name for the domain adapters that parse the tag out of it.
        process = name.rsplit(" (", 1)[0] if " (" in name else name

        task = {
            "hash": abbrev,
            "task_id": None,  # not recorded in the store; key on hash anyway
            "name": name,
            "process": process,
            "container": spec.get("container", ""),
            "status": STATUS_UNRECORDED,  # the store records no exit status, see coverage
            "target": "",   # one machine per run; nothing to record
            "workdir": workdir,
            "workpath": f"{full_hash[:2]}/{full_hash[2:]}",
            "script": spec.get("script", ""),
        }
        # Absent unless true, so a chain with no re-runs reads exactly as it
        # did before this field existed.
        if full_hash in stale:
            task["superseded"] = True
        tasks[abbrev] = task

        edges.extend(task_edges(full_hash, spec))
        outputs[abbrev] = sorted(task_files)
        output_details[abbrev] = []
        for rel in sorted(task_files):
            detail = {"file": rel, "size": task_files[rel].get("size")}
            digest = digest_of(task_files[rel].get("checksum"))
            if digest:
                detail["digest"] = digest
            output_details[abbrev].append(detail)
        for spec_out in task_files.values():
            modes.add((spec_out.get("checksum") or {}).get("mode", ""))

    known = set(tasks)
    dangling = sum(1 for e in edges
                   if e["producer"] not in known and e["producer"] != "EXTERNAL")

    return {"tasks": tasks, "edges": edges, "outputs": outputs,
            "output_details": output_details,
            "coverage": coverage_notes(stale, kinds, modes, dangling)}


class NextflowStore(Extractor):
    name = "nextflow"
    description = "a Nextflow .lineage store, including Seqera Platform"

    def add_arguments(self, parser):
        parser.add_argument("--store", required=True, help="path to the .lineage directory")
        parser.add_argument("--run", help="run name, run-hash prefix, or sessionId prefix "
                                          "(default: most recent run)")
        parser.add_argument("--list-runs", action="store_true",
                            help="list recorded runs and exit")

    def records(self, path):
        path = Path(path)
        if (path / ".lineage").is_dir():
            path = path / ".lineage"
        if not (path / ".history").is_dir():
            return None
        return {"root": path,
                "runs": [{"name": r["name"], "id": r["run_hash"], "timestamp": r["timestamp"],
                          "session": r["session_id"]} for r in load_history(path)]}

    def load(self, root, run_id):
        run = pick_run(load_history(root), run_id)
        return extract(root, run["session_id"])

    def extract(self, args):
        runs = load_history(args.store)
        if args.list_runs:
            for r in runs:
                print(f"{r['timestamp']}  {r['name']:<22} {r['run_hash']}")
            return None
        self.run_record = pick_run(runs, args.run)
        self.chain = chain_of(runs, self.run_record["session_id"])
        return extract(args.store, self.run_record["session_id"])

    def summarize(self, graph, args):
        run, chain = self.run_record, self.chain
        known = set(graph["tasks"])
        resolved = [e for e in graph["edges"] if e["producer"] in known]
        external = [e for e in graph["edges"] if e["producer"] == "EXTERNAL"]
        dangling = [e for e in graph["edges"]
                    if e["producer"] not in known and e["producer"] != "EXTERNAL"]
        stale = [t for t in graph["tasks"].values() if t.get("superseded")]

        print(f"run                : {run['name']} ({run['run_hash'][:8]}, {run['timestamp']})")
        print(f"session            : {run['session_id']}")
        if len(chain) > 1:
            print(f"  resume chain     : {len(chain)} runs, "
                  f"{', '.join(r['name'] for r in chain)}")
        print(f"tasks in session   : {len(graph['tasks'])}")
        if stale:
            print(f"  superseded       : {len(stale)}")
        print(f"input files (edges): {len(graph['edges'])}")
        print(f"  resolved to task : {len(resolved)}")
        print(f"  external inputs  : {len(external)}")
        print(f"  DANGLING         : {len(dangling)}")
        if dangling:
            # Another pipeline sharing the store, or a store pruned since the run.
            print("\n=== DANGLING (producer not a task in this session) ===")
            for e in dangling[:10]:
                print(f"{e['consumer']}  <-  {e['producer']}  ({e['filename']})")
        self.coverage(graph)


main = NextflowStore.main


if __name__ == "__main__":
    sys.exit(main())
