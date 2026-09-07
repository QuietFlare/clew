"""Where two runs of the same workflow part ways, and why."""

import argparse
import fnmatch
import json
from collections import defaultdict
from pathlib import Path

from clew.graph import blast_radius as core
from clew.graph.graph import EXTERNAL
from clew.domains.nfcore import BOOKKEEPING
from clew.views import drift_report

REPRODUCED = "REPRODUCED"
DRIFTED = "DRIFTED"
DOWNSTREAM = "DOWNSTREAM"
UNVERIFIED = "UNVERIFIED"
ADDED = "ADDED"
REMOVED = "REMOVED"

EXPLAIN = {
    DRIFTED: "outputs differ and every input matches; the divergence starts here",
    DOWNSTREAM: "outputs differ because an upstream task drifted",
    UNVERIFIED: "an output has no digest on one side; not compared",
    REPRODUCED: "every output has the same digest in both runs",
    ADDED: "in the after run only",
    REMOVED: "in the before run only",
}

ORDER = (DRIFTED, DOWNSTREAM, UNVERIFIED, REPRODUCED, ADDED, REMOVED)


def pair_tasks(before, after):
    """[(before hash | None, after hash | None)] paired by task name, in name order."""
    names_before, names_after = defaultdict(list), defaultdict(list)
    for h, t in before["tasks"].items():
        names_before[t.get("name", "")].append(h)
    for h, t in after["tasks"].items():
        names_after[t.get("name", "")].append(h)
    pairs = []
    for name in sorted(set(names_before) | set(names_after)):
        b, a = sorted(names_before.get(name, [])), sorted(names_after.get(name, []))
        for i in range(max(len(b), len(a))):
            pairs.append((b[i] if i < len(b) else None, a[i] if i < len(a) else None))
    return pairs


def signature(graph, task_hash, ignore):
    """{output file: digest} for a task, or None when any output lacks one."""
    details = graph.get("output_details", {}).get(task_hash, [])
    known = {d["file"]: d.get("digest") for d in details}
    sig = {}
    for name in graph.get("outputs", {}).get(task_hash, []):
        if any(fnmatch.fnmatch(name, glob) for glob in ignore):
            continue
        if not known.get(name):
            return None
        sig[name] = known[name]
    return sig


def external_inputs(graph, task_hash):
    """{filename: digest} of a task's external inputs."""
    return {e["filename"]: e.get("digest") for e in graph["edges"]
            if e["consumer"] == task_hash and e["producer"] == EXTERNAL}


def cause(before, b, after, a):
    """Why a task's outputs differ although no upstream task did."""
    tb, ta = before["tasks"][b], after["tasks"][a]
    changed = sorted(name for name, digest in external_inputs(after, a).items()
                     if digest != external_inputs(before, b).get(name))
    if changed:
        return "input changed: " + ", ".join(changed)
    if (tb.get("container") or "") != (ta.get("container") or ""):
        return f"container changed: {tb.get('container') or '?'} -> {ta.get('container') or '?'}"
    if (tb.get("script") or "") != (ta.get("script") or ""):
        return "script changed"
    return "same inputs and recipe, different outputs"


def drift(before, after, ignore=BOOKKEEPING):
    """One item per task in either run, with a verdict and its reason."""
    producers = defaultdict(set)
    for e in after["edges"]:
        if e["producer"] not in (EXTERNAL, None, e["consumer"]):
            producers[e["consumer"]].add(e["producer"])

    verdict = {}
    items = []
    pairs = pair_tasks(before, after)
    paired_after = {a: b for b, a in pairs if a and b}

    def settle(a):
        if a in verdict:
            return verdict[a]
        b = paired_after.get(a)
        if b is None:
            verdict[a] = (ADDED, "no task with this name in the before run")
            return verdict[a]
        sb, sa = signature(before, b, ignore), signature(after, a, ignore)
        if sb is None or sa is None:
            verdict[a] = (UNVERIFIED, "an output has no digest; run clew digest on both runs")
        elif sb == sa:
            upstream = [p for p in producers.get(a, ()) if settle(p)[0] in (DRIFTED, DOWNSTREAM)]
            verdict[a] = (REPRODUCED, "reproduced although " + ", ".join(sorted(upstream))
                          + " drifted" if upstream else "same digests")
        else:
            upstream = [p for p in producers.get(a, ()) if settle(p)[0] in (DRIFTED, DOWNSTREAM, UNVERIFIED)]
            if upstream:
                verdict[a] = (DOWNSTREAM, "follows " + ", ".join(sorted(upstream)))
            else:
                changed = sorted(f for f in sa if sb.get(f) != sa[f])
                verdict[a] = (DRIFTED, cause(before, b, after, a) + "; differs: " + ", ".join(changed))
        return verdict[a]

    for b, a in pairs:
        if a is not None:
            v, reason = settle(a)
            task = after["tasks"][a]
        else:
            v, reason = REMOVED, "no task with this name in the after run"
            task = before["tasks"][b]
        items.append({"task": a or b, "before": b, "after": a,
                      "process": task.get("process", ""), "name": task.get("name", ""),
                      "target": task.get("target", ""), "verdict": v, "reason": reason})
    return items


