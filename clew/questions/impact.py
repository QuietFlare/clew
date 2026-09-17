"""
What must happen downstream when something upstream turns out invalid.

    clew impact --graph g.json --trigger patient:donor_003 --samplesheet sheet.csv
    clew impact --graph g.json --container gatk4
    clew impact --graph g.json --input genome.fasta
    clew impact --graph g.json --pipeline qbc    # whatever the adapter has pending

A removal takes a source away: an artifact that exists only because of it
can be destroyed, which is what scope `exclusive` means. A defect or
reference update is traced: every artifact is still wanted, so every scope
is `shared` and the worst verdict is QUARANTINE.

Classes come from pipeline evidence alone. Publication is an assertion
carried in through --assertions with an actor and a date. Anything unknown
fails closed to IRREDUCIBLE.
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

import sys


from clew.graph import blast_radius as core
from clew.graph import contribution
from clew.ledger import policy
from clew.ledger.policy import UNDETERMINED
from clew.contracts import Adapter, discover
from clew.views import report
from clew.contracts import trigger as triggers
from clew.contracts.trigger import REMOVE, TRACE, ENGINE_KINDS, Mode
from clew.graph.contribution import classify
from clew.graph.graph import (
    MATCH_NAME_ONLY,
    container_matches,
    describe,
    external_input_matches,
    load_assertions,
    outputs_for,
    resolve_workdirs,
)
from clew.graph.results import index_results, published_copies



def graph_notes(graph):
    """
    What the graph itself says it does not cover: the extractor's coverage
    notes, and subworkflow calls Cromwell left unexpanded. Printed and
    carried into the plan, so a verdict is read next to its limits.
    """
    notes = list(graph.get("coverage") or [])
    hidden = sorted(h for h, t in graph["tasks"].items()
                    if t.get("unexpanded_subworkflow"))
    if hidden:
        notes.append(
            f"{len(hidden)} subworkflow call(s) were not expanded, so the "
            f"tasks inside them are absent and anything they fed cannot be "
            f"reached: {', '.join(hidden)}. Fetch the metadata with "
            "expandSubWorkflows=true.")
    return notes


def container_notes(graph, needle):
    """Entry tasks matched on name alone, because their image names no version."""
    name_only = sorted(h for h, how in container_matches(graph, needle).items()
                       if how == MATCH_NAME_ONLY)
    if not name_only:
        return []
    return [f"{len(name_only)} entry task(s) matched {needle!r} on name only: "
            "their image carries a content hash rather than a version, so "
            "the version could not be checked and they are included: "
            + ", ".join(f"{h} ({(graph['tasks'][h].get('container') or '').rsplit('/', 1)[-1]})"
                        for h in name_only)]


def input_notes(graph, filename):
    """Companion files the input trigger also reached, such as an index."""
    extra = {name: nodes for name, nodes in external_input_matches(graph, filename).items()
             if name != filename}
    if not extra:
        return []
    return [f"input trigger {filename!r} also reached consumers of "
            + ", ".join(f"{name} ({len(nodes)} task(s))" for name, nodes in extra.items())
            + ", since a companion file is regenerated with the file it belongs to"]


def print_notes(heading, notes):
    if notes:
        print(f"{heading}")
        for note in notes:
            print(f"  - {note}")
        print()


def print_plan(adapter, graph, subject, entry_nodes, affected, exclusive_set,
               published, results_index=None, active_policy=None,
               work_root=None, kind_name=None, mode=TRACE):
    """Classify every affected task and print the remediation plan."""
    published_checked = (results_index is not None
                         and bool(graph.get("output_details")))
    forward = core.forward_index(graph["edges"])
    tree = core.evidence_tree(entry_nodes, forward)
    active_policy = active_policy or policy.DEFAULT
    stamp = policy.identify(active_policy)

    print(f"TRIGGER: {subject}")
    print(f"entry points: {len(entry_nodes)} tasks")
    print(f"AFFECTED: {len(affected)} of {len(graph['tasks'])} tasks")
    spared = [describe(graph, h) for h in sorted(graph["tasks"]) if h not in affected]
    if spared:
        print(f"UNTOUCHED: {', '.join(spared)}")
    # Named up front, not in a footer: every verdict below is a verdict UNDER
    # this table. A reader who cannot see which table was used cannot check
    # any of them.
    print(f"POLICY: {stamp['policy_version']}  {stamp['policy_hash'][:16]}\n")

    # Placed once for the whole graph, not per task: whether the root is
    # right at all is a question about every task together.
    resolved, warnings = resolve_workdirs(graph, work_root)
    for line in warnings:
        print(f"clew: {line}", file=sys.stderr)

    plan = []
    for task_hash in sorted(affected):
        facts = classify(
            graph, task_hash, task_hash in exclusive_set, published=published,
            work_root=work_root, resolved=resolved,
        )
        # The engine classifies from evidence alone. An adapter that knows the
        # step (a concatenation is separable, a trained model is not) may say
        # so, and the plan records who said it.
        asserted = adapter.contribution(graph, task_hash, kind_name) if adapter else None
        if asserted is not None:
            if asserted not in contribution.CLASSES:
                raise SystemExit(
                    f"adapter {adapter.name!r} returned {asserted!r} as the class of "
                    f"{task_hash}; classes are {', '.join(contribution.CLASSES)}")
            if asserted != facts["contribution"]:
                facts["evidence"] = (f"class {asserted} asserted by adapter {adapter.name} "
                                   f"(evidence alone said {facts['contribution']}); "
                                   + facts["evidence"])
            facts["contribution"] = asserted
            facts["class_asserted_by"] = adapter.name
        # The adapter's storage check only sees the workdir. If the scratch
        # copy is gone but published copies are known to exist, the artifact
        # is NOT already gone, those copies are precisely what remediation
        # must reach. Scratch cleanup must never launder an obligation.
        # A published copy IS a verified sighting, whether the scratch copy
        # was checked and gone or never checked at all. Those copies are
        # precisely what remediation must reach, so finding one settles the
        # storage question on its own.
        if (facts["storage"] in (contribution.DESTROYED, None)
                and published_copies(graph, task_hash, results_index)):
            was = facts["storage"]
            facts["storage"] = contribution.WRITABLE
            facts["evidence"] += ("; workdir removed but published copies exist"
                                if was == contribution.DESTROYED
                                else "; published copies found on disk")
        elif facts["storage"] == contribution.DESTROYED and not published_checked:
            # A missing workdir is only "gone" once the published tree has
            # been looked at too. Without that, DESTROYED would settle to
            # ALREADY_GONE for a sample whose BAM sits in results/. Leave
            # the dimension unverified and let the policy withhold.
            facts["storage"] = None
            facts["evidence"] += ("; workdir removed, published tree not "
                                "checked (no --results, or no output sizes "
                                "in the graph)")
        facts["mode"] = mode.value
        decision = policy.decide(
            facts["contribution"],
            storage=facts["storage"],
            scope=facts["scope"],
            released=facts["released"],
            mode=facts["mode"],
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

    width = max((len(h) for h, _, _ in plan), default=4)
    print("REMEDIATION PLAN")
    print(f"    {'task':<{width}}  {'process':<26} {'contribution':<12} scope")
    for action in sorted(by_action):
        rows = by_action[action]
        if action == UNDETERMINED:
            print(f"\n  {action}  ({len(rows)}), no verdict; see below")
            print(f"    {rows[0][2]['reason']}")
        else:
            print(f"\n  {action}  ({len(rows)}), {contribution.explain(action)}")
            # One rule decided this whole group; print it once with its
            # rationale rather than repeating an id against every task.
            print(f"    rule {rows[0][2]['rule']}: {rows[0][2]['reason']}")
        for task_hash, facts, _ in rows:
            print(f"    {task_hash:<{width}}  {describe(graph, task_hash):<26} "
                  f"{facts['contribution']:<12} {facts['scope']}")
            copies = published_copies(graph, task_hash, results_index)
            if copies:
                for c in copies:
                    flag = "  AMBIGUOUS, verify before acting" if c["ambiguous"] else ""
                    for path in c["published"]:
                        print(f"        published: {path}{flag}")
            if facts["released"]:
                print(f"        {facts['evidence']}")
            elif task_hash not in entry_nodes:
                # Evidence: show one concrete chain reaching this task, so the
                # claim is checkable rather than merely asserted.
                path = core.path_from(tree, task_hash)
                if path:
                    hops = " -> ".join(
                        f"{h}[{describe(graph, h)}]" for h in path
                    )
                    print(f"        via {hops}")
    return plan


def count_undetermined(plan):
    return sum(1 for _, _, decision in plan if decision["action"] is None)


def plan_cost(graph, plan):
    """
    Per verdict, the sum of every metric the affected tasks carry, and how
    many tasks carried none under that name. The names are the provider's.
    Recorded figures are a floor on a rerun, never an estimate of one.
    """
    by_action = {}
    for task_hash, _, decision in plan:
        action = decision["action"] or UNDETERMINED
        bucket = by_action.setdefault(action, {"tasks": 0, "metrics": {}, "missing": {}})
        bucket["tasks"] += 1
        metrics = graph["tasks"].get(task_hash, {}).get("metrics") or {}
        for name, value in metrics.items():
            bucket["metrics"][name] = round(bucket["metrics"].get(name, 0) + value, 6)
    for bucket in by_action.values():
        for name in bucket["metrics"]:
            bucket["missing"][name] = 0
    for task_hash, _, decision in plan:
        bucket = by_action[decision["action"] or UNDETERMINED]
        metrics = graph["tasks"].get(task_hash, {}).get("metrics") or {}
        for name in bucket["missing"]:
            if name not in metrics:
                bucket["missing"][name] += 1
    return {"by_action": by_action,
            "caveat": "sums of what the engine recorded for the original tasks; a rerun "
                      "costs at least the recorded figure, and tasks missing one add an "
                      "unknown amount"}


def print_cost(cost):
    rows = [(a, b) for a, b in cost["by_action"].items() if b["metrics"]]
    if not rows:
        return
    print("\nCOST OF THIS PLAN")
    for action, bucket in sorted(rows):
        parts = [f"{value:g} {name}" for name, value in sorted(bucket["metrics"].items())]
        gaps = [f"{n} without {name}" for name, n in sorted(bucket["missing"].items()) if n]
        line = f"  {action} {bucket['tasks']} task(s): " + ", ".join(parts)
        if gaps:
            line += "; " + ", ".join(gaps)
        print(line)
    print(f"  {cost['caveat']}")


def plan_to_dict(adapter, graph, subject, entry_nodes, plan, results_index=None,
                 active_policy=None, notes=()):
    """
    The plan as data for scripts and CI. Clock-free, so the same inputs give
    byte-identical output. Carries the policy version and its hash; the hash
    is what lets two parties prove they read the same table.
    """
    forward = core.forward_index(graph["edges"])
    tree = core.evidence_tree(entry_nodes, forward)
    active_policy = active_policy or policy.DEFAULT
    items = []
    for task_hash, facts, decision in plan:
        task = graph["tasks"].get(task_hash, {})
        action = decision["action"]
        item = {
            "task": task_hash,
            "process": describe(graph, task_hash),
            "name": task.get("name", ""),
            # Where it ran. The one cost signal that is a fact rather than
            # an estimate: a fan-out landing on a cluster is expensive
            # whatever a clock says. Empty for engines that run one machine.
            "target": task.get("target", ""),
            **({"metrics": task["metrics"]} if task.get("metrics") else {}),
            # None when undetermined. A consumer treating a falsy action as
            # "nothing to do" is the exact failure this guards against, so
            # `possible` is present precisely when `action` is not.
            "action": action,
            "rule": decision["rule"],
            "reason": decision["reason"],
            "contribution": facts["contribution"],
            "storage": facts["storage"],
            "scope": facts["scope"],
            "released": facts["released"],
            "mode": facts["mode"],
            "evidence": facts["evidence"],
            **({"class_asserted_by": facts["class_asserted_by"]}
               if "class_asserted_by" in facts else {}),
        }
        # What a re-run script needs, for the artifacts it must rebuild.
        if action == "REGENERATE":
            item["container"] = task.get("container", "")
            item["script"] = task.get("script", "")
        # One checkable derivation chain per non-entry task: the evidence.
        if task_hash not in entry_nodes:
            path = core.path_from(tree, task_hash)
            if path:
                item["evidence_path"] = path
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
        "clew_plan_version": 2,
        **policy.identify(active_policy),
        "trigger": subject,
        "entry_tasks": sorted(entry_nodes),
        "tasks_total": len(graph["tasks"]),
        "tasks_affected": len(plan),
        # Named, not just counted: a reader checks the answer against the
        # tasks it did not reach as much as against the ones it did.
        "untouched": [{"task": h, "process": describe(graph, h)}
                      for h in sorted(graph["tasks"])
                      if h not in {i["task"] for i in items}],
        "actions": dict(sorted(counts.items())),
        "cost": plan_cost(graph, plan),
        "plan": items,
        "caveats": [
            "classes assigned from pipeline evidence only "
            "(script + container recorded, artifact present on disk)",
            "verdicts hold under the cited policy version only; replay an "
            "older plan under the policy it names, not under this one",
            "UNDETERMINED items are not clean; they are unanswered. Re-run "
            "with --work-root and --results where the artifacts live to "
            "settle them",
            "release status is an external assertion, not verified by Clew",
            "MTA transfers and physical destruction are not modelled",
            "uninstrumented systems are unknown, never clean",
            # What the graph and the trigger said about their own limits,
            # so the evidence bundle carries them with the verdicts.
            *notes,
        ],
    }


def per_trigger_path(path, trigger):
    """plan.json -> plan.subject-SPC-0412.json"""
    if not path or path == "-":
        return path
    p = Path(path)
    tag = f"{trigger['kind']}-{trigger['value']}".replace("/", "_")
    return str(p.with_name(f"{p.stem}.{tag}{p.suffix}"))


def pending_triggers(adapter):
    """The adapter's pending triggers, checked before any is asked."""
    from clew.contracts.adapter import check
    pending = adapter.pending()
    if not isinstance(pending, list):
        raise SystemExit(f"{adapter.name}: pending() must return a list")
    for i, trigger in enumerate(pending):
        problems = check(trigger)
        if problems:
            raise SystemExit(f"{adapter.name}: pending trigger {i}: {'; '.join(problems)}")
    return pending


