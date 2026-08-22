"""
Clew — what must happen downstream when something upstream turns out invalid.

Three triggers, one engine:

    # consent withdrawal (a source is removed)
    python3 blast.py --graph graph5.json --samplesheet donors.csv --donor donor_003

    # tool defect (every artifact a container touched is suspect)
    python3 blast.py --graph graph5.json --samplesheet donors.csv --container gatk4

    # reference / load-bearing input update
    python3 blast.py --graph graph5.json --samplesheet donors.csv --input genome.fasta

    # externally-asserted facts (publication) change the verdicts
    ... --donor donor_003 --assertions assertions.json

Wires the sarek domain adapter to the core traversal. All this file does is
translate between them and print the result; it holds no logic of its own.

TWO KINDS OF TRIGGER, ONE DELIBERATE DIFFERENCE
-----------------------------------------------
A withdrawal REMOVES A SOURCE. Ownership matters: an artifact that exists
only because of the withdrawn donor has nothing left to serve, so it can be
destroyed outright. That is what `exclusive` means.

A tool defect or reference update CASTS DOUBT. Nothing is owned by the
trigger — every affected artifact is still wanted, it just cannot be trusted.
So `exclusive` is always False for these: the worst verdict is QUARANTINE,
never DESTROY. Collapsing that distinction would delete data people need.

WHAT THIS DOES NOT KNOW
-----------------------
Classes are assigned from pipeline evidence alone: was the script and
container recorded, and does the artifact still exist on disk.

Publication is an assertion carried in from outside via --assertions, with
an actor and a date. MTA transfers and physical destruction are not modelled
yet. Anything unknown fails closed to IRREDUCIBLE.
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import blast_radius as core
from core import contribution
from domains import rnaseq, sarek, viralrecon

# Which adapter translates between this pipeline's vocabulary and core's.
# Adding a pipeline = adding a module in domains/ and one entry here.
DOMAINS = {"sarek": sarek, "viralrecon": viralrecon, "rnaseq": rnaseq}


def print_plan(domain, graph, subject, entry_nodes, affected, exclusive_set,
               published, results_index=None):
    """Classify every affected task and print the remediation plan."""
    forward = core.forward_index(graph["edges"])

    print(f"TRIGGER: {subject}")
    print(f"entry points: {len(entry_nodes)} tasks")
    print(f"AFFECTED: {len(affected)} of {len(graph['tasks'])} tasks\n")

    plan = []
    for task_hash in sorted(affected):
        facts = domain.classify(
            graph, task_hash, task_hash in exclusive_set, published=published
        )
        # The domain's storage check only sees the workdir. If the scratch
        # copy is gone but published copies are known to exist, the artifact
        # is NOT already gone — those copies are precisely what remediation
        # must reach. Scratch cleanup must never launder an obligation.
        if (facts["storage"] == contribution.DESTROYED
                and published_copies(graph, task_hash, results_index)):
            facts["storage"] = contribution.WRITABLE
            facts["reason"] += "; workdir removed but published copies exist"
        action = contribution.remediate(
            facts["contribution"],
            storage=facts["storage"],
            exclusive=facts["exclusive"],
            terminal=facts["terminal"],
        )
        plan.append((task_hash, facts, action))

    by_action = defaultdict(list)
    for task_hash, facts, action in plan:
        by_action[action].append((task_hash, facts))

    print("REMEDIATION PLAN")
    for action in sorted(by_action):
        rows = by_action[action]
        print(f"\n  {action}  ({len(rows)})  — {contribution.explain(action)}")
        for task_hash, facts in rows:
            scope = "exclusive" if facts["exclusive"] else "shared"
            print(f"    {task_hash}  {domain.describe(graph, task_hash):<26} "
                  f"{facts['contribution']:<12} {scope}")
            copies = published_copies(graph, task_hash, results_index)
            if copies:
                for c in copies:
                    flag = "  AMBIGUOUS, verify before acting" if c["ambiguous"] else ""
                    for path in c["published"]:
                        print(f"        published: {path}{flag}")
            if facts["terminal"]:
                print(f"        {facts['reason']}")
            elif task_hash not in entry_nodes:
                # Evidence: show one concrete chain reaching this task, so the
                # claim is checkable rather than merely asserted.
                for path in core.paths_to(entry_nodes, task_hash, forward, limit=1):
                    hops = " -> ".join(
                        f"{h}[{domain.describe(graph, h)}]" for h in path
                    )
                    print(f"        via {hops}")
    return plan


def index_results(results_dir):
    """
    Index a published-results tree by (basename, size).

    Why this key: the artifacts in results/ are COPIES made by publishDir.
    The lineage store's checksums are Nextflow's "standard" mode — hashed
    from path and mtime — so they change on copy and cannot identify one.
    Basename plus exact size can, almost always; where several published
    files collide on both, every candidate is listed and the match is
    flagged ambiguous rather than silently picking one. A deletion list
    must over-report candidates, never guess.
    """
    index = {}
    root = Path(results_dir)
    for path in root.rglob("*"):
        if path.is_file():
            key = (path.name, path.stat().st_size)
            index.setdefault(key, []).append(str(path.relative_to(root)))
    return index


def published_copies(graph, task_hash, results_index):
    """Published copies of one task's outputs, from the (name, size) index."""
    if not results_index:
        return []
    matches = []
    for detail in graph.get("output_details", {}).get(task_hash, []):
        key = (Path(detail["file"]).name, detail.get("size"))
        found = results_index.get(key, [])
        if found:
            matches.append({
                "output": detail["file"],
                "published": sorted(found),
                "ambiguous": len(found) > 1,
            })
    return matches


