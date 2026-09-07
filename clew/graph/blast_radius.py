"""
Clew core — blast radius.

Given a graph and a set of starting nodes, find everything downstream.

THIS FILE KNOWS NOTHING ABOUT BIOLOGY.
No wet-lab or workflow-engine vocabulary of any kind. It sees opaque node ids
and typed edges. Everything domain-specific lives in domains/. (CLAUDE.md
greps this directory for the forbidden words; even naming them here would
trip the check, which is why this sentence is vague on purpose.)

If you ever need to write one of those words here, the design is wrong: the
knowledge belongs in a domain adapter that translates before calling in.

DIRECTION
---------
The extractor records edges BACKWARDS, because that is how a filesystem stores
them: a consumer holds a pointer to its producer.

    edge = {consumer: B, producer: A}     meaning "B was made from A"

A withdrawal travels FORWARDS: something at the source is revoked, and we need
everything built on top of it. So we invert the edges before traversing.
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
    One breadth-first pass from every start node: {node: parent}, with
    parent None for the start nodes themselves.

    Computed once per trigger and read once per affected task. The earlier
    per-target depth-first walk kept no visited set, so every scatter-gather
    stage multiplied the paths it had to enumerate; fifty subjects with
    three interval stages did not finish. This is linear in edges.

    Sorted, not incidental set order: which chain is recorded must not depend
    on the interpreter's hash seed, because re-running on the same inputs
    has to give byte-identical output.
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
    Core entry point.

    `subjects` maps an opaque subject id to the nodes where that subject's
    material enters the graph:

        {"subject-a": ["80/10c05c"], "subject-b": ["9b/e1fa4d"], ...}

    Core does not know or care what a subject is. The domain adapter decides.

    Returns, for each subject:
        affected   every node reachable from that subject
        exclusive  reachable from this subject and NO other
        shared     reachable from this subject and at least one other

    WHY THE SPLIT MATTERS
    ---------------------
    It maps straight onto remediation. A node built only from one subject can
    be removed outright. A node built from several cannot - the others still
    need it, so it has to be rebuilt without the withdrawn one.

    This function does not assign a contribution class. It reports structure.
    Classification needs the class on each edge, which does not exist yet.
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
