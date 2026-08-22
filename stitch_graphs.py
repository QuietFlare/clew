"""
Clew — stitch per-run graphs into one cross-run lineage graph.

WHY THIS EXISTS
---------------
The engine's lineage (native store, work/ symlinks, nf-prov) sees exactly
one launch. But real analyses are chains of launches: rnaseq publishes a
count matrix, differentialabundance consumes it, a report goes to people.
The chain's join point — one run's published file becoming the next run's
"external" input — is precisely what no engine-level record crosses, and
crossing it is the difference between per-run provenance and an answer to
"what did this donor's withdrawal reach".

HOW THE BRIDGE IS FOUND
-----------------------
An upstream task's outputs are matched to their published copies by
(basename, size) — see blast.index_results for why not checksums. A
downstream run's EXTERNAL input records the absolute path it was staged
from. Where that path IS one of the upstream run's published copies, the
EXTERNAL edge is rewritten to point at the task that produced it. The
original path stays in `target`, so every bridge is checkable.

Node ids are prefixed with per-run labels ("rna:0c/8143cf"): abbreviated
hashes from different runs can collide, and a silent collision would merge
two unrelated tasks — the false-negative factory. Core does not care; ids
are opaque to it.

USAGE
-----
    python3 stitch_graphs.py \
        --graph rna=graph_rna.json --results rna=/path/to/rnaseq/results \
        --graph da=graph_da.json \
        --out graph_chain.json

Every --graph gets a label. --results attaches a published-results tree to
the graph with the same label; bridges are found from any labelled results
tree into any other graph's EXTERNAL inputs.
"""

import argparse
import json
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from blast import index_results


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


def published_path_map(label, graph, results_dir):
    """
    {absolute published path: prefixed producing task} for one run.

    Built from the same (basename, size) join the plan output uses. An
    ambiguous match (two tasks' outputs identical in name and size) maps a
    path to whichever task claims it last — both would be affected in any
    traversal that reaches either, so ambiguity here widens the bridge
    rather than narrowing it.
    """
    index = index_results(results_dir)
    root = Path(results_dir).resolve()
    mapping = {}
    for task_hash, details in graph.get("output_details", {}).items():
        for detail in details:
            key = (Path(detail["file"]).name, detail.get("size"))
            for rel in index.get(key, []):
                mapping[str(root / rel)] = task_hash
    return mapping


def stitch(labelled_graphs, labelled_results):
    """
    Merge prefixed graphs and rewrite EXTERNAL edges that cross runs.

    Returns (graph, bridges); each bridge records consumer, producer and
    the path that joined them — the checkable evidence for the crossing.
    """
    merged = {"tasks": {}, "edges": [], "outputs": {}, "output_details": {}}
    prefixed = {}
    for label, graph in labelled_graphs.items():
        prefixed[label] = prefix_graph(label, graph)
        for key in ("tasks", "outputs", "output_details"):
            merged[key].update(prefixed[label][key])

    path_to_task = {}
    for label, results_dir in labelled_results.items():
        path_to_task.update(
            published_path_map(label, prefixed[label], results_dir))

    bridges = []
    for label, graph in prefixed.items():
        for edge in graph["edges"]:
            if edge["producer"] == "EXTERNAL":
                # The store records staged paths as URIs (file:///...);
                # the published map keys are plain absolute paths.
                staged = edge["target"].split("#", 1)[0]
                staged = staged.removeprefix("file://")
                producer = path_to_task.get(staged)
                if producer and not producer.startswith(f"{label}:"):
                    edge = dict(edge, producer=producer)
                    bridges.append({
                        "consumer": edge["consumer"],
                        "producer": producer,
                        "path": staged,
                    })
            merged["edges"].append(edge)

    return merged, bridges


def main():
    parser = argparse.ArgumentParser(
        description="Stitch per-run Clew graphs into one cross-run graph.")
    parser.add_argument("--graph", action="append", required=True,
                        metavar="LABEL=PATH", help="a run's graph, labelled")
    parser.add_argument("--results", action="append", default=[],
                        metavar="LABEL=DIR",
                        help="published results tree for the graph with that label")
    parser.add_argument("--out", required=True, help="stitched graph JSON")
    args = parser.parse_args()

    def parse_pairs(pairs):
        out = {}
        for pair in pairs:
            label, _, value = pair.partition("=")
            if not label or not value:
                raise SystemExit(f"expected LABEL=PATH, got {pair!r}")
            out[label] = value
        return out

    graphs = {label: json.loads(Path(p).read_text())
              for label, p in parse_pairs(args.graph).items()}
    results = parse_pairs(args.results)
    unknown = set(results) - set(graphs)
    if unknown:
        raise SystemExit(f"--results labels without a --graph: {', '.join(unknown)}")

    merged, bridges = stitch(graphs, results)

    print(f"graphs stitched     : {', '.join(graphs)}")
    print(f"tasks total         : {len(merged['tasks'])}")
    print(f"edges total         : {len(merged['edges'])}")
    print(f"cross-run bridges   : {len(bridges)}")
    for b in bridges:
        print(f"  {b['consumer']}  <-  {b['producer']}")
        print(f"      via {b['path']}")
    if not bridges:
        print("  (none found — check that --results dirs match the runs "
              "that actually published the chained inputs)")

    Path(args.out).write_text(json.dumps(merged, indent=2))
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
