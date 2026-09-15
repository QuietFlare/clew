"""
Blast radius: from a set of start nodes, everything downstream.

Opaque node ids and typed edges only; vocabulary lives in providers. The
extractor records edges backwards, consumer to producer, because that is how
a filesystem stores them. A removal travels forwards, so the edges are
inverted before traversal.
"""

import json
from collections import defaultdict, deque
from pathlib import Path

EXTERNAL = "EXTERNAL"


def load_graph(path):
    """Read a graph produced by a domain extractor."""
    return json.loads(Path(path).read_text())


def forward_index(edges):
    """
    Invert the edge list: producer -> {consumers}.

    Self-edges and EXTERNAL producers are skipped. EXTERNAL is not a node -
    it is a marker meaning "came from outside the graph", so nothing can be
    reached *through* it.
    """
    forward = defaultdict(set)
    for edge in edges:
        producer = edge["producer"]
        if producer == EXTERNAL or producer is None:
            continue
        if producer == edge["consumer"]:
            continue
        forward[producer].add(edge["consumer"])
    return forward


def reachable(start_nodes, forward):
    """
    Every node reachable from start_nodes, following edges forward.

    Includes the start nodes themselves - they are affected too, not merely
    the things built from them.
    """
    seen = set(start_nodes)
    stack = list(start_nodes)
    while stack:
        node = stack.pop()
        for nxt in forward.get(node, ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def evidence_tree(start_nodes, forward):
    """
    One breadth-first pass from every start node: {node: parent}, None for
    the start nodes. Linear in edges; the earlier per-target depth-first
    walk did not finish on fifty subjects. Sorted, so the recorded chain
    does not depend on the hash seed.
    """
    parent = {node: None for node in start_nodes}
    queue = deque(sorted(start_nodes))
    while queue:
        node = queue.popleft()
        for nxt in sorted(forward.get(node, ())):
            if nxt not in parent:
                parent[nxt] = node
                queue.append(nxt)
    return parent


def path_from(tree, target):
    """The recorded chain from a start node to target; [] if target is a start node or unreached."""
    if tree.get(target) is None:
        return []
    path = [target]
    while tree[path[-1]] is not None:
        path.append(tree[path[-1]])
    path.reverse()
    return path


def paths_to(start_nodes, target, forward, limit=1):
    """
    A forward path from any start node to `target`, as a one-element list,
    or [] when there is none. Kept for callers that ask one target at a
    time; `evidence_tree` answers every target at once.
    """
    path = path_from(evidence_tree(start_nodes, forward), target)
    return [path] if path else []


def blast_radius(graph, subjects):
    """
    Core entry point. `subjects` maps an opaque id to the nodes where its
    material enters. Returns per subject: affected (all reachable),
    exclusive (reachable from this subject only) and shared. Exclusive nodes
    can be removed outright; shared ones must be rebuilt without the
    removed input. Structure only, no classes.
    """
    forward = forward_index(graph["edges"])

    per_subject = {
        subject: reachable(nodes, forward) for subject, nodes in subjects.items()
    }

    result = {}
    for subject, affected in per_subject.items():
        others = set()
        for other_subject, other_affected in per_subject.items():
            if other_subject != subject:
                others |= other_affected

        result[subject] = {
            "affected": affected,
            "exclusive": affected - others,
            "shared": affected & others,
        }
    return result
