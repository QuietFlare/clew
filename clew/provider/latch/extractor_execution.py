"""
Latch executions, read into the graph.

Latch keeps one execution graph node per task behind the GraphQL API its SDK
uses, with status, timings, cost, the Flyte literal maps of inputs and
outputs, and the workflow's commit and image hashes. Files are named by
latch:// path, so edges join on that path. --records DIR reads a saved
execution.json plus one literals file per node. --execution ID fetches the
same with the token latch login stores, and has not yet met a live
execution.

The task hash is the node id, the container "<workflow>@<image hash>", the
script the workflow commit, with optional price and duration_s.
"""

import base64
import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

from clew.graph.graph import EXTERNAL, task_status
from clew.contracts import Extractor

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


def producer_of(path, producers):
    """
    The task that produced this path, or the one that produced the longest
    directory above it. A file read out of another task's directory output
    has no exact match, and without this it would read as external.
    """
    if path in producers:
        return producers[path]
    parents = [p for p in producers if path.startswith(p.rstrip("/") + "/")]
    return producers[max(parents, key=len)] if parents else None


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
            "status": task_status(node.get("status")),
            "engine_status": node.get("status") or "",
            "script": commit,
            "workdir": "",
        }
        price = node.get("cost") if node.get("cost") is not None else node.get("price")
        if isinstance(price, (int, float)):
            task.setdefault("metrics", {})["price"] = price
        duration = seconds_between(node.get("startTime"), node.get("resolutionTime"))
        if duration is not None:
            task.setdefault("metrics", {})["duration_s"] = duration
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
            producer = producer_of(path, producers) or EXTERNAL
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


class Latch(Extractor):
    name = "latch"
    description = "a Latch execution, from saved records or the API"

    def add_arguments(self, parser):
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument("--records", help="directory with execution.json and literals")
        source.add_argument("--execution", help="execution id to fetch over the API")
        parser.add_argument("--token", help="API token; default ~/.latch/token")

    def extract(self, args):
        if args.records:
            return extract(load_records(args.records))
        token = args.token or token_from_env()
        if not token:
            print("clew: no API token; pass --token or run latch login", file=sys.stderr)
            raise SystemExit(2)
        return extract(fetch(args.execution, token))

    def summarize(self, graph, args):
        external = [e for e in graph["edges"] if e["producer"] == EXTERNAL]
        priced = [t for t in graph["tasks"].values() if "price" in t.get("metrics", {})]
        print(f"tasks              : {len(graph['tasks'])}")
        print(f"input files (edges): {len(graph['edges'])}")
        print(f"  external inputs  : {len(external)}")
        if priced:
            print(f"total price        : {sum(t['metrics']['price'] for t in priced):.2f}")
        self.coverage(graph)


main = Latch.main


if __name__ == "__main__":
    sys.exit(main())
