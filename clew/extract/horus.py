"""
Clew — lineage adapter for horus-lineage run directories.

WHY THIS EXISTS
---------------
The other three adapters read Nextflow. Horus is a different engine with a
property Nextflow does not have: every task can run on a different machine,
so a run's outputs are scattered across a laptop, a cluster and whatever
else the workflow named. Paths alone cannot join that back together.

horus-lineage records a content digest for every input and output, computed
on the machine holding the bytes. That is what this adapter joins on, so a
graph closes across machines the same way it closes on one.

THE RUN DIRECTORY, AS horus-lineage WRITES IT
---------------------------------------------
    run.json          the plan: run id, workflow, timings, final status
    definition.json   the projected workflow: tasks and declared edges
    <task>.<hash>.json    one record per task
    records.jsonl     the same records, one per line, when merged

Both record layouts are read. A task record carries status, the resolved
command, environment and code digests, and every input and output with its
sha256.

WHAT JOINS TO WHAT
------------------
Edges come from digests: an input whose sha256 equals some task's output
sha256 was produced by that task. An input matching no output came from
outside the run.

Declared edges from definition.json fill the gaps, because two kinds of
artifact have no digest. Folders, which the engine cannot hash, and
subworkflow ports, which are boundary placeholders with no file on disk.
Without the declared fallback those tasks would read as edgeless.

Digests alone are not enough for a different reason too: a task that copies
its input to its output produces identical bytes, so a pure digest join
reports it as its own producer. Self-edges are dropped.

HONEST LIMITS
-------------
Skipped tasks are recorded in full, with digests, so a cached run gives the
same graph as a fresh one. That is the point of the format, but it means a
task's `status` is often `skipped` rather than `completed`, and the record
describes outputs that already existed rather than work just done.

A record whose `incomplete` names `digests_disabled` or `digests_partial`
has edges this adapter cannot see. Those tasks are still emitted, so they
appear as nodes, and their missing edges fail open to EXTERNAL rather than
being silently dropped.
"""

import argparse
import json
from pathlib import Path

RECORD_FORMAT = "horus-lineage/v1"
PLAN = "run.json"
DEFINITION = "definition.json"
MERGED = "records.jsonl"


def load_records(run_dir):
    """
    Every task record in a run directory, whichever layout it uses.
    """
    run_dir = Path(run_dir)
    merged = run_dir / MERGED
    if merged.exists():
        return [json.loads(line) for line in
                merged.read_text().splitlines() if line.strip()]

    return [json.loads(path.read_text())
            for path in sorted(run_dir.glob("*.json"))
            if path.name not in (PLAN, DEFINITION)]


def check_format(records, plan):
    """
    Refuse a version we do not know rather than guessing at its fields.
    """
    seen = {r.get("format") for r in records} | {plan.get("format")}
    unknown = {f for f in seen if f and f != RECORD_FORMAT}
    if unknown:
        raise SystemExit(
            f"clew: unsupported record format {sorted(unknown)}, "
            f"this adapter reads {RECORD_FORMAT}")


def node_id(record):
    """
    A task's node id: run-scoped, so several runs load into one graph
    without colliding. Horus task ids are readable, so they are kept.
    """
    return f"{record['run']}/{record['task']['id']}"


def environment_of(record):
    """
    What re-running this task would need, in the field Clew calls
    container. Horus separates the environment (executor) from what runs
    inside it (runtime), and the executor is the closer analogue.
    """
    env = record.get("environment") or {}
    executor = env.get("executor") or {}
    kind, digest = executor.get("kind", ""), executor.get("sha256", "")
    return f"{kind}@{digest[:12]}" if kind and digest else kind


def script_of(record):
    """
    The first code file this task ran, as re-execution evidence.
    """
    code = record.get("code") or []
    return code[0]["path"] if code else ""


def labels_of(entry):
    """
    An artifact's labels, keeping only string keys and values.

    The vocabulary is the domain's, never this module's: whatever the
    workflow author wrote is passed through untouched.
    """
    raw = entry.get("labels")
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items()
            if isinstance(k, str) and isinstance(v, str)}


