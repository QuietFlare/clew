"""Which task directories a run can give back, and the proof for each. Nothing is removed without --apply."""

import argparse
import fnmatch
import json
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from clew.extract.digest import sha256_file
from clew.graph import blast_radius as core
from clew.graph.graph import (EXTERNAL, STATUS_FAILED, STATUS_UNKNOWN,
                              published_digests, resolve_workdirs, task_status)
from clew.domains.nfcore import BOOKKEEPING
from clew.views import reclaim_report
from clew.views.reclaim_report import human

GONE = "GONE"
REDUNDANT = "REDUNDANT"
SUPERSEDED = "SUPERSEDED"
FAILED = "FAILED"
INTERMEDIATE = "INTERMEDIATE"
KEEP = "KEEP"

EXPLAIN = {
    GONE: "not on disk; nothing to reclaim",
    REDUNDANT: "every output has a published copy with the same digest",
    SUPERSEDED: "replaced by a later run in the resume chain",
    FAILED: "the attempt failed and nothing consumed it",
    INTERMEDIATE: "consumed only downstream; recipe and inputs still here",
    KEEP: "the graph cannot prove these bytes are redundant",
}

ORDER = (REDUNDANT, SUPERSEDED, FAILED, INTERMEDIATE, KEEP, GONE)


def dir_bytes(path):
    """
    Bytes freed by removing path: regular files with one link. Symlinks
    are staged inputs, and a file with another hard link survives the
    removal, so neither gives anything back.
    """
    total = 0
    for child in path.rglob("*"):
        if child.is_symlink() or not child.is_file():
            continue
        stat = child.lstat()
        if stat.st_nlink == 1:
            total += stat.st_size
    return total


def local_path(target):
    """A recorded input path as a local path: store URIs and '#checksum' stripped."""
    return Path(target.split("#", 1)[0].removeprefix("file://"))


def link_kind(work, published):
    """'symlink' when published points into work, 'hardlink' when same inode, else None."""
    try:
        if published.is_symlink():
            return "symlink" if published.resolve() == work.resolve() else None
        a, b = work.stat(), published.stat()
        return "hardlink" if (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino) else None
    except OSError:
        return None


def consumed_outputs(graph):
    """producer -> {filenames some task consumed}."""
    consumed = defaultdict(set)
    for edge in graph["edges"]:
        if edge["producer"] not in (EXTERNAL, None, edge["consumer"]):
            consumed[edge["producer"]].add(edge["filename"])
    return consumed


def inputs_of(graph):
    """consumer -> [(producer, target path)]."""
    inputs = defaultdict(list)
    for edge in graph["edges"]:
        if edge["producer"] != edge["consumer"]:
            inputs[edge["consumer"]].append((edge["producer"], edge.get("target", "")))
    return inputs


