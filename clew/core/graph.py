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