def merged_labels(record):
    """
    Every label on everything the task touched, inputs and outputs.

    A later key wins, which only matters when one task's own artifacts
    disagree, and then either answer is equally arbitrary.
    """
    merged = {}
    for side in ("inputs", "outputs"):
        for entry in record.get(side, []):
            merged.update(labels_of(entry))
    return merged


def producers_by_digest(records):
    """
    sha256 -> the node that produced it.

    A digest can be produced more than once, by a pass-through task or by
    two tasks that genuinely agree. The first producer in run order wins,
    which keeps the edge pointing at the origin rather than at a copy.
    """
    producers = {}
    for record in records:
        for output in record.get("outputs", []):
            digest = output.get("sha256")
            if digest:
                producers.setdefault(digest, node_id(record))
    return producers


def declared_producers(definition, run):
    """
    (consumer task id, input id) -> producer node, from the workflow's own
    edges. Used only where a digest is missing.
    """
    declared = {}
    for edge in (definition or {}).get("edges", []):
        source, target = edge.get("source"), edge.get("target")
        target_input = edge.get("target_input")
        if not source or not target or not target_input:
            continue
        if source.startswith("artifact-"):
            continue          # a root artifact, external by definition
        declared[(target, target_input)] = f"{run}/{source}"
    return declared


def extract(run_dir):
    """
    Build the common graph schema from one horus-lineage run directory.
    """
    run_dir = Path(run_dir)
    plan_path = run_dir / PLAN
    if not plan_path.exists():
        raise SystemExit(f"clew: no {PLAN} in {run_dir}")

    plan = json.loads(plan_path.read_text())
    records = load_records(run_dir)
    check_format(records, plan)

    definition_path = run_dir / DEFINITION
    definition = (json.loads(definition_path.read_text())
                  if definition_path.exists() else {})

    run = plan["run"]
    producers = producers_by_digest(records)
    declared = declared_producers(definition, run)

    tasks, edges, outputs = {}, [], {}
    for record in records:
        task = record["task"]
        node = node_id(record)

        tasks[node] = {
            "hash": node,
            "task_id": task.get("id"),
            "name": task.get("name") or task.get("id"),
            "process": task.get("definition_id") or task.get("id"),
            "container": environment_of(record),
            "status": (task.get("status") or "").upper(),
            "target": (record.get("target") or {}).get("location_id") or "",
            "workdir": record.get("working_dir") or "",
            "script": script_of(record),
        }

        for entry in record.get("inputs", []):
            digest = entry.get("sha256")
            producer = producers.get(digest) if digest else None
            if producer is None:
                producer = declared.get((task["id"], entry.get("id")))
            if producer == node:
                # A pass-through task copies its input to its output, so
                # the bytes match its own. It did not produce its input.
                producer = declared.get((task["id"], entry.get("id")))
            edges.append({
                "consumer": node,
                "producer": producer or "EXTERNAL",
                "filename": Path(entry.get("path", "")).name,
                "target": entry.get("path", ""),
                "labels": labels_of(entry),
            })

        # An output's labels reach the graph through the task, because no
        # edge carries them when nothing downstream consumes the artifact.
        tasks[node]["labels"] = merged_labels(record)

        outputs[node] = sorted(
            Path(o.get("path", "")).name
            for o in record.get("outputs", []) if o.get("path"))

    return {"tasks": tasks, "edges": edges, "outputs": outputs}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build a Clew graph from a horus-lineage run directory.")
    parser.add_argument("--run-dir", required=True,
                        help="a ~/.horus-lineage/<run-id>/ directory")
    parser.add_argument("--json-out", help="path to write the graph as JSON")
    args = parser.parse_args(argv)

    graph = extract(args.run_dir)
    known = set(graph["tasks"])
    external = [e for e in graph["edges"] if e["producer"] == "EXTERNAL"]
    dangling = [e for e in graph["edges"]
                if e["producer"] not in known and e["producer"] != "EXTERNAL"]
    skipped = [t for t in graph["tasks"].values() if t["status"] == "SKIPPED"]

    print(f"tasks in run       : {len(graph['tasks'])}")
    print(f"  skipped (cached) : {len(skipped)}")
    print(f"input files (edges): {len(graph['edges'])}")
    print(f"  external inputs  : {len(external)}")
    print(f"  DANGLING         : {len(dangling)}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(graph, indent=2))
        print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()
