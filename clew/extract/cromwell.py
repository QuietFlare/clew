"""
Clew: lineage adapter for Cromwell workflow metadata.

Every call lists its inputs and outputs as paths, so edges join on path.
Details and limits: docs/sources.md, "Cromwell".
"""

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from clew.graph.graph import relative_to, task_status

EXTERNAL = "EXTERNAL"
SCHEMES = ("gs://", "s3://", "drs://", "http://", "https://", "az://", "file://")


def load_metadata(path):
    return json.loads(Path(path).read_text())


def strings_in(value):
    """Every string inside a WDL value, however nested."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings_in(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings_in(v)]
    return []


def looks_like_file(text):
    return text.startswith("/") or text.startswith(SCHEMES) or "/" in text


def producer_of(path, producers):
    """
    The call that produced this path, or the one that produced the longest
    directory above it. A file read out of another call's Directory output
    has no exact match, and without this it would read as external.
    """
    if path in producers:
        return producers[path]
    parents = [p for p in producers if path.startswith(p.rstrip("/") + "/")]
    return producers[max(parents, key=len)] if parents else None


def last_attempts(attempts):
    """The final attempt of each shard, in shard order."""
    latest = {}
    for attempt in attempts:
        shard = attempt.get("shardIndex", -1)
        if shard not in latest or attempt.get("attempt", 1) >= latest[shard].get("attempt", 1):
            latest[shard] = attempt
    return [latest[shard] for shard in sorted(latest)]


def walk_calls(metadata, prefix=""):
    """(node id, call fqn, attempt) for every leaf call, subworkflows flattened."""
    for fqn, attempts in (metadata.get("calls") or {}).items():
        for attempt in last_attempts(attempts):
            shard = attempt.get("shardIndex", -1)
            node = f"{prefix}{fqn}" + (f"/shard-{shard}" if shard >= 0 else "")
            sub = attempt.get("subWorkflowMetadata")
            if isinstance(sub, dict):
                yield from walk_calls(sub, prefix=f"{node}/")
            else:
                attempt = dict(attempt)
                attempt["_unexpanded"] = bool(attempt.get("subWorkflowId"))
                yield node, fqn, attempt


def container_of(attempt):
    """The resolved image digest, else the declared image."""
    return (attempt.get("dockerImageUsed")
            or (attempt.get("runtimeAttributes") or {}).get("docker")
            or "")


def duration_of(attempt):
    try:
        start = datetime.fromisoformat(attempt["start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(attempt["end"].replace("Z", "+00:00"))
    except (KeyError, ValueError, AttributeError):
        return None
    return (end - start).total_seconds()


def extract(metadata):
    """Build the common graph schema from one workflow's metadata."""
    leaves = list(walk_calls(metadata))
    # Subworkflow calls nest under the outer workflow's root, so every call
    # is placed relative to the top-level root, whatever depth it ran at.
    root = metadata.get("workflowRoot") or ""

    producers = {}
    for node, _, attempt in leaves:
        for path in strings_in(attempt.get("outputs")):
            producers.setdefault(path, node)

    tasks, edges, outputs = {}, [], {}
    for node, fqn, attempt in leaves:
        shard = attempt.get("shardIndex", -1)
        task = {
            "hash": node,
            "task_id": node,
            "name": fqn + (f" (shard {shard})" if shard >= 0 else ""),
            "process": fqn,
            "container": container_of(attempt),
            "status": task_status(attempt.get("executionStatus")),
            "engine_status": attempt.get("executionStatus") or "",
            "script": attempt.get("commandLine") or "",
            "workdir": attempt.get("callRoot") or "",
        }
        # The call root holds inputs/ and execution/; the task's own files,
        # outputs included, are under execution/, so that is the directory
        # storage questions are asked about.
        call_path = relative_to(attempt.get("callRoot"), root)
        if call_path:
            task["workpath"] = f"{call_path}/execution"
        if (attempt.get("callCaching") or {}).get("hit"):
            task["cached"] = True
        if attempt["_unexpanded"]:
            task["unexpanded_subworkflow"] = attempt["subWorkflowId"]
        duration = duration_of(attempt)
        if duration is not None:
            task["duration_s"] = duration
        tasks[node] = task

        seen = set()
        for path in strings_in(attempt.get("inputs")):
            if path in seen:
                continue
            producer = producer_of(path, producers)
            if producer is None and not looks_like_file(path):
                continue
            seen.add(path)
            if producer == node:
                producer = None
            edges.append({
                "consumer": node,
                "producer": producer or EXTERNAL,
                "filename": Path(path).name,
                "target": path,
            })

        outputs[node] = sorted(
            {Path(p).name for p in strings_in(attempt.get("outputs"))})

    return {"tasks": tasks, "edges": edges, "outputs": outputs}


def fetch(server, workflow_id, token=None):
    """One workflow's metadata with every subworkflow expanded."""
    query = urllib.parse.urlencode({"expandSubWorkflows": "true"})
    url = f"{server.rstrip('/')}/api/workflows/v1/{workflow_id}/metadata?{query}"
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers)) as response:
        return json.loads(response.read())


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build a Clew graph from Cromwell workflow metadata.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--metadata",
                        help="metadata JSON, from `cromwell run -m` or the API")
    source.add_argument("--server", help="Cromwell server URL, with --workflow")
    parser.add_argument("--workflow", help="workflow id to fetch from --server")
    parser.add_argument("--token", help="bearer token for a server behind auth")
    parser.add_argument("--json-out", help="path to write the graph as JSON")
    args = parser.parse_args(argv)

    if args.metadata:
        metadata = load_metadata(args.metadata)
    else:
        if not args.workflow:
            print("clew: --server needs --workflow <id>", file=sys.stderr)
            return 2
        metadata = fetch(args.server, args.workflow, args.token)

    graph = extract(metadata)
    external = [e for e in graph["edges"] if e["producer"] == EXTERNAL]
    cached = [t for t in graph["tasks"].values() if t.get("cached")]
    unexpanded = [t for t in graph["tasks"].values()
                  if t.get("unexpanded_subworkflow")]

    print(f"workflow           : {metadata.get('workflowName', '?')} "
          f"{metadata.get('id', '')} {metadata.get('status', '')}")
    print(f"calls              : {len(graph['tasks'])}")
    print(f"  cache hits       : {len(cached)}")
    print(f"input files (edges): {len(graph['edges'])}")
    print(f"  external inputs  : {len(external)}")
    if unexpanded:
        print(f"  UNEXPANDED subworkflows: {len(unexpanded)} "
              f"(fetch with expandSubWorkflows=true)")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(graph, indent=2))
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