def answer_each(args, adapter, argv):
    """One plan per pending trigger. A bad trigger fails its answer, not the batch."""
    triggers_to_ask = pending_triggers(adapter)
    failed = []
    for trigger in triggers_to_ask:
        spec = f"{trigger['kind']}:{trigger['value']}"
        who = trigger.get("asserted_by")
        when = trigger.get("date")
        print("=" * 70)
        print(f"TRIGGER {spec}" + (f"  asserted by {who}" if who else "")
              + (f" on {when}" if when else ""))
        print("=" * 70)
        call = list(argv) + ["--trigger", spec]
        for flag, value in (("--json", args.json_out), ("--html", args.html_out)):
            if value:
                call = [a for a in call if a != flag and a != value]
                call += [flag, per_trigger_path(value, trigger)]
        try:
            main(call)
        except SystemExit as stop:
            failed.append((spec, str(stop)))
            print(f"FAILED {spec}: {stop}", file=sys.stderr)
        print()
    print(f"{len(triggers_to_ask)} triggers, {len(failed)} failed")
    return 1 if failed else 0


def parser_for(adapters, adapter):
    parser = argparse.ArgumentParser(
        description="Compute a blast radius and remediation plan.",
        conflict_handler="resolve")
    parser.add_argument("--graph", help="graph JSON from an extractor")
    parser.add_argument("--runs", metavar="DIR",
                        help="the engine's record instead of --graph, read through "
                             "whichever installed extractor recognises it; with --json, "
                             "the derived graph is written beside the plan")
    parser.add_argument("--run", help="which run under --runs; default: the latest")
    parser.add_argument("--pipeline", choices=sorted(adapters), default="sarek",
                        help="which adapter's trigger kinds apply")
    trigger = parser.add_mutually_exclusive_group()
    trigger.add_argument(
        "--trigger",
        help="kind:value, for example container:gatk4, input:genome.fa or "
             "patient:donor_003. Kinds are this pipeline's, then the engine's "
             "(container, script, process, input), then any label key the "
             "graph carries. A bare kind prints the reach of every value.")
    trigger.add_argument("--container", help="short for --trigger container:X")
    trigger.add_argument("--input", dest="input_file", help="short for --trigger input:X")
    parser.add_argument("--mode", choices=("remove", "trace"),
                        help="remove = the source is removed and what only it "
                             "fed can be destroyed; trace = follow what it touched, "
                             "everything stays, worst case quarantine. Defaults "
                             "to what the kind declares.")
    parser.add_argument("--assertions", help="JSON file of externally-asserted facts")
    parser.add_argument("--policy", metavar="VERSION|PATH",
                        help="a shipped policy version (v1) or a policy JSON "
                             "file. Defaults to the current table.")
    parser.add_argument("--files", action="store_true", help="list affected output files")
    parser.add_argument("--html", dest="html_out", metavar="PATH",
                        help="write one self-contained HTML page, or - for stdout")
    parser.add_argument("--json", dest="json_out", metavar="PATH",
                        help="also write the plan as JSON ('-' for stdout)")
    parser.add_argument("--work-root", metavar="DIR",
                        help="the run's work directory, so storage can be checked; "
                             "without it storage-dependent verdicts are UNDETERMINED")
    parser.add_argument("--results", metavar="DIR",
                        help="the run's published results directory, checked with "
                             "--work-root before anything reads ALREADY_GONE")
    for kind in adapter.triggers.values():
        kind.add_arguments(parser)
    return parser