class Reclaimer:
    def __init__(self, graph, work_root, results=None, intermediates=False,
                 ignore=BOOKKEEPING, target=None):
        self.graph = graph
        self.work_root = Path(work_root)
        self.results = Path(results) if results else None
        self.published = published_digests(graph)
        self.intermediates = intermediates
        self.ignore = tuple(ignore)
        self.target = target
        self.forward = core.forward_index(graph["edges"])
        self.consumed = consumed_outputs(graph)
        self.inputs = inputs_of(graph)
        # Placed once for the whole graph: a directory several tasks share,
        # or a root nothing resolves under, unplaces every task it touches.
        self.local, self.warnings = resolve_workdirs(graph, self.work_root)
        self.present = {}
        self.recomputable = {}

    def on_disk(self, task_hash):
        if task_hash not in self.present:
            path = self.local.get(task_hash)
            self.present[task_hash] = bool(path and path.is_dir())
        return self.present[task_hash]

    def can_recompute(self, task_hash, seen=()):
        """
        Whether this task could be re-run from what is on disk now: recipe
        recorded, external inputs present, produced inputs present or
        themselves recomputable. Returns (yes, reason).
        """
        if task_hash in self.recomputable:
            return self.recomputable[task_hash]
        if task_hash in seen:
            return False, "input chain loops"
        task = self.graph["tasks"].get(task_hash)
        if task is None:
            return False, "producer not in graph"
        if not (task.get("script") and task.get("container")):
            answer = (False, "script or container not recorded")
        else:
            answer = (True, "recipe recorded")
            for producer, target in self.inputs.get(task_hash, []):
                if producer == EXTERNAL or producer is None:
                    if "://" in target and not target.startswith("file://"):
                        answer = (False, f"external input is remote, not verified: "
                                         f"{local_path(target).name}")
                        break
                    if not target or not local_path(target).exists():
                        answer = (False, "external input not found at its recorded "
                                         f"path: {local_path(target).name or '?'}")
                        break
                elif not self.on_disk(producer):
                    ok, why = self.can_recompute(producer, seen + (task_hash,))
                    if not ok:
                        answer = (False, f"input from {producer} gone and not recomputable ({why})")
                        break
        self.recomputable[task_hash] = answer
        return answer

    def published_copy(self, path, detail):
        """
        How the published tree holds this output: ('digest' | 'hardlink',
        [paths]) when it does, ('symlink', [paths]) when it only points into
        work, ('changed', [paths]) when a copy is no longer the size that
        was digested, ('none', []) when it does not, or ('unverified',
        [paths]) when --results was not given to check.

        Two outputs can share a digest (an empty file, a repeated header),
        so a copy with the output's own basename is preferred; only when
        none has it are all copies with that digest considered.
        """
        rels = self.published.get(detail.get("digest") or "", [])
        named = [rel for rel in rels if Path(rel).name == Path(detail["file"]).name]
        rels = named or rels
        if not rels:
            return "none", []
        if self.results is None:
            return "unverified", rels
        held, linked, changed = [], [], []
        for rel in rels:
            published = self.results / rel
            kind = link_kind(path / detail["file"], published)
            if kind == "symlink":
                linked.append(rel)
            elif kind == "hardlink":
                held.append((kind, rel))
            elif published.is_file():
                recorded = self.graph.get("published", {}).get(rel, {}).get("size")
                if recorded is not None and published.stat().st_size != recorded:
                    changed.append(rel)
                else:
                    held.append(("digest", rel))
        if held:
            return held[0][0], [rel for _, rel in held]
        for how, found in (("changed", changed), ("symlink", linked)):
            if found:
                return how, found
        return "none", []

    def recheck(self, item):
        """
        Why a planned removal must not go ahead now, or None. Re-hashes the
        published copies a REDUNDANT verdict rests on, since the plan only
        compared sizes and the tree may have moved on since it was digested.
        """
        if item["verdict"] != REDUNDANT or self.results is None:
            return None
        for copy in item.get("published_copies", []):
            for rel in copy["published"]:
                recorded = self.graph.get("published", {}).get(rel, {}).get("digest") or ""
                published = self.results / rel
                if not published.is_file():
                    return f"published copy gone since the plan: {rel}"
                if not recorded.startswith("sha256:"):
                    return f"published copy digest is not sha256, cannot re-hash: {rel}"
                if sha256_file(published) != recorded:
                    return f"published copy changed since it was digested: {rel}"
        return None

    def verdict(self, task_hash):
        task = self.graph["tasks"][task_hash]
        path = self.local.get(task_hash)
        if path is None:
            return KEEP, "task directory not placed under --work-root", []
        if not path.is_dir():
            return GONE, "directory not found under --work-root", []

        details = {d["file"]: d for d in self.graph.get("output_details", {}).get(task_hash, [])}
        outputs = [name for name in self.graph.get("outputs", {}).get(task_hash, [])
                   if not any(fnmatch.fnmatch(name, glob) for glob in self.ignore)]

        if task.get("superseded"):
            if self.forward.get(task_hash):
                return KEEP, "marked superseded but downstream tasks consumed it", []
            return SUPERSEDED, "marked superseded by the extractor", []
        status, word = task_status(task.get("status")), task.get("status") or ""
        if status == STATUS_FAILED:
            if self.forward.get(task_hash):
                return KEEP, f"status {word} but downstream tasks consumed it", []
            return FAILED, f"status {word}", []
        if status == STATUS_UNKNOWN:
            return KEEP, f"status {word} is not a finished state", []

        copies, settled, linked, changed, unverified, nodigest = [], set(), [], [], [], []
        for name in outputs:
            detail = details.get(name, {"file": name})
            if not detail.get("digest"):
                nodigest.append(name)
                continue
            how, rels = self.published_copy(path, detail)
            if how == "none":
                continue
            copies.append({"output": name, "published": sorted(rels), "verified": how})
            {"symlink": linked, "changed": changed,
             "unverified": unverified}.get(how, []).append(name)
            if how in ("digest", "hardlink"):
                settled.add(name)

        if nodigest:
            return KEEP, ("no content digest: " + ", ".join(sorted(nodigest))
                          + "; record them in the engine or run clew digest"), copies
        if linked:
            return KEEP, "published copy is a symlink into work: " + ", ".join(sorted(linked)), copies
        if changed:
            return KEEP, ("published copy is not the size that was digested: "
                          + ", ".join(sorted(changed))), copies
        if unverified:
            return KEEP, ("published copies recorded but --results not given to "
                          "check they still exist: " + ", ".join(sorted(unverified))), copies
        if outputs and all(name in settled for name in outputs):
            return REDUNDANT, f"{len(outputs)} outputs published, same digest", copies

        unpublished = sorted(name for name in outputs if name not in settled)
        if not self.intermediates:
            return KEEP, ("not published: " + ", ".join(unpublished)
                          + "; --intermediates not given"), copies
        loose = [name for name in unpublished if name not in self.consumed.get(task_hash, set())]
        if loose:
            return KEEP, "terminal output without a published copy: " + ", ".join(sorted(loose)), copies
        if not outputs:
            return KEEP, "no outputs recorded", copies
        ok, why = self.can_recompute(task_hash)
        return (INTERMEDIATE if ok else KEEP), why, copies

    def plan(self):
        """One item per task on this target, in task order."""
        items = []
        for task_hash in sorted(self.graph["tasks"]):
            task = self.graph["tasks"][task_hash]
            if self.target is not None and task.get("target", "") != self.target:
                continue
            verdict, reason, copies = self.verdict(task_hash)
            path = self.local.get(task_hash)
            item = {
                "task": task_hash,
                "process": task.get("process", ""),
                "name": task.get("name", ""),
                "target": task.get("target", ""),
                "dir": str(path) if path else "",
                "verdict": verdict,
                "reason": reason,
                "bytes": dir_bytes(path) if verdict != GONE and path and path.is_dir() else 0,
            }
            if copies:
                item["published_copies"] = copies
            items.append(item)
        return items


