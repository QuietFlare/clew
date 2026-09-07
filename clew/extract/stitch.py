"""Stitch per-run graphs into one cross-run graph, joined by content digest."""

import argparse
import json
from pathlib import Path

from clew.graph.graph import output_digests


def prefix_graph(label, graph):
    """Return a copy of the graph with every node id prefixed 'label:'."""
    def pre(h):
        return f"{label}:{h}"

    tasks = {pre(h): dict(t, hash=pre(h)) for h, t in graph["tasks"].items()}
    edges = []
    for e in graph["edges"]:
        producer = e["producer"]
        if producer not in (None, "EXTERNAL"):
            producer = pre(producer)
        edges.append(dict(e, consumer=pre(e["consumer"]), producer=producer))
    outputs = {pre(h): v for h, v in graph.get("outputs", {}).items()}
    details = {pre(h): v for h, v in graph.get("output_details", {}).items()}
    return {"tasks": tasks, "edges": edges, "outputs": outputs,
            "output_details": details}


def stitch(labelled_graphs):
    """
    Merge prefixed graphs and rewrite EXTERNAL edges whose digest another
    run produced. Returns (graph, bridges).
    """
    merged = {"tasks": {}, "edges": [], "outputs": {}, "output_details": {}}
    prefixed = {label: prefix_graph(label, g) for label, g in labelled_graphs.items()}
    for graph in prefixed.values():
        for key in ("tasks", "outputs", "output_details"):
            merged[key].update(graph[key])

    producers = {}
    for digest, produced in output_digests(merged).items():
        producers[digest] = produced[0][0]

    bridges = []
    for label, graph in prefixed.items():
        for edge in graph["edges"]:
            producer = producers.get(edge.get("digest")) if edge["producer"] == "EXTERNAL" else None
            if producer and not producer.startswith(f"{label}:"):
                edge = dict(edge, producer=producer)
                bridges.append({"consumer": edge["consumer"], "producer": producer,
                                "digest": edge["digest"], "path": edge.get("target", "")})
            merged["edges"].append(edge)
    return merged, bridges


def digest_coverage(labelled_graphs):
    """label -> (outputs with a digest, external inputs with a digest)."""
    coverage = {}
    for label, graph in labelled_graphs.items():
        outs = sum(1 for ds in graph.get("output_details", {}).values()
                   for d in ds if d.get("digest"))
        ins = sum(1 for e in graph["edges"]
                  if e["producer"] == "EXTERNAL" and e.get("digest"))
        coverage[label] = (outs, ins)
    return coverage


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Stitch per-run Clew graphs into one cross-run graph, by content digest.")
    parser.add_argument("--graph", action="append", required=True,
                        metavar="LABEL=PATH", help="a run's graph, labelled")
    parser.add_argument("--out", required=True, help="stitched graph JSON")
    args = parser.parse_args(argv)

    graphs = {}
    for pair in args.graph:
        label, _, path = pair.partition("=")
        if not label or not path:
            raise SystemExit(f"expected LABEL=PATH, got {pair!r}")
        graphs[label] = json.loads(Path(path).read_text())

    merged, bridges = stitch(graphs)

    print(f"graphs stitched     : {', '.join(graphs)}")
    print(f"tasks total         : {len(merged['tasks'])}")
    print(f"edges total         : {len(merged['edges'])}")
    print(f"cross-run bridges   : {len(bridges)}")
    for b in bridges:
        print(f"  {b['consumer']}  <-  {b['producer']}")
        print(f"      {b['digest']}")
    if not bridges:
        print("  (none. A bridge needs an EXTERNAL input in one graph whose "
              "digest equals an output digest in another.)")
        for label, (outs, ins) in digest_coverage(graphs).items():
            print(f"      {label}: {outs} outputs and {ins} external inputs carry a digest")

    Path(args.out).write_text(json.dumps(merged, indent=2))
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