def summarise(items):
    counts = defaultdict(int)
    for item in items:
        counts[item["verdict"]] += 1
    return dict(counts)


def caveats(ignore):
    return [
        "Tasks are paired by name. A renamed process reads as removed and added.",
        "Only outputs with a content digest on both sides are compared; "
        f"ignored outputs: {', '.join(ignore) or 'none'}.",
        "A root cause is read from the record: input digests, container, script. "
        "An input the engine did not record cannot be named.",
    ]


def print_plan(items, before_path, after_path, ignore):
    counts = summarise(items)
    roots = [i for i in items if i["verdict"] == DRIFTED]
    print(f"BEFORE: {before_path}")
    print(f"AFTER: {after_path}")
    print(f"TASKS: {len(items)}")
    print(f"DRIFT: {len(roots)} root{'s' if len(roots) != 1 else ''}, "
          f"{counts.get(DOWNSTREAM, 0)} downstream, {counts.get(REPRODUCED, 0)} reproduced\n")
    print("DRIFT PLAN")
    for v in ORDER:
        rows = [i for i in items if i["verdict"] == v]
        if not rows:
            continue
        print(f"\n  {v}  ({len(rows)})  {EXPLAIN[v]}")
        if v == REPRODUCED and len(rows) > 10:
            print(f"    {len(rows)} tasks, listed in --json")
            continue
        for i in rows:
            print(f"    {i['task']}  {i['process'].split(':')[-1]:<28} {i['reason']}")
    print("\n" + "-" * 60)
    for line in caveats(ignore):
        print(line)


def plan_to_dict(items, before_path, after_path, ignore):
    return {
        "clew_drift_version": 1,
        "before": str(before_path),
        "after": str(after_path),
        "ignored_outputs": list(ignore),
        "tasks_total": len(items),
        "verdicts": dict(sorted(summarise(items).items())),
        "plan": items,
        "caveats": caveats(ignore),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Where two runs of the same workflow part ways, and why.")
    parser.add_argument("--before", required=True,
                        help="graph JSON of the earlier run, or its run name under --runs")
    parser.add_argument("--after", required=True,
                        help="graph JSON of the later run, or its run name under --runs")
    parser.add_argument("--runs", metavar="DIR",
                        help="the engine's record: a .lineage store, a horus-lineage "
                             "root, or a directory of graphs; --before and --after "
                             "are then run names")
    parser.add_argument("--ignore", action="append", metavar="GLOB",
                        help="output names never compared; repeatable. "
                             f"Default: {', '.join(BOOKKEEPING)}.")
    parser.add_argument("--json", dest="json_out", metavar="PATH",
                        help="write the plan as JSON ('-' for stdout)")
    parser.add_argument("--html", dest="html_out", metavar="PATH",
                        help="write one self-contained HTML page ('-' for stdout)")
    args = parser.parse_args(argv)

    if args.runs:
        from clew.extract.runs import Runs
        store = Runs(args.runs)
        before, after = store.load(args.before), store.load(args.after)
    else:
        before, after = core.load_graph(args.before), core.load_graph(args.after)
    ignore = tuple(args.ignore) if args.ignore else BOOKKEEPING
    items = drift(before, after, ignore)
    print_plan(items, args.before, args.after, ignore)
    if args.json_out or args.html_out:
        built = plan_to_dict(items, args.before, args.after, ignore)
        if args.html_out:
            drift_report.write(built, args.html_out)
        if args.json_out == "-":
            print(json.dumps(built, indent=2))
        elif args.json_out:
            Path(args.json_out).write_text(json.dumps(built, indent=2))
            print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()
