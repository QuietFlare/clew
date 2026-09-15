"""The published results tree: which of a task's outputs were copied there."""

from pathlib import Path

# Outputs every task writes for bookkeeping and no consumer reads, so no
# edge names them and a missing copy is not a missing result.
BOOKKEEPING = ("versions.yml",)

# Index key standing in for a directory's size, which is meaningless.
DIRECTORY = "directory"


def index_results(results_dir):
    """
    {(basename, size): [relative paths]}, plus {(basename, DIRECTORY): [...]}.

    Published files are copies, and engine checksums change on copy, so
    name plus exact size is the identity. Collisions list every candidate
    and are flagged ambiguous rather than picked.
    """
    index = {}
    root = Path(results_dir)
    for path in root.rglob("*"):
        rel = str(path.relative_to(root))
        if path.is_file():
            index.setdefault((path.name, path.stat().st_size), []).append(rel)
        elif path.is_dir():
            index.setdefault((path.name, DIRECTORY), []).append(rel)
    return index


def published_copies(graph, task_hash, results_index):
    """Published copies of one task's outputs, from the (name, size) index."""
    if not results_index:
        return []
    matches = []
    for detail in graph.get("output_details", {}).get(task_hash, []):
        name = Path(detail["file"]).name
        found = results_index.get((name, detail.get("size")), [])
        if found:
            matches.append({"output": detail["file"], "published": sorted(found),
                            "ambiguous": len(found) > 1})
            continue
        found = results_index.get((name, DIRECTORY), [])
        if found:
            # Name only; a directory's size says nothing. Over-report.
            matches.append({"output": detail["file"], "published": sorted(found),
                            "ambiguous": True, "match": "directory name"})
    return matches
