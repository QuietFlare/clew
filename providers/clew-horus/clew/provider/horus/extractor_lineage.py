"""
horus-lineage run directories, read into the graph.

Horus runs tasks on different machines, so paths cannot join a run back
together. horus-lineage records a sha256 for every input and output on the
machine holding the bytes, and edges join on those: an input matching a
task's output was produced by it, and one matching none is external.
Declared edges from definition.json fill in for folders and subworkflow
ports, which have no digest. A task that copies input to output would match
itself, so self-edges are dropped.

Layout: run.json, definition.json, one <task>.<hash>.json per task or a
records.jsonl. Skipped tasks are recorded with digests and map to CACHED. A
record marked digests_disabled or digests_partial has edges this cannot see;
they fail open to EXTERNAL.
"""

import json
import sys
from pathlib import Path

from clew.graph.graph import STATUS_CACHED, relative_to, task_status
from clew.contracts import Extractor

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


def duration_of(task):
    """Seconds between started_at and finished_at, when the record has both."""
    from datetime import datetime
    try:
        start = datetime.fromisoformat(task["started_at"])
        end = datetime.fromisoformat(task["finished_at"])
    except (KeyError, TypeError, ValueError):
        return None
    seconds = (end - start).total_seconds()
    return round(seconds, 3) if seconds >= 0 else None


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

    tasks, edges, outputs, output_details = {}, [], {}, {}
    for record in records:
        task = record["task"]
        node = node_id(record)

        tasks[node] = {
            "hash": node,
            "task_id": task.get("id"),
            "name": task.get("name") or task.get("id"),
            "process": task.get("definition_id") or task.get("id"),
            "container": environment_of(record),
            "status": task_status(task.get("status")),
            "engine_status": task.get("status") or "",
            "target": (record.get("target") or {}).get("location_id") or "",
            "workdir": record.get("working_dir") or "",
            "script": script_of(record),
        }
        workpath = relative_to(record.get("working_dir"), plan.get("run_directory"))
        if workpath:
            tasks[node]["workpath"] = workpath
        duration = duration_of(task)
        if duration is not None:
            tasks[node]["metrics"] = {"duration_s": duration}

        for entry in record.get("inputs", []):
            digest = entry.get("sha256")
            producer = producers.get(digest) if digest else None
            if producer is None:
                producer = declared.get((task["id"], entry.get("id")))
            if producer == node:
                # A pass-through task copies its input to its output, so
                # the bytes match its own. It did not produce its input.
                producer = declared.get((task["id"], entry.get("id")))
            edge = {
                "consumer": node,
                "producer": producer or "EXTERNAL",
                "filename": Path(entry.get("path", "")).name,
                "target": entry.get("path", ""),
                "labels": labels_of(entry),
            }
            if digest:
                edge["digest"] = f"sha256:{digest}"
            edges.append(edge)

        # An output's labels reach the graph through the task, because no
        # edge carries them when nothing downstream consumes the artifact.
        tasks[node]["labels"] = merged_labels(record)

        produced = sorted((o for o in record.get("outputs", []) if o.get("path")),
                          key=lambda o: Path(o["path"]).name)
        outputs[node] = [Path(o["path"]).name for o in produced]
        output_details[node] = []
        for o in produced:
            detail = {"file": Path(o["path"]).name, "size": o.get("size")}
            if o.get("sha256"):
                detail["digest"] = f"sha256:{o['sha256']}"
            output_details[node].append(detail)

    return {"tasks": tasks, "edges": edges, "outputs": outputs,
            "output_details": output_details}


class Horus(Extractor):
    name = "horus"
    description = "a horus-lineage run directory"

    def add_arguments(self, parser):
        parser.add_argument("--run-dir", required=True,
                            help="a ~/.horus-lineage/<run-id>/ directory")

    def records(self, path):
        from clew.extract.runs import recorded_timestamp
        path = Path(path)
        if (path / PLAN).is_file():  # one run: the sidecar lives beside it
            return {"root": path.parent,
                    "runs": [{"name": path.name, "id": path.name,
                              "timestamp": recorded_timestamp(path / PLAN)}]}
        runs = [c for c in path.iterdir() if (c / PLAN).is_file()] if path.is_dir() else []
        if not runs:
            return None
        return {"root": path,
                "runs": [{"name": c.name, "id": c.name,
                          "timestamp": recorded_timestamp(c / PLAN),
                          "mtime": c.stat().st_mtime} for c in runs]}

    def load(self, root, run_id):
        return extract(Path(root) / run_id)

    def extract(self, args):
        return extract(args.run_dir)

    def summarize(self, graph, args):
        known = set(graph["tasks"])
        external = [e for e in graph["edges"] if e["producer"] == "EXTERNAL"]
        dangling = [e for e in graph["edges"]
                    if e["producer"] not in known and e["producer"] != "EXTERNAL"]
        skipped = [t for t in graph["tasks"].values() if t["status"] == STATUS_CACHED]
        print(f"tasks in run       : {len(graph['tasks'])}")
        print(f"  skipped (cached) : {len(skipped)}")
        print(f"input files (edges): {len(graph['edges'])}")
        print(f"  external inputs  : {len(external)}")
        print(f"  DANGLING         : {len(dangling)}")
        self.coverage(graph)


main = Horus.main


if __name__ == "__main__":
    sys.exit(main())
