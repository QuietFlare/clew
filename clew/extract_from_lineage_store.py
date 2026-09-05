"""
Clew — lineage adapter for Nextflow's native data lineage store (25.04+).

WHY THIS EXISTS
---------------
The work/ symlink extractor rebuilds history from an accident of staging.
Nextflow's lineage feature records the same facts on purpose: every task,
every output file, and every input reference, written as JSON records into
a .lineage store at run time. When the store exists, it is the better
witness — inputs are typed, externals carry checksums, and each task names
the session it belongs to, so pulling one pipeline out of a shared store is
a field lookup instead of a heuristic.

Both extractors emit the SAME graph JSON. Everything downstream (clew impact,
core/, domains/) neither knows nor cares which one produced its input.

THE STORE, AS FOUND ON DISK (lineage/v1beta1)
---------------------------------------------
    .lineage/
      .history/<run-hash>          one line per run:
                                   timestamp \t name \t sessionId \t lid://hash
      <task-hash>/.data.json       kind: TaskRun
                                   spec: name, sessionId, container, script,
                                   workflowRun, input[] — path inputs are
                                     "lid://<producer-hash>/<file>"   (internal)
                                     {"path": ..., "checksum": ...}   (external)
      <task-hash>/<file>/.data.json  kind: FileOutput
                                   spec.path = absolute path in work/
      <task-hash>#output/          kind: TaskOutput, carries createdAt
      <run-hash>#output/           workflow-level outputs; not tasks

Task hashes here are the full 32 hex characters; work/ folders and the
symlink extractor abbreviate them to "XX/YYYYYY". We abbreviate the same way
so graphs from both extractors are comparable node for node.

SCOPING: SESSION, NOT RUN
-------------------------
A resumed run reuses cached tasks, and a cached task writes no new record:
the one from the run that first executed it still stands, and still names
that run in `workflowRun`. Filtering by run therefore drops every cached
task, and a fully cached run appears to contain nothing at all.

`sessionId` is the field that spans a resume chain. Every run in the chain
shares it, so selecting on it returns the tasks the chain actually relied
on. Upstream confirmed this is the intended reading
(nextflow-io/nextflow#7586).

A chain can hold more than one version of the same task, when a resumed run
invalidated it and ran it again. The newest is live and the older ones are
history. They are kept and marked `superseded` rather than dropped, because
their outputs really were produced and may still be on disk: a deletion
plan that omits them under-reports, which is the one direction that must
never happen.

WHAT THIS SOURCE CANNOT SHOW
----------------------------
The store records no task exit status, so a task that failed is written
exactly like one that succeeded and simply produced no files. Every graph
built here says so in `coverage`, which travels with the graph into
evidence bundles and the dashboard rather than being printed and lost.

USAGE
-----
    clew extract-store --store /path/to/.lineage --list-runs
    clew extract-store \
        --store /path/to/.lineage --run tender_mccarthy --json-out graph.json
"""

import argparse
import json
from pathlib import Path

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
    finishes, and the wrong one silently if you meant an older run — which
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


def task_created_at(store, task_hash):
    """
    When this task's outputs were recorded, from its TaskOutput record.

    The only timestamp a task carries. Used to order two versions of the
    same task within a resume chain.
    """
    record = read_record(Path(store) / f"{task_hash}#output" / ".data.json")
    if not record:
        return ""
    return (record.get("spec") or {}).get("createdAt") or ""


def superseded_tasks(selected):
    """
    Task hashes replaced by a later version of the same task in this chain.

    Same name, different hash, ordered by when their outputs were recorded.
    When any version lacks a timestamp nothing is claimed, because guessing
    which one is live is worse than admitting the order is unknown.
    """
    by_name = {}
    for full_hash, spec, created in selected:
        by_name.setdefault(spec.get("name", ""), []).append((created, full_hash))

    stale = set()
    for versions in by_name.values():
        if len(versions) < 2 or any(not created for created, _ in versions):
            continue
        versions.sort()
        stale.update(full_hash for _, full_hash in versions[:-1])
    return stale


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
        for value in inp.get("value", []):
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
                # store gives us its checksum, which the symlink extractor
                # never could — kept in `target` for future content identity.
                path = value["path"]
                checksum = (value.get("checksum") or {}).get("value", "")
                edges.append({
                    "consumer": consumer,
                    "producer": "EXTERNAL",
                    "filename": Path(path).name,
                    "target": f"{path}#{checksum}" if checksum else path,
                })
    return edges