def spec_of(args):
    if args.trigger:
        return args.trigger
    if args.container:
        return f"container:{args.container}"
    if args.input_file:
        return f"input:{args.input_file}"
    return None


def print_reach(graph, entry):
    """One line per value of the kind: how far each reaches."""
    radius = core.blast_radius(graph, entry)
    print(f"{len(graph['tasks'])} tasks, {len(graph['edges'])} edges, {len(entry)} values\n")
    print(f"{'value':<12} {'entry':>6} {'affected':>9} {'exclusive':>10} {'shared':>7}")
    for value in sorted(radius):
        r = radius[value]
        print(f"{value:<12} {len(entry[value]):>6} {len(r['affected']):>9} "
              f"{len(r['exclusive']):>10} {len(r['shared']):>7}")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    adapters = discover(Adapter)
    first = argparse.ArgumentParser(add_help=False)
    first.add_argument("--pipeline", choices=sorted(adapters), default="sarek")
    chosen, _ = first.parse_known_args(argv)
    adapter = adapters[chosen.pipeline]
    args = parser_for(adapters, adapter).parse_args(argv)

    spec = spec_of(args)
    if not spec:
        if adapter.pending():
            return answer_each(args, adapter, argv)
        raise SystemExit(
            "nothing to ask: give --trigger kind:value, or --container / --input. "
            f"This pipeline's kinds: {', '.join(adapter.triggers) or 'none'}; the "
            f"engine's: {', '.join(ENGINE_KINDS)}. A bare --trigger kind prints "
            "the reach of every value.")

    try:
        active_policy = (policy.resolve_or_load(args.policy)
                         if args.policy else policy.DEFAULT)
    except policy.InvalidPolicy as bad:
        # Refuse to compute rather than compute under a table nobody vetted.
        raise SystemExit(f"policy rejected: {bad}")
    if bool(args.graph) == bool(args.runs):
        raise SystemExit("give --graph or --runs, not both")
    if args.runs:
        from clew.extract.runs import Runs
        graph = Runs(args.runs).load(args.run)
        if args.json_out and args.json_out != "-":
            # The plan must be re-derivable from a file someone can hash and
            # hand over, not from a directory that may have changed since.
            derived = Path(args.json_out).with_suffix(".graph.json")
            derived.write_text(json.dumps(graph, indent=2))
            print(f"graph derived from {args.runs} ({graph['run']['name']}) written to {derived}\n")
    else:
        graph = core.load_graph(args.graph)
    results_index = index_results(args.results) if args.results else None
    if args.results and not graph.get("output_details"):
        print("note: this graph records no output sizes, so published "
              "copies cannot be mapped. Graphs extracted before sizes were "
              "recorded look like this; re-extract to fix.\n")
    notes = graph_notes(graph)
    print_notes("WHAT THIS GRAPH DOES NOT COVER", notes)

    kind_name, value = triggers.parse(spec)
    kind = triggers.lookup(adapter, kind_name, graph)
    if kind is None:
        raise SystemExit(
            f"unknown trigger kind {kind_name!r}: not one this pipeline declares "
            f"({', '.join(adapter.triggers) or 'none'}), not an engine kind "
            f"({', '.join(ENGINE_KINDS)}), and no task or edge in this graph "
            f"carries a {kind_name!r} label.")
    mode = Mode(args.mode) if args.mode else kind.mode
    if mode is REMOVE and kind.mode is not REMOVE:
        raise SystemExit(
            f"--mode remove needs a kind that owns something; {kind_name!r} can only "
            "be traced, it does not remove a source.")

    entry = kind.resolve(graph, value, args)
    if value is None:
        print_reach(graph, entry)
        return
    published = load_assertions(args.assertions)

    if kind.mode is TRACE:
        subject, entry_nodes = next(iter(entry.items()))
        if not entry_nodes:
            raise SystemExit(f"no task matches {subject}")
        trigger_notes = (container_notes(graph, value) if kind_name == "container"
                         else input_notes(graph, value) if kind_name == "input" else [])
        print_notes("TRIGGER NOTES", trigger_notes)
        notes = notes + trigger_notes
        radius = core.blast_radius(graph, entry)
        affected, exclusive, label = radius[subject]["affected"], set(), subject
    else:
        if not entry[value]:
            # Zero entry nodes is a failed attribution, not a clean result.
            tagged = sum(len(nodes) for nodes in entry.values())
            hint = (f" No task matched ANY {kind_name}: the ids and the run disagree."
                    if not tagged else "")
            raise SystemExit(f"{value!r} is a known {kind_name} but no task in the "
                             f"graph carries it. Not attributable, not clean.{hint}")
        radius = core.blast_radius(graph, entry)
        result = radius[value]
        entry_nodes = entry[value]
        if mode is REMOVE:
            # Withdrawal: what only this value fed has nothing left to serve.
            label, exclusive = f"removal of {value}", result["exclusive"]
        else:
            # Contamination, swap, QC failure: the data is wrong, not removed.
            label, exclusive = f"trace of {value}", set()
        affected = result["affected"]

    plan = print_plan(adapter, graph, label, entry_nodes, affected, exclusive,
                      published, results_index=results_index,
                      active_policy=active_policy, work_root=args.work_root,
                      kind_name=kind_name, mode=mode)

    if args.files and kind.mode is REMOVE:
        exclusive_files = outputs_for(graph, result["exclusive"])
        shared_files = outputs_for(graph, result["shared"])
        print(f"\nFILES exclusive to {value}: {len(exclusive_files)}")
        for path in exclusive_files[:20]:
            print(f"    {path}")
        if len(exclusive_files) > 20:
            print(f"    ... {len(exclusive_files) - 20} more")
        print(f"\nFILES shared with others: {len(shared_files)}")
        for path in shared_files[:20]:
            print(f"    {path}")
        if len(shared_files) > 20:
            print(f"    ... {len(shared_files) - 20} more")

    print_cost(plan_cost(graph, plan))
    print_caveats(bool(published), active_policy, undetermined=count_undetermined(plan),
                  asserted=sum(1 for _, facts, _ in plan if 'class_asserted_by' in facts))
    # Last on stdout on purpose: with --json -, a consumer can split at the final '{'.
    write_outputs(args.json_out, args.html_out, adapter, graph, label, entry_nodes, plan,
                  results_index, active_policy, notes)


