"""
Clew: lineage adapter for Latch executions.

Latch keeps per-task records behind the GraphQL API its SDK uses: one
execution graph node per task with status, timings, cost and the inputs
and outputs it ran with, and the workflow's commit and image hashes. The
inputs and outputs are Flyte literal maps that name files by latch://
path, so edges join on that path and two executions stitch at a shared
path the same way two Nextflow runs do.

Two ways in. `--records DIR` reads a saved execution.json plus one
literals file per node, which is how the fixtures and tests work.
`--execution ID` fetches the same over the API with the token that
`latch login` stores. The API path has not yet been run against a live
execution. The schema comes from introspection; the literal-map shape
comes from Flyte's JSON encoding.

The task hash is the execution graph node id. The container is
"<workflow name>@<image hash>", since every task in an execution runs
the workflow's image. The script field carries the workflow commit hash.
Optional `price` and `duration_s` per task.
"""

import argparse
import base64
import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

from clew.core.graph import EXTERNAL

API = "https://vacuole.latch.bio/graphql"
TOKEN_FILE = Path.home() / ".latch" / "token"

QUERY = """
query Execution($id: BigInt!) {
  executionInfo(id: $id) {
    id displayName status price startTime resolutionTime
    workflow { name version commitHash dockerHash }
    executionGraphNodesByExecutionId {
      nodes {
        id status cost price startTime resolutionTime inputsUrl outputsUrl
        workflowGraphNode { nodeName displayName taskInfo { displayName version } }
      }
    }
  }
}
"""


def load_records(records_dir):
    """execution.json plus <node id>.inputs.json and <node id>.outputs.json."""
    records_dir = Path(records_dir)
    execution = json.loads((records_dir / "execution.json").read_text())
    literals = {}
    for node in execution["executionGraphNodesByExecutionId"]["nodes"]:
        entry = {}
        for kind in ("inputs", "outputs"):
            path = records_dir / f"{node['id']}.{kind}.json"
            if path.exists():
                entry[kind] = json.loads(path.read_text())
        literals[str(node["id"])] = entry
    return {"execution": execution, "literals": literals}


def latch_paths(value):
    """Every latch:// path inside a Flyte literal map, in order."""
    if isinstance(value, str):
        return [value] if value.startswith("latch://") else []
    if isinstance(value, dict):
        return [p for v in value.values() for p in latch_paths(v)]
    if isinstance(value, list):
        return [p for v in value for p in latch_paths(v)]
    return []


def seconds_between(start, end):
    try:
        a = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (b - a).total_seconds()


def extract(records):
    """Build the common graph schema from one execution and its literals."""
    execution = records["execution"]
    literals = records.get("literals", {})
    workflow = execution.get("workflow") or {}
    image = workflow.get("dockerHash") or ""
    container = f"{workflow.get('name', '')}@{image}" if image else workflow.get("name", "")
    commit = workflow.get("commitHash") or ""

    nodes = execution["executionGraphNodesByExecutionId"]["nodes"]
    tasks, outputs, producers = {}, {}, {}
    for node in nodes:
        node_id = str(node["id"])
        graph_node = node.get("workflowGraphNode") or {}
        task_info = graph_node.get("taskInfo") or {}
        name = graph_node.get("displayName") or graph_node.get("nodeName") or node_id
        task = {
            "hash": node_id,
            "task_id": node_id,
            "name": name,
            "process": task_info.get("displayName") or name,
            "container": container,
            "status": (node.get("status") or "").upper(),
            "script": commit,
            "workdir": "",
        }
        price = node.get("cost") if node.get("cost") is not None else node.get("price")
        if isinstance(price, (int, float)):
            task["price"] = price
        duration = seconds_between(node.get("startTime"), node.get("resolutionTime"))
        if duration is not None:
            task["duration_s"] = duration
        tasks[node_id] = task

        produced = list(dict.fromkeys(
            latch_paths(literals.get(node_id, {}).get("outputs"))))
        outputs[node_id] = sorted(Path(p).name for p in produced)
        for path in produced:
            producers.setdefault(path, node_id)

    edges = []
    for node in nodes:
        node_id = str(node["id"])
        for path in dict.fromkeys(latch_paths(literals.get(node_id, {}).get("inputs"))):
            producer = producers.get(path, EXTERNAL)
            if producer == node_id:
                producer = EXTERNAL
            edges.append({
                "consumer": node_id,
                "producer": producer,
                "filename": Path(path).name,
                "target": path,
            })

    return {"tasks": tasks, "edges": edges, "outputs": outputs}


def token_from_env():
    """latch login writes the token to ~/.latch/token."""
    token = os.environ.get("LATCH_TOKEN")
    if token:
        return token
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip() or None
    return None


def graphql(query, variables, token, api=API):
    request = urllib.request.Request(
        api, data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"Latch-SDK-Token {token}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(request) as response:
        payload = json.loads(response.read())
    if payload.get("errors"):
        raise RuntimeError(payload["errors"][0].get("message", "GraphQL error"))
    return payload["data"]


def fetch_literals(url):
    """A node's inputs or outputs, as the JSON the signed URL serves."""
    if not url:
        return None
    with urllib.request.urlopen(url) as response:
        raw = response.read()
    try:
        return json.loads(raw)
    except ValueError:
        return json.loads(base64.b64decode(raw))


def fetch(execution_id, token, api=API):
    """One execution with its nodes, plus each node's literal maps."""
    data = graphql(QUERY, {"id": execution_id}, token, api)
    execution = data["executionInfo"]
    if execution is None:
        raise SystemExit(f"clew: no execution {execution_id} visible to this token")
    literals = {}
    for node in execution["executionGraphNodesByExecutionId"]["nodes"]:
        literals[str(node["id"])] = {
            "inputs": fetch_literals(node.get("inputsUrl")),
            "outputs": fetch_literals(node.get("outputsUrl")),
        }
    return {"execution": execution, "literals": literals}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build a Clew graph from a Latch execution.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--records", help="directory with execution.json and literals")
    source.add_argument("--execution", help="execution id to fetch over the API")
    parser.add_argument("--token", help="API token; default ~/.latch/token")
    parser.add_argument("--json-out", help="path to write the graph as JSON")
    args = parser.parse_args(argv)

    if args.records:
        records = load_records(args.records)
    else:
        token = args.token or token_from_env()
        if not token:
            print("clew: no API token; pass --token or run latch login", file=sys.stderr)
            return 2
        records = fetch(args.execution, token)

    graph = extract(records)
    external = [e for e in graph["edges"] if e["producer"] == EXTERNAL]
    priced = [t for t in graph["tasks"].values() if "price" in t]

    print(f"tasks              : {len(graph['tasks'])}")
    print(f"input files (edges): {len(graph['edges'])}")
    print(f"  external inputs  : {len(external)}")
    if priced:
        print(f"total price        : {sum(t['price'] for t in priced):.2f}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(graph, indent=2))
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