def plan_to_dict(domain, graph, subject, entry_nodes, plan, results_index=None):
    """
    The remediation plan as data, for scripts and CI rather than eyes.

    Deliberately clock-free: the same inputs must produce byte-identical
    output, because "re-run it and get the same answer" is the whole basis
    of Clew's evidence claim. Whoever stores this can wrap it with a
    timestamp; Clew itself only states what follows from the inputs.
    """
    forward = core.forward_index(graph["edges"])
    items = []
    for task_hash, facts, action in plan:
        task = graph["tasks"].get(task_hash, {})
        item = {
            "task": task_hash,
            "process": domain.describe(graph, task_hash),
            "name": task.get("name", ""),
            "action": action,
            "contribution": facts["contribution"],
            "storage": facts["storage"],
            "exclusive": facts["exclusive"],
            "terminal": facts["terminal"],
            "reason": facts["reason"],
        }
        # What a re-run script needs, for the artifacts it must rebuild.
        if action == "REGENERATE":
            item["container"] = task.get("container", "")
            item["script"] = task.get("script", "")
        # One checkable derivation chain per non-entry task: the evidence.
        if task_hash not in entry_nodes:
            paths = core.paths_to(entry_nodes, task_hash, forward, limit=1)
            if paths:
                item["evidence_path"] = paths[0]
        copies = published_copies(graph, task_hash, results_index)
        if copies:
            item["published_copies"] = copies
        items.append(item)

    counts = defaultdict(int)
    for _, _, action in plan:
        counts[action] += 1

    return {
        "clew_plan_version": 1,
        "trigger": subject,
        "entry_tasks": sorted(entry_nodes),
        "tasks_total": len(graph["tasks"]),
        "tasks_affected": len(plan),
        "actions": dict(sorted(counts.items())),
        "plan": items,
        "caveats": [
            "classes assigned from pipeline evidence only "
            "(script + container recorded, artifact present on disk)",
            "publication status is an external assertion, not verified by Clew",
            "MTA transfers and physical destruction are not modelled",
            "uninstrumented systems are unknown, never clean",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="Compute a blast radius and remediation plan.")
    parser.add_argument("--graph", required=True, help="graph JSON from an extractor")
    parser.add_argument("--samplesheet", required=True, help="nf-core samplesheet CSV")
    parser.add_argument("--pipeline", choices=sorted(DOMAINS), default="sarek",
                        help="which domain adapter reads the samplesheet and names")
    trigger = parser.add_mutually_exclusive_group()
    trigger.add_argument("--donor", help="withdraw a donor")
    trigger.add_argument("--container", help="flag every task run in a matching container")
    trigger.add_argument("--input", dest="input_file",
                         help="invalidate an external input file by basename")
    parser.add_argument("--mode", choices=("remove", "distrust"),
                        help="what kind of wrong: remove = the source must be "
                             "taken out (withdrawal; exclusive artifacts can be "
                             "destroyed); distrust = the data is suspect but "
                             "still wanted (contamination, defects; worst case "
                             "quarantine). Defaults: remove for --donor, "
                             "distrust for --container/--input.")
    parser.add_argument("--assertions", help="JSON file of externally-asserted facts")
    parser.add_argument("--files", action="store_true", help="list affected output files")
    parser.add_argument("--json", dest="json_out", metavar="PATH",
                        help="also write the plan as JSON ('-' for stdout)")
    parser.add_argument("--results", metavar="DIR",
                        help="the run's published results directory; plan items "
                             "then name the published copies of each artifact "
                             "(needs a graph from the lineage store adapter)")
    args = parser.parse_args()

    domain = DOMAINS[args.pipeline]
    graph = core.load_graph(args.graph)
    results_index = index_results(args.results) if args.results else None
    if args.results and not graph.get("output_details"):
        print("note: this graph has no output sizes (symlink extractor?); "
              "--results mapping needs a lineage-store graph\n")
    donors = domain.load_subjects(args.samplesheet)
    published = domain.load_assertions(args.assertions)

    # --- doubt triggers: single subject, nothing exclusive -------------------
    if args.container or args.input_file:
        if args.mode == "remove":
            # Removal needs an owner: "exclusive" only means something when
            # other subjects exist to compare against. A retracted upstream
            # dataset is a real remove-shaped input trigger, but computing
            # its exclusive set needs multi-root traversal we don't do yet.
            raise SystemExit(
                "--mode remove requires a subject trigger (--donor); "
                "container and input triggers cast doubt, they do not remove "
                "an owned source.")
        if args.container:
            subjects = domain.container_entry_nodes(graph, args.container)
        else:
            subjects = domain.external_input_entry_nodes(graph, args.input_file)

        subject, entry_nodes = next(iter(subjects.items()))
        if not entry_nodes:
            raise SystemExit(f"no tasks match {subject}")

        radius = core.blast_radius(graph, subjects)
        affected = radius[subject]["affected"]
        # Doubt, not removal: every artifact is still wanted. See header.
        plan = print_plan(domain, graph, subject, entry_nodes, affected,
                          exclusive_set=set(), published=published,
                          results_index=results_index)
        print_caveats(bool(published))
        # Last on stdout on purpose: with --json -, a consumer can split at
        # the final '{' and parse cleanly.
        write_json(args.json_out, domain, graph, subject, entry_nodes, plan,
                   results_index)
        return

    # --- withdrawal: exclusive/shared computed against the other donors ------
    entry = domain.subject_entry_nodes(graph, donors)
    radius = core.blast_radius(graph, entry)

    if not args.donor:
        print(f"{len(graph['tasks'])} tasks, {len(graph['edges'])} edges, "
              f"{len(donors)} donors\n")
        print(f"{'donor':<12} {'entry':>6} {'affected':>9} {'exclusive':>10} {'shared':>7}")
        for donor in sorted(radius):
            r = radius[donor]
            print(f"{donor:<12} {len(entry[donor]):>6} {len(r['affected']):>9} "
                  f"{len(r['exclusive']):>10} {len(r['shared']):>7}")
        return

    if args.donor not in radius:
        raise SystemExit(f"unknown donor {args.donor!r}; known: {', '.join(sorted(radius))}")

    result = radius[args.donor]
    mode = args.mode or "remove"
    if mode == "remove":
        # Withdrawal: the subject's exclusive artifacts have nothing left to
        # serve and can be destroyed.
        label = f"withdrawal of {args.donor}"
        exclusive = result["exclusive"]
    else:
        # Contamination / swap / QC failure: the subject's data is WRONG, not
        # withdrawn. Every artifact is still wanted once the cause is fixed,
        # so nothing is owned-and-destroyable; worst case is quarantine.
        label = f"distrust of {args.donor}"
        exclusive = set()
    plan = print_plan(domain, graph, label, entry[args.donor],
                      result["affected"], exclusive, published,
                      results_index=results_index)

    if args.files:
        exclusive_files = domain.outputs_for(graph, result["exclusive"])
        shared_files = domain.outputs_for(graph, result["shared"])
        print(f"\nFILES exclusive to {args.donor}: {len(exclusive_files)}")
        for path in exclusive_files[:20]:
            print(f"    {path}")
        if len(exclusive_files) > 20:
            print(f"    ... {len(exclusive_files) - 20} more")
        print(f"\nFILES shared with other donors: {len(shared_files)}")
        for path in shared_files[:20]:
            print(f"    {path}")
        if len(shared_files) > 20:
            print(f"    ... {len(shared_files) - 20} more")

    print_caveats(bool(published))
    # Last on stdout on purpose: with --json -, a consumer can split at the
    # final '{' and parse cleanly.
    write_json(args.json_out, domain, graph, label, entry[args.donor], plan,
               results_index)


def write_json(json_out, domain, graph, subject, entry_nodes, plan,
               results_index=None):
    if not json_out:
        return
    payload = json.dumps(
        plan_to_dict(domain, graph, subject, entry_nodes, plan, results_index),
        indent=2)
    if json_out == "-":
        print(payload)
    else:
        Path(json_out).write_text(payload)
        print(f"\nwrote {json_out}")


def print_caveats(have_assertions):
    print("\n" + "-" * 60)
    print("Classes assigned from pipeline evidence only (script + container "
          "recorded, artifact present on disk).")
    if have_assertions:
        print("Publication status from the assertions file; recorded as an "
              "external claim with actor and date, not verified by Clew.")
    else:
        print("No assertions file given: publication status unknown, all "
              "artifacts treated as unpublished.")
    print("MTA transfers and physical destruction are not modelled here.")


if __name__ == "__main__":
    main()