def write_outputs(json_out, html_out, adapter, graph, subject, entry_nodes,
                  plan, results_index=None, active_policy=None, notes=()):
    """
    Render the plan in whichever formats were asked for, building it once.
    """
    if not json_out and not html_out:
        return
    built = plan_to_dict(adapter, graph, subject, entry_nodes, plan,
                         results_index, active_policy, notes)
    if html_out:
        report.write(built, html_out)
    if json_out:
        write_json(json_out, built)


def write_json(json_out, built):
    if not json_out:
        return
    payload = json.dumps(built, indent=2)
    if json_out == "-":
        print(payload)
    else:
        Path(json_out).write_text(payload)
        print(f"\nwrote {json_out}")


def print_caveats(have_assertions, active_policy=None, undetermined=0, asserted=0):
    stamp = policy.identify(active_policy or policy.DEFAULT)
    print("\n" + "-" * 60)
    print(f"Computed under policy {stamp['policy_version']}, "
          f"sha256 {stamp['policy_hash']}.")
    if asserted:
        print(f"{asserted} class(es) asserted by the adapter, recorded per item; the rest "
              "from pipeline evidence (script + container recorded, artifact on disk).")
    else:
        print("Classes assigned from pipeline evidence only (script + container "
              "recorded, artifact present on disk).")
    if have_assertions:
        print("Publication status from the assertions file; recorded as an "
              "external claim with actor and date, not verified by Clew.")
    else:
        print("No assertions file given: release status unknown, all "
              "artifacts treated as unpublished.")
    print("MTA transfers and physical destruction are not modelled here.")
    if undetermined:
        print(f"{undetermined} items are UNDETERMINED: not clean, unanswered. "
              "Storage was not\nfully checked. Re-run with --work-root and "
              "--results pointing at the run's directories to settle them.")


if __name__ == "__main__":
    main()