def summarise(items):
    counts, size = defaultdict(int), defaultdict(int)
    for item in items:
        counts[item["verdict"]] += 1
        size[item["verdict"]] += item["bytes"]
    return dict(counts), dict(size)


def print_plan(items, graph, work_root, results, intermediates, ignore=BOOKKEEPING, target=None):
    counts, size = summarise(items)
    deletable = [v for v in (REDUNDANT, SUPERSEDED, FAILED, INTERMEDIATE) if counts.get(v)]
    reclaimable = sum(size.get(v, 0) for v in deletable)
    if target is not None:
        print(f"TARGET: {target}")
    print(f"WORK ROOT: {work_root}")
    print(f"RESULTS: {results or 'not given'}")
    print(f"PUBLISHED DIGESTS: {len(graph.get('published', {}))} files recorded")
    print(f"IGNORED OUTPUTS: {', '.join(ignore) or 'none'}")
    print(f"TASKS: {len(items)}, on disk {len(items) - counts.get(GONE, 0)}")
    print(f"RECLAIMABLE: {human(reclaimable)} in {sum(counts.get(v, 0) for v in deletable)} directories\n")
    print("RECLAIM PLAN")
    for verdict in ORDER:
        rows = [i for i in items if i["verdict"] == verdict]
        if not rows:
            continue
        print(f"\n  {verdict}  ({len(rows)}, {human(size.get(verdict, 0))})  {EXPLAIN[verdict]}")
        if verdict == GONE:
            continue
        for item in rows:
            print(f"    {item['task']}  {item['process'].split(':')[-1]:<28} "
                  f"{human(item['bytes']):>10}  {item['reason']}")
    print("\n" + "-" * 60)
    for line in caveats(graph, results, intermediates):
        print(line)


def caveats(graph, results, intermediates):
    lines = [
        "Verdicts hold for the graph and disk as they are now. Re-run before applying.",
        "Sizes count regular files with one link only; staged inputs are symlinks "
        "to their producers, and a file hard-linked elsewhere survives the removal.",
    ]
    if not graph.get("published"):
        lines.append("No published digests in this graph, so nothing can be REDUNDANT. "
                     "Run clew digest --results over the published tree.")
    elif not results:
        lines.append("No --results given, so published copies could not be checked "
                     "to still exist. Nothing can be REDUNDANT.")
    if not intermediates:
        lines.append("Intermediates were not assessed. --intermediates proposes "
                     "directories whose outputs can be recomputed from inputs still on disk.")
    else:
        lines.append("INTERMEDIATE directories cost compute to get back: their "
                     "producers must be re-run.")
    return lines


def plan_to_dict(items, graph, work_root, results, intermediates, ignore=BOOKKEEPING,
                 target=None):
    counts, size = summarise(items)
    return {
        "clew_reclaim_version": 1,
        "work_root": str(work_root),
        "results": str(results) if results else None,
        "target": target,
        "intermediates": intermediates,
        "ignored_outputs": list(ignore),
        "tasks_total": len(items),
        "verdicts": dict(sorted(counts.items())),
        "bytes": dict(sorted(size.items())),
        "plan": items,
        "caveats": caveats(graph, results, intermediates),
    }


