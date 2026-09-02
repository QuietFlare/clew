"""
Clew — what must happen downstream when something upstream turns out invalid.

Three triggers, one engine:

    # consent withdrawal (a source is removed)
    clew impact --graph clew/data/graph5.json --samplesheet clew/data/donors.csv --subject donor_003

    # tool defect (every artifact a container touched is suspect)
    clew impact --graph clew/data/graph5.json --samplesheet clew/data/donors.csv --container gatk4

    # reference / load-bearing input update
    clew impact --graph clew/data/graph5.json --samplesheet clew/data/donors.csv --input genome.fasta

    # externally-asserted facts (publication) change the verdicts
    ... --subject donor_003 --assertions assertions.json

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


from clew.core import blast_radius as core
from clew.core import contribution
from clew.core import policy
from clew.core.policy import UNDETERMINED
from clew.domains import rnaseq, sarek, viralrecon
from clew.core import triggers
from clew.core.contribution import classify
from clew.core.graph import (
    container_entry_nodes,
    describe,
    external_input_entry_nodes,
    load_assertions,
    outputs_for,
)
from clew.domains.nfcore import index_results, published_copies

# Which adapter translates between this pipeline's vocabulary and core's.
# Adding a pipeline = adding a module in domains/ and one entry here.
DOMAINS = {"sarek": sarek, "viralrecon": viralrecon, "rnaseq": rnaseq}


def print_plan(domain, graph, subject, entry_nodes, affected, exclusive_set,
               published, results_index=None, active_policy=None,
               work_root=None):
    """Classify every affected task and print the remediation plan."""
    forward = core.forward_index(graph["edges"])
    active_policy = active_policy or policy.DEFAULT
    stamp = policy.identify(active_policy)

    print(f"TRIGGER: {subject}")
    print(f"entry points: {len(entry_nodes)} tasks")
    print(f"AFFECTED: {len(affected)} of {len(graph['tasks'])} tasks")
    # Named up front, not in a footer: every verdict below is a verdict UNDER
    # this table. A reader who cannot see which table was used cannot check
    # any of them.
    print(f"POLICY: {stamp['policy_version']}  {stamp['policy_hash'][:16]}\n")

    plan = []
    for task_hash in sorted(affected):
        facts = classify(
            graph, task_hash, task_hash in exclusive_set, published=published,
            work_root=work_root,
        )
        # The domain's storage check only sees the workdir. If the scratch
        # copy is gone but published copies are known to exist, the artifact
        # is NOT already gone — those copies are precisely what remediation
        # must reach. Scratch cleanup must never launder an obligation.
        # A published copy IS a verified sighting, whether the scratch copy
        # was checked and gone or never checked at all. Those copies are
        # precisely what remediation must reach, so finding one settles the
        # storage question on its own.
        if (facts["storage"] in (contribution.DESTROYED, None)
                and published_copies(graph, task_hash, results_index)):
            was = facts["storage"]
            facts["storage"] = contribution.WRITABLE
            facts["reason"] += ("; workdir removed but published copies exist"
                                if was == contribution.DESTROYED
                                else "; published copies found on disk")
        decision = policy.decide(
            facts["contribution"],
            storage=facts["storage"],
            exclusive=facts["exclusive"],
            terminal=facts["terminal"],
            policy=active_policy,
        )
        plan.append((task_hash, facts, decision))

    by_action = defaultdict(list)
    for task_hash, facts, decision in plan:
        # UNDETERMINED sorts last on purpose: it is not a verdict, and burying
        # it among the verdicts would let a reader skim past the part Clew is
        # telling them it could not answer.
        by_action[decision["action"] or UNDETERMINED].append(
            (task_hash, facts, decision))

    print("REMEDIATION PLAN")
    for action in sorted(by_action):
        rows = by_action[action]
        if action == UNDETERMINED:
            print(f"\n  {action}  ({len(rows)})  — no verdict; see below")
            print(f"    {rows[0][2]['because']}")
        else:
            print(f"\n  {action}  ({len(rows)})  — {contribution.explain(action)}")
            # One rule decided this whole group; print it once with its
            # rationale rather than repeating an id against every task.
            print(f"    rule {rows[0][2]['rule']}: {rows[0][2]['because']}")
        for task_hash, facts, _ in rows:
            scope = "exclusive" if facts["exclusive"] else "shared"
            print(f"    {task_hash}  {describe(graph, task_hash):<26} "
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
                        f"{h}[{describe(graph, h)}]" for h in path
                    )
                    print(f"        via {hops}")
    return plan


def count_undetermined(plan):
    return sum(1 for _, _, decision in plan if decision["action"] is None)


def plan_to_dict(domain, graph, subject, entry_nodes, plan, results_index=None,
                 active_policy=None):
    """
    The remediation plan as data, for scripts and CI rather than eyes.

    Deliberately clock-free: the same inputs must produce byte-identical
    output, because "re-run it and get the same answer" is the whole basis
    of Clew's evidence claim. Whoever stores this can wrap it with a
    timestamp; Clew itself only states what follows from the inputs.

    Carries the policy version AND its hash. The version alone is a label
    anyone can print; the hash is what makes two parties able to prove they
    were reading the same table.
    """
    forward = core.forward_index(graph["edges"])
    active_policy = active_policy or policy.DEFAULT
    items = []
    for task_hash, facts, decision in plan:
        task = graph["tasks"].get(task_hash, {})
        action = decision["action"]
        item = {
            "task": task_hash,
            "process": describe(graph, task_hash),
            "name": task.get("name", ""),
            # None when undetermined. A consumer treating a falsy action as
            # "nothing to do" is the exact failure this guards against, so
            # `possible` is present precisely when `action` is not.
            "action": action,
            "rule": decision["rule"],
            "because": decision["because"],
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
        if decision.get("possible"):
            item["possible"] = decision["possible"]
        copies = published_copies(graph, task_hash, results_index)
        if copies:
            item["published_copies"] = copies
        items.append(item)

    counts = defaultdict(int)
    for _, _, decision in plan:
        counts[decision["action"] or UNDETERMINED] += 1

    return {
        "clew_plan_version": 1,
        **policy.identify(active_policy),
        "trigger": subject,
        "entry_tasks": sorted(entry_nodes),
        "tasks_total": len(graph["tasks"]),
        "tasks_affected": len(plan),
        "actions": dict(sorted(counts.items())),
        "plan": items,
        "caveats": [
            "classes assigned from pipeline evidence only "
            "(script + container recorded, artifact present on disk)",
            "verdicts hold under the cited policy version only; replay an "
            "older plan under the policy it names, not under this one",
            "UNDETERMINED items are not clean; they are unanswered. Re-run "
            "with --work-root where the artifacts live to settle them",
            "publication status is an external assertion, not verified by Clew",
            "MTA transfers and physical destruction are not modelled",
            "uninstrumented systems are unknown, never clean",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compute a blast radius and remediation plan.")
    parser.add_argument("--graph", required=True, help="graph JSON from an extractor")
    parser.add_argument(
        "--samplesheet",
        help="nf-core samplesheet CSV. Needed only for --subject:\n"
             "is the one thing a domain has to resolve.")
    parser.add_argument("--pipeline", choices=sorted(DOMAINS), default="sarek",
                        help="which domain adapter reads the samplesheet and names")
    trigger = parser.add_mutually_exclusive_group()
    trigger.add_argument(
        "--subject", "--donor", dest="subject",
        help="withdraw a subject: a donor, a batch, a specimen. What "
             "one is belongs to the domain adapter, not here. --donor "
             "is the former name and still works.")
    trigger.add_argument("--container", help="flag every task run in a matching container")
    trigger.add_argument(
        "--trigger",
        help="kind:value, for example container:gatk4, script:prep.py, "
             "input:genome.fa or subject:batch_017. An unknown kind is "
             "read as a label key, so a graph that carries "
             "labels: {tissue: liver} answers tissue:liver with no "
             "adapter and no new flag.")
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
    parser.add_argument("--policy", metavar="VERSION|PATH",
                        help="a shipped policy version (v1, v2) or a policy "
                             "JSON file. Defaults to the current table. Pin "
                             "this to replay a historical plan under the "
                             "table that was in force when it was computed.")
    parser.add_argument("--files", action="store_true", help="list affected output files")
    parser.add_argument("--json", dest="json_out", metavar="PATH",
                        help="also write the plan as JSON ('-' for stdout)")
    parser.add_argument("--work-root", metavar="DIR",
                        help="the run's work directory, so Clew can check "
                             "whether each task's artifacts still exist. "
                             "Without it no storage claim is made, and any "
                             "verdict that depends on storage is reported "
                             "UNDETERMINED rather than guessed.")
    parser.add_argument("--results", metavar="DIR",
                        help="the run's published results directory; plan items "
                             "then name the published copies of each artifact "
                             "(needs a graph from the lineage store adapter)")
    args = parser.parse_args(argv)

    domain = DOMAINS[args.pipeline]
    try:
        active_policy = (policy.resolve_or_load(args.policy)
                         if args.policy else policy.DEFAULT)
    except policy.InvalidPolicy as bad:
        # Refuse to compute rather than compute under a table nobody vetted.
        raise SystemExit(f"policy rejected: {bad}")
    graph = core.load_graph(args.graph)
    results_index = index_results(args.results) if args.results else None
    if args.results and not graph.get("output_details"):
        print("note: this graph records no output sizes, so published "
              "copies cannot be mapped. Graphs extracted before sizes were "
              "recorded look like this; re-extract to fix.\n")
    # Only a subject trigger needs a domain to resolve one. Container and
    # input triggers are graph questions, so asking one should not require
    # naming a pipeline or producing its samplesheet.
    if args.subject:
        if not args.samplesheet:
            raise SystemExit(
                "--subject needs --samplesheet: resolving a subject to the "
                "nodes it enters at is the one thing a domain does.")
        donors = domain.load_subjects(args.samplesheet)
    else:
        donors = {}
    published = load_assertions(args.assertions)

    # --- doubt triggers: single subject, nothing exclusive -------------------
    if args.trigger:
        kind, value = triggers.parse(args.trigger)
        args.container = args.container or (
            value if kind == "container" else None)
    if args.trigger or args.container or args.input_file:
        if args.mode == "remove":
            # Removal needs an owner: "exclusive" only means something when
            # other subjects exist to compare against. A retracted upstream
            # dataset is a real remove-shaped input trigger, but computing
            # its exclusive set needs multi-root traversal we don't do yet.
            raise SystemExit(
                "--mode remove requires a subject trigger (--subject); "
                "container and input triggers cast doubt, they do not remove "
                "an owned source.")
        if args.trigger:
            kind, value = triggers.parse(args.trigger)
            subjects = triggers.resolve(graph, kind, value)
            if not next(iter(subjects.values())):
                raise SystemExit(
                    f"no task matches {args.trigger!r}. A trigger kind that "
                    f"is not built in is read as a label key, so this graph "
                    f"may simply carry no such label.")
        elif args.container:
            subjects = container_entry_nodes(graph, args.container)
        else:
            subjects = external_input_entry_nodes(graph, args.input_file)

        subject, entry_nodes = next(iter(subjects.items()))
        if not entry_nodes:
            raise SystemExit(f"no tasks match {subject}")

        radius = core.blast_radius(graph, subjects)
        affected = radius[subject]["affected"]
        # Doubt, not removal: every artifact is still wanted. See header.
        plan = print_plan(domain, graph, subject, entry_nodes, affected,
                          exclusive_set=set(), published=published,
                          results_index=results_index,
                          active_policy=active_policy,
                          work_root=args.work_root)
        print_caveats(bool(published), active_policy,
                      undetermined=count_undetermined(plan))
        # Last on stdout on purpose: with --json -, a consumer can split at
        # the final '{' and parse cleanly.
        write_json(args.json_out, domain, graph, subject, entry_nodes, plan,
                   results_index, active_policy)
        return

    # --- withdrawal: exclusive/shared computed against the other donors ------
    entry = domain.subject_entry_nodes(graph, donors)
    radius = core.blast_radius(graph, entry)

    if not args.subject:
        print(f"{len(graph['tasks'])} tasks, {len(graph['edges'])} edges, "
              f"{len(donors)} donors\n")
        print(f"{'donor':<12} {'entry':>6} {'affected':>9} {'exclusive':>10} {'shared':>7}")
        for donor in sorted(radius):
            r = radius[donor]
            print(f"{donor:<12} {len(entry[donor]):>6} {len(r['affected']):>9} "
                  f"{len(r['exclusive']):>10} {len(r['shared']):>7}")
        return

    if args.subject not in radius:
        raise SystemExit(f"unknown subject {args.subject!r}; known: {', '.join(sorted(radius))}")

    result = radius[args.subject]
    mode = args.mode or "remove"
    if mode == "remove":
        # Withdrawal: the subject's exclusive artifacts have nothing left to
        # serve and can be destroyed.
        label = f"withdrawal of {args.subject}"
        exclusive = result["exclusive"]
    else:
        # Contamination / swap / QC failure: the subject's data is WRONG, not
        # withdrawn. Every artifact is still wanted once the cause is fixed,
        # so nothing is owned-and-destroyable; worst case is quarantine.
        label = f"distrust of {args.subject}"
        exclusive = set()
    plan = print_plan(domain, graph, label, entry[args.subject],
                      result["affected"], exclusive, published,
                      results_index=results_index, active_policy=active_policy,
                      work_root=args.work_root)

    if args.files:
        exclusive_files = outputs_for(graph, result["exclusive"])
        shared_files = outputs_for(graph, result["shared"])
        print(f"\nFILES exclusive to {args.subject}: {len(exclusive_files)}")
        for path in exclusive_files[:20]:
            print(f"    {path}")
        if len(exclusive_files) > 20:
            print(f"    ... {len(exclusive_files) - 20} more")
        print(f"\nFILES shared with other donors: {len(shared_files)}")
        for path in shared_files[:20]:
            print(f"    {path}")
        if len(shared_files) > 20:
            print(f"    ... {len(shared_files) - 20} more")

    print_caveats(bool(published), active_policy,
                  undetermined=count_undetermined(plan))
    # Last on stdout on purpose: with --json -, a consumer can split at the
    # final '{' and parse cleanly.
    write_json(args.json_out, domain, graph, label, entry[args.subject], plan,
               results_index, active_policy)


def write_json(json_out, domain, graph, subject, entry_nodes, plan,
               results_index=None, active_policy=None):
    if not json_out:
        return
    payload = json.dumps(
        plan_to_dict(domain, graph, subject, entry_nodes, plan, results_index,
                     active_policy),
        indent=2)
    if json_out == "-":
        print(payload)
    else:
        Path(json_out).write_text(payload)
        print(f"\nwrote {json_out}")


def print_caveats(have_assertions, active_policy=None, undetermined=0):
    stamp = policy.identify(active_policy or policy.DEFAULT)
    print("\n" + "-" * 60)
    print(f"Computed under policy {stamp['policy_version']}, "
          f"sha256 {stamp['policy_hash']}.")
    print("Classes assigned from pipeline evidence only (script + container "
          "recorded, artifact present on disk).")
    if have_assertions:
        print("Publication status from the assertions file; recorded as an "
              "external claim with actor and date, not verified by Clew.")
    else:
        print("No assertions file given: publication status unknown, all "
              "artifacts treated as unpublished.")
    print("MTA transfers and physical destruction are not modelled here.")
    if undetermined:
        print(f"{undetermined} items are UNDETERMINED: not clean, unanswered. "
              "Storage was not\nchecked. Re-run with --work-root pointing at "
              "the work directory to settle them.")


if __name__ == "__main__":
    main()
