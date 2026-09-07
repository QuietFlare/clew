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
UNSETTLED = "UNSETTLED"
UNVERIFIED = "UNVERIFIED"
ADDED = "ADDED"
REMOVED = "REMOVED"

EXPLAIN = {
    DRIFTED: "outputs differ and every input matches; the divergence starts here",
    DOWNSTREAM: "outputs differ because an upstream task drifted",
    UNSETTLED: "outputs differ and an upstream task could not be verified; "
               "the divergence may start here or there",
    UNVERIFIED: "an output has no digest on one side, or the task could not "
                "be paired; not compared",
    REPRODUCED: "every output has the same digest in both runs",
    ADDED: "in the after run only",
    REMOVED: "in the before run only",
}

ORDER = (DRIFTED, UNSETTLED, DOWNSTREAM, UNVERIFIED, REPRODUCED, ADDED, REMOVED)

UNPAIRED = "several tasks share this name and their input digests do not tell them apart"


def input_digests(graph, task_hash):
    """
    Sorted digests of everything the task consumed, or None when any input
    lacks one. External edges carry their own; internal ones take the
    producer's output digest for the same file.
    """
    produced = {}
    for producer, details in graph.get("output_details", {}).items():
        for d in details:
            if d.get("digest"):
                produced[(producer, d["file"])] = d["digest"]
                produced.setdefault((producer, Path(d["file"]).name), d["digest"])
    digests = []
    for e in graph["edges"]:
        if e["consumer"] != task_hash:
            continue
        digest = e.get("digest") or produced.get((e["producer"], e["filename"])) \
            or produced.get((e["producer"], Path(e["filename"]).name))
        if not digest:
            return None
        digests.append(digest)
    return tuple(sorted(digests))


def live_tasks(graph):
    """Task hashes by name, leaving out versions a later run replaced."""
    by_name = defaultdict(list)
    for h, t in graph["tasks"].items():
        if not t.get("superseded"):
            by_name[t.get("name", "")].append(h)
    return by_name


def pair_tasks(before, after):
    """
    [(before hash | None, after hash | None, note)] in name order.

    One task per name on each side pairs by name. When a name repeats,
    as a per-interval step does, the two sides pair on input digests, and
    what that cannot settle is left unpaired with a note rather than
    matched by hash order, which pairs shards crosswise.
    """
    names_before, names_after = live_tasks(before), live_tasks(after)
    pairs = []
    for name in sorted(set(names_before) | set(names_after)):
        b, a = sorted(names_before.get(name, [])), sorted(names_after.get(name, []))
        if len(b) <= 1 and len(a) <= 1:
            pairs.append((b[0] if b else None, a[0] if a else None, None))
            continue
        keyed_b, keyed_a = defaultdict(list), defaultdict(list)
        for h in b:
            keyed_b[input_digests(before, h)].append(h)
        for h in a:
            keyed_a[input_digests(after, h)].append(h)
        left_b, left_a = [], []
        for key in sorted(set(keyed_b) | set(keyed_a), key=str):
            hb, ha = keyed_b.get(key, []), keyed_a.get(key, [])
            if key is not None and len(hb) == 1 and len(ha) == 1:
                pairs.append((hb[0], ha[0], None))
            else:
                left_b.extend(hb)
                left_a.extend(ha)
        if len(left_b) == 1 and len(left_a) == 1:
            # The one task on each side that nothing else claimed: paired
            # by elimination, so a changed input is found rather than hidden.
            pairs.append((left_b[0], left_a[0], None))
            continue
        pairs.extend((h, None, UNPAIRED) for h in left_b)
        pairs.extend((None, h, UNPAIRED) for h in left_a)
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
    paired_after = {a: b for b, a, _ in pairs if a and b}
    unpaired_after = {a: note for b, a, note in pairs if a and not b and note}

    def settle(a):
        if a in verdict:
            return verdict[a]
        b = paired_after.get(a)
        if b is None:
            verdict[a] = ((UNVERIFIED, unpaired_after[a]) if a in unpaired_after
                          else (ADDED, "no task with this name in the before run"))
            return verdict[a]
        sb, sa = signature(before, b, ignore), signature(after, a, ignore)
        if sb is None or sa is None:
            verdict[a] = (UNVERIFIED, "an output has no digest; run clew digest on both runs")
        elif sb == sa:
            upstream = [p for p in producers.get(a, ())
                        if settle(p)[0] in (DRIFTED, DOWNSTREAM, UNSETTLED)]
            verdict[a] = (REPRODUCED, "reproduced although " + ", ".join(sorted(upstream))
                          + " drifted" if upstream else "same digests")
        else:
            drifted = [p for p in producers.get(a, ())
                       if settle(p)[0] in (DRIFTED, DOWNSTREAM, UNSETTLED)]
            unverified = [p for p in producers.get(a, ()) if settle(p)[0] == UNVERIFIED]
            changed = sorted(f for f in sa if sb.get(f) != sa[f])
            if drifted:
                verdict[a] = (DOWNSTREAM, "follows " + ", ".join(sorted(drifted)))
            elif unverified:
                # The upstream difference cannot be seen, so this may be the
                # root or may follow one. Its own label keeps it in view.
                verdict[a] = (UNSETTLED, "upstream " + ", ".join(sorted(unverified))
                              + " not verified; differs: " + ", ".join(changed))
            else:
                verdict[a] = (DRIFTED, cause(before, b, after, a) + "; differs: " + ", ".join(changed))
        return verdict[a]

    for b, a, note in pairs:
        if a is not None:
            v, reason = settle(a)
            task = after["tasks"][a]
        else:
            v, reason = ((UNVERIFIED, note) if note
                         else (REMOVED, "no task with this name in the after run"))
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
        "Tasks are paired by name, and by input digests where a name repeats. "
        "A renamed process reads as removed and added; same-named tasks whose "
        "inputs cannot be told apart are unverified. Versions a later run "
        "superseded are left out.",
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
          f"{counts.get(UNSETTLED, 0)} unsettled, "
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
        same_chain = before["run"].get("session") and (
            before["run"]["session"] == after["run"].get("session"))
        if same_chain or before["run"]["id"] == after["run"]["id"]:
            # The store holds one graph per resume chain, so two runs of
            # one chain load the same graph and would compare as identical.
            raise SystemExit(
                f"--before {args.before!r} and --after {args.after!r} are "
                "the same graph: runs of one resume chain share a session, "
                "and the record holds one graph per session. Compare runs "
                "from different sessions, or two extracted graph files.")
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
