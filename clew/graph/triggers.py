"""
Where something bad enters a graph.

A trigger names what went wrong and resolves to the nodes it entered at:
container:toolkit-2.1 and script:prep.py match a node field,
input:reference.dat an EXTERNAL edge's filename, subject:batch_017 a label.
Any other kind is read as a label key, so a graph carrying labels: {site:
north} answers site:north with no code added here. The vocabulary travels
inside the graph.
"""

from clew.graph.graph import container_matches, external_input_entry_nodes


def node_field(field):
    """Nodes whose *field* contains the value. Substring, as versions vary."""

    def resolve(graph, value):
        return sorted(
            h for h, t in graph["tasks"].items()
            if value in (t.get(field) or "")
        )

    return resolve


def container(graph, value):
    """Nodes whose image names the tool, at that version when both say one."""
    return sorted(container_matches(graph, value))


def external_filename(graph, value):
    """Nodes that consumed an EXTERNAL file with this basename or a companion."""
    return external_input_entry_nodes(graph, value)[f"input:{value}"]


def label_keys(graph):
    """Every label key any task or edge carries."""
    keys = set()
    for task in graph["tasks"].values():
        keys.update(task.get("labels") or {})
    for edge in graph["edges"]:
        keys.update(edge.get("labels") or {})
    return keys


def label(key):
    """
    Nodes carrying `labels[key] == value`, on the node or on any artifact
    the node consumed or produced.

    Labels sit on artifacts, since that is what a vocabulary describes,
    so a node counts when any artifact it touched carries the label.
    """

    def resolve(graph, value):
        hits = set()
        for node, task in graph["tasks"].items():
            if (task.get("labels") or {}).get(key) == value:
                hits.add(node)
        for edge in graph["edges"]:
            if (edge.get("labels") or {}).get(key) == value:
                hits.add(edge["consumer"])
                if edge["producer"] != "EXTERNAL":
                    hits.add(edge["producer"])
        return sorted(hits)

    return resolve


KINDS = {
    "container": container,
    "script": node_field("script"),
    "process": node_field("process"),
    "input": external_filename,
}


def resolve(graph, kind, value):
    """
    `{trigger_id: [entry nodes]}` for one trigger.

    An unknown kind is a label key rather than an error: that is how a
    domain adds a vocabulary word without touching Clew.
    """
    finder = KINDS.get(kind) or label(kind)
    return {f"{kind}:{value}": finder(graph, value)}


def parse(spec):
    """
    Split `kind:value`. The value may contain colons, the kind may not.
    """
    kind, sep, value = spec.partition(":")
    if not sep or not kind or not value:
        raise SystemExit(
            f"trigger {spec!r} should look like kind:value, "
            f"for example container:toolkit or subject:batch_017")
    return kind, value
