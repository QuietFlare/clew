"""
Clew — lineage adapter for Nextflow's native data lineage store (25.04+).

WHY THIS EXISTS
---------------
The work/ symlink extractor rebuilds history from an accident of staging.
Nextflow's lineage feature records the same facts on purpose: every task,
every output file, and every input reference, written as JSON records into
a .lineage store at run time. When the store exists, it is the better
witness — inputs are typed, externals carry checksums, and each task names
the exact workflow run it belongs to, so filtering one run out of a shared
store is a field lookup instead of a heuristic.

Both extractors emit the SAME graph JSON. Everything downstream (blast.py,
core/, domains/) neither knows nor cares which one produced its input.

THE STORE, AS FOUND ON DISK (lineage/v1beta1)
---------------------------------------------
    .lineage/
      .history/<run-hash>          one line per run:
                                   timestamp \t name \t sessionId \t lid://hash
      <task-hash>/.data.json       kind: TaskRun
                                   spec: name, container, script, workflowRun,
                                   input[] — path inputs are either
                                     "lid://<producer-hash>/<file>"   (internal)
                                     {"path": ..., "checksum": ...}   (external)
      <task-hash>/<file>/.data.json  kind: FileOutput
                                   spec.path = absolute path in work/
      <hash>#output/               workflow-level output records; not tasks

Task hashes here are the full 32 hex characters; work/ folders and the
symlink extractor abbreviate them to "XX/YYYYYY". We abbreviate the same way
so graphs from both extractors are comparable node for node.

KNOWN LIMIT
-----------
A resumed run reuses cached tasks from earlier runs, and a cached task's
record names the run that first executed it. Filtering by run therefore
excludes cached tasks, and their consumers show up as DANGLING rather than
being silently merged in. Dangling is the honest answer until resume
semantics are modelled properly.

USAGE
-----
    python3 extract_from_lineage_store.py --store /path/to/.lineage --list-runs
    python3 extract_from_lineage_store.py \
        --store /path/to/.lineage --run tender_mccarthy --json-out graph.json
"""

import argparse
import json
from pathlib import Path

LID_PREFIX = "lid://"


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


def read_record(path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def iter_task_records(store):
    """Yield (full_hash, spec) for every TaskRun record in the store."""
    for entry in Path(store).iterdir():
        # "#output" entries are workflow-level outputs; .history is bookkeeping.
        if entry.name.startswith(".") or "#" in entry.name or not entry.is_dir():
            continue
        record = read_record(entry / ".data.json")
        if record and record.get("kind") == "TaskRun":
            yield entry.name, record.get("spec", {})


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


def extract(store, run_hash):
    """
    Build the graph for one run, in the exact schema extract_lineage.py
    emits: {"tasks": {...}, "edges": [...], "outputs": {...}}.
    """
    tasks = {}
    edges = []
    outputs = {}

    for full_hash, spec in iter_task_records(store):
        if spec.get("workflowRun", "").removeprefix(LID_PREFIX) != run_hash:
            continue

        abbrev = abbreviate(full_hash)
        task_files, workdir = task_outputs(store, full_hash)

        name = spec.get("name", "")
        # The store has no separate process field; the name is the process
        # plus an optional "(tag)". Strip the tag for `process`, keep the
        # full name for the domain adapters that parse the tag out of it.
        process = name.rsplit(" (", 1)[0] if " (" in name else name

        tasks[abbrev] = {
            "hash": abbrev,
            "task_id": None,  # not recorded in the store; key on hash anyway
            "name": name,
            "process": process,
            "container": spec.get("container", ""),
            "status": "",  # the store only records tasks that ran
            "workdir": workdir,
            "script": spec.get("script", ""),
        }
        edges.extend(task_edges(full_hash, spec))
        outputs[abbrev] = sorted(task_files)

    return {"tasks": tasks, "edges": edges, "outputs": outputs}


def main():
    parser = argparse.ArgumentParser(
        description="Build a Clew graph from a Nextflow .lineage store.")
    parser.add_argument("--store", required=True, help="path to the .lineage directory")
    parser.add_argument("--run", help="run name, run-hash prefix, or sessionId prefix "
                                      "(default: most recent run)")
    parser.add_argument("--list-runs", action="store_true", help="list recorded runs and exit")
    parser.add_argument("--json-out", help="path to write the graph as JSON")
    args = parser.parse_args()

    runs = load_history(args.store)

    if args.list_runs:
        for r in runs:
            print(f"{r['timestamp']}  {r['name']:<22} {r['run_hash']}")
        return

    run = pick_run(runs, args.run)
    graph = extract(args.store, run["run_hash"])

    known = set(graph["tasks"])
    resolved = [e for e in graph["edges"] if e["producer"] in known]
    external = [e for e in graph["edges"] if e["producer"] == "EXTERNAL"]
    dangling = [e for e in graph["edges"]
                if e["producer"] not in known and e["producer"] != "EXTERNAL"]

    print(f"run                : {run['name']} ({run['run_hash'][:8]}, {run['timestamp']})")
    print(f"tasks in run       : {len(graph['tasks'])}")
    print(f"input files (edges): {len(graph['edges'])}")
    print(f"  resolved to task : {len(resolved)}")
    print(f"  external inputs  : {len(external)}")
    print(f"  DANGLING         : {len(dangling)}")

    if dangling:
        # Usually cached tasks from an earlier run (resume), which this run's
        # filter correctly refuses to claim. See KNOWN LIMIT in the header.
        print("\n=== DANGLING (producer not a task in this run) ===")
        for e in dangling[:10]:
            print(f"{e['consumer']}  <-  {e['producer']}  ({e['filename']})")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(graph, indent=2))
        print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()
