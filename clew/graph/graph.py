"""
Clew core — questions you can ask any graph.

Everything here reads the common schema and nothing else: `tasks`,
`edges`, `outputs`. No engine, no domain.

These lived in a domains/ adapter, re-exported unchanged by every other
one. That placement had a cost: reaching them at all meant going through
an adapter, and paying for its inputs. A domain decides what a SUBJECT
is; that is the whole of its job.
"""

import json
from pathlib import Path


def container_entry_nodes(graph, needle):
    """Entry nodes for a tool-defect trigger: tasks whose container matches."""
    subject = f"container:{needle}"
    nodes = sorted(
        h for h, t in graph["tasks"].items()
        if needle in (t.get("container") or "")
    )
    return {subject: nodes}


def external_input_entry_nodes(graph, filename):
    """
    Entry nodes for an upstream-input trigger: tasks that consumed an
    EXTERNAL file with this basename. This is the reference-update /
    load-bearing-input case.
    """
    subject = f"input:{filename}"
    nodes = set()
    for edge in graph["edges"]:
        if edge["producer"] != "EXTERNAL":
            continue
        if Path(edge["filename"]).name == filename:
            nodes.add(edge["consumer"])
    return {subject: sorted(nodes)}


def load_assertions(path):
    """
    Externally-asserted facts the pipeline cannot know about itself
    (publication, so far). Returns {task_hash: assertion_record}; a missing
    path honestly means "publication status unknown".
    """
    if not path:
        return {}
    data = json.loads(Path(path).read_text())
    return {rec["task"]: rec for rec in data.get("published", [])}


def outputs_for(graph, task_hashes):
    """Every file produced by the given tasks, as 'hash/filename'."""
    files = []
    for task_hash in sorted(task_hashes):
        for filename in graph["outputs"].get(task_hash, []):
            files.append(f"{task_hash}/{filename}")
    return files


def describe(graph, task_hash):
    """Short human label for a task: the process name without its full path."""
    task = graph["tasks"].get(task_hash, {})
    return (task.get("process", "") or "?").split(":")[-1]


TASK_FIELDS = ("hash", "name", "process", "container", "status",
               "script", "workdir")
OPTIONAL_TASK_FIELDS = ("task_id", "target")
EDGE_FIELDS = ("consumer", "producer", "filename", "target")
EXTERNAL = "EXTERNAL"


def contract_violations(graph):
    """
    Everything downstream assumes this shape. Returns a list of problems,
    empty when the graph conforms. Extra keys are allowed.
    """
    problems = []
    for key, kind in (("tasks", dict), ("edges", list), ("outputs", dict)):
        if not isinstance(graph.get(key), kind):
            problems.append(f"{key}: missing or not a {kind.__name__}")
    if problems:
        return problems

    tasks = graph["tasks"]
    for key, task in tasks.items():
        if not isinstance(task, dict):
            problems.append(f"task {key}: not an object")
            continue
        for field in TASK_FIELDS:
            if not isinstance(task.get(field), str):
                problems.append(f"task {key}: {field} missing or not a string")
        for field in OPTIONAL_TASK_FIELDS:
            if task.get(field) is not None and not isinstance(task[field], (str, int)):
                problems.append(f"task {key}: {field} is not a string or integer")
        if task.get("hash") != key:
            problems.append(f"task {key}: hash field does not match its key")
        status = task.get("status")
        if isinstance(status, str) and status != status.upper():
            problems.append(f"task {key}: status {status!r} is not upper-case")
        labels = task.get("labels")
        if labels is not None and not (
                isinstance(labels, dict)
                and all(isinstance(k, str) and isinstance(v, str)
                        for k, v in labels.items())):
            problems.append(f"task {key}: labels must map strings to strings")

    for i, edge in enumerate(graph["edges"]):
        if not isinstance(edge, dict):
            problems.append(f"edge {i}: not an object")
            continue
        for field in EDGE_FIELDS:
            if not isinstance(edge.get(field), str):
                problems.append(f"edge {i}: {field} missing or not a string")
        consumer, producer = edge.get("consumer"), edge.get("producer")
        if consumer not in tasks:
            problems.append(f"edge {i}: consumer {consumer!r} is not a task")
        if producer != EXTERNAL and producer not in tasks:
            problems.append(f"edge {i}: producer {producer!r} is not a task")
        if consumer == producer:
            problems.append(f"edge {i}: task {consumer!r} feeds itself")

    for key, names in graph["outputs"].items():
        if key not in tasks:
            problems.append(f"outputs: {key!r} is not a task")
        if not (isinstance(names, list)
                and all(isinstance(n, str) for n in names)):
            problems.append(f"outputs {key}: not a list of strings")
    return problems