def task_outputs(store, task_hash):
    """
    Collect the task's FileOutput records: ({relative_path: record}, workdir).

    Output records live in subdirectories of the task's store entry, one per
    file, nested to mirror the file's path inside the task workdir. The
    workdir itself is recovered from any output's absolute path — the store
    has no other field for it.
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
            f"{plural(len(stale), 'task version')} superseded by a later "
            "run in this resume chain, included and marked `superseded` "
            "because the outputs may still be on disk.")
    weak = sorted(m for m in modes if m and m not in CONTENT_MODES)
    if weak:
        notes.append(
            f"Checksums use Nextflow {', '.join(repr(m) for m in weak)} "
            "mode, which hashes path and metadata rather than content, so "
            "they cannot identify a copy of a file.")
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
    Build the graph for one resume chain, in the exact schema clew
    extract-work emits: {"tasks": {...}, "edges": [...], "outputs": {...}}
    plus "output_details" (per-output size) and "coverage".

    Scoped by session rather than by run so that cached tasks, which keep
    the workflowRun of whichever run first executed them, are still part of
    the chain that relied on them.
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
        selected.append((name, spec, task_created_at(store, name)))

    check_version(versions)
    stale = superseded_tasks(selected)

    tasks, edges, outputs, output_details = {}, [], {}, {}
    for full_hash, spec, _created in selected:
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
            "status": "",  # the store records no exit status, see coverage
            "target": "",   # one machine per run; nothing to record
            "workdir": workdir,
            "script": spec.get("script", ""),
        }
        # Absent unless true, so a chain with no re-runs reads exactly as it
        # did before this field existed.
        if full_hash in stale:
            task["superseded"] = True
        tasks[abbrev] = task

        edges.extend(task_edges(full_hash, spec))
        outputs[abbrev] = sorted(task_files)
        output_details[abbrev] = [
            {"file": rel, "size": task_files[rel].get("size")}
            for rel in sorted(task_files)
        ]
        for spec_out in task_files.values():
            modes.add((spec_out.get("checksum") or {}).get("mode", ""))

    known = set(tasks)
    dangling = sum(1 for e in edges
                   if e["producer"] not in known and e["producer"] != "EXTERNAL")

    return {"tasks": tasks, "edges": edges, "outputs": outputs,
            "output_details": output_details,
            "coverage": coverage_notes(stale, kinds, modes, dangling)}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build a Clew graph from a Nextflow .lineage store.")
    parser.add_argument("--store", required=True, help="path to the .lineage directory")
    parser.add_argument("--run", help="run name, run-hash prefix, or sessionId prefix "
                                      "(default: most recent run)")
    parser.add_argument("--list-runs", action="store_true", help="list recorded runs and exit")
    parser.add_argument("--json-out", help="path to write the graph as JSON")
    args = parser.parse_args(argv)

    runs = load_history(args.store)

    if args.list_runs:
        for r in runs:
            print(f"{r['timestamp']}  {r['name']:<22} {r['run_hash']}")
        return

    run = pick_run(runs, args.run)
    chain = chain_of(runs, run["session_id"])
    graph = extract(args.store, run["session_id"])

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
        # A producer outside this session: another pipeline sharing the
        # store, or a store pruned since the run.
        print("\n=== DANGLING (producer not a task in this session) ===")
        for e in dangling[:10]:
            print(f"{e['consumer']}  <-  {e['producer']}  ({e['filename']})")

    print("\n=== what this graph does not cover ===")
    for note in graph["coverage"]:
        print(f"  - {note}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(graph, indent=2))
        print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()