def apply(items, work_root, receipt_path, verdicts, recheck=None):
    """
    Remove each directory in `verdicts`, one receipt line before each
    removal. `recheck(item)` may return a reason to leave one alone; those
    come back as (item, reason) pairs. Returns (removed, refused).
    """
    root = Path(work_root).resolve()
    removed, refused = 0, []
    with open(receipt_path, "a") as receipt:
        for item in items:
            if item["verdict"] not in verdicts:
                continue
            target = Path(item["dir"]).resolve()
            if target == root or root not in target.parents:
                raise SystemExit(f"refusing to remove {target}: outside {root}")
            reason = recheck(item) if recheck else None
            if reason:
                refused.append((item, reason))
                continue
            entry = {**item, "removed_at": datetime.now(timezone.utc).isoformat()}
            receipt.write(json.dumps(entry) + "\n")
            receipt.flush()
            shutil.rmtree(target)
            removed += 1
    return removed, refused


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Which task directories a run can give back, and why.")
    parser.add_argument("--graph", help="graph JSON with content digests")
    parser.add_argument("--runs", metavar="DIR",
                        help="the engine's record instead of --graph: a .lineage store, "
                             "a horus-lineage root, or a directory of graphs")
    parser.add_argument("--run", help="which run under --runs; default: the latest")
    parser.add_argument("--work-root", required=True, metavar="DIR",
                        help="the run's work directory on this machine")
    parser.add_argument("--results", metavar="DIR",
                        help="the published results tree, to check that recorded "
                             "copies still exist and are not links into work")
    parser.add_argument("--intermediates", action="store_true",
                        help="also propose directories whose outputs are consumed "
                             "only downstream and can be recomputed")
    parser.add_argument("--target", metavar="ID",
                        help="only tasks that ran on this target, for engines that "
                             "run tasks on several machines; --work-root is then "
                             "that target's work directory")
    parser.add_argument("--ignore", action="append", metavar="GLOB",
                        help="output names that never block a verdict; repeatable. "
                             f"Default: {', '.join(BOOKKEEPING)}. Given values "
                             "replace the default.")
    parser.add_argument("--json", dest="json_out", metavar="PATH",
                        help="write the plan as JSON ('-' for stdout)")
    parser.add_argument("--html", dest="html_out", metavar="PATH",
                        help="write one self-contained HTML page ('-' for stdout)")
    parser.add_argument("--apply", action="store_true",
                        help="delete the proposed directories; needs --receipt")
    parser.add_argument("--receipt", metavar="PATH",
                        help="JSON lines file; one line is written per directory "
                             "before it is removed")
    args = parser.parse_args(argv)

    if args.apply and not args.receipt:
        raise SystemExit("--apply needs --receipt: nothing is deleted without a record")
    if not Path(args.work_root).is_dir():
        raise SystemExit(f"--work-root {args.work_root} is not a directory")
    if bool(args.graph) == bool(args.runs):
        raise SystemExit("give --graph or --runs, not both")

    if args.runs:
        from clew.extract.runs import Runs
        graph = Runs(args.runs).load(args.run)
    else:
        graph = core.load_graph(args.graph)
    ignore = tuple(args.ignore) if args.ignore else BOOKKEEPING
    reclaimer = Reclaimer(graph, args.work_root, args.results, args.intermediates,
                          ignore, args.target)
    items = reclaimer.plan()
    for line in reclaimer.warnings:
        print(f"clew: {line}", file=sys.stderr)
    if args.target is not None and not items:
        raise SystemExit(f"no task in this graph ran on target {args.target!r}")

    print_plan(items, graph, args.work_root, args.results, args.intermediates, ignore, args.target)
    if args.json_out or args.html_out:
        built = plan_to_dict(items, graph, args.work_root, args.results, args.intermediates,
                             ignore, args.target)
        if args.html_out:
            reclaim_report.write(built, args.html_out)
        if args.json_out == "-":
            print(json.dumps(built, indent=2))
        elif args.json_out:
            Path(args.json_out).write_text(json.dumps(built, indent=2))
            print(f"\nwrote {args.json_out}")

    if args.apply:
        verdicts = {REDUNDANT, SUPERSEDED, FAILED}
        if args.intermediates:
            verdicts.add(INTERMEDIATE)
        removed, refused = apply(items, args.work_root, args.receipt, verdicts,
                                 reclaimer.recheck)
        for item, reason in refused:
            print(f"kept {item['dir']}: {reason}")
        print(f"\nremoved {removed} directories; receipt in {args.receipt}")


if __name__ == "__main__":
    main()
