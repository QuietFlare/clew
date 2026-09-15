"""Which task directories a run can give back, and the proof for each. Nothing is removed without --apply."""

import argparse
import fnmatch
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from clew.graph import blast_radius as core
from clew.graph.graph import (EXTERNAL, STATUS_FAILED, STATUS_UNKNOWN,
                              published_digests, resolve_workpaths, task_status)
from clew.graph.results import BOOKKEEPING
from clew.graph.storage import LocalTree, open_tree
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
UNPUBLISHED = "not published"


def local_path(target):
    """A recorded input path as a local path: store URIs and '#checksum' stripped."""
    return Path(target.split("#", 1)[0].removeprefix("file://"))


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


def said(cause, files=(), hint=""):
    """The reason as one sentence, with the files it names spliced in after the cause."""
    text = cause + (": " + ", ".join(sorted(files)) if files else "") + ("; " + hint if hint else "")
    return text


class Reclaimer:
    def __init__(self, graph, work_root, results=None, intermediates=False,
                 ignore=BOOKKEEPING, target=None):
        self.graph = graph
        self.work = open_tree(work_root)
        self.results = open_tree(results)
        self.published = published_digests(graph)
        self.intermediates = intermediates
        self.ignore = tuple(ignore)
        self.target = target
        self.forward = core.forward_index(graph["edges"])
        self.consumed = consumed_outputs(graph)
        self.inputs = inputs_of(graph)
        # Placed once for the whole graph: a directory several tasks share,
        # or a root nothing resolves under, unplaces every task it touches.
        self.local, self.warnings = resolve_workpaths(graph, self.work)
        self.present = {}
        self.recomputable = {}

    def on_disk(self, task_hash):
        if task_hash not in self.present:
            rel = self.local.get(task_hash)
            self.present[task_hash] = bool(rel and self.work.is_dir(rel))
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

    def published_copy(self, rel, detail):
        """
        How the published tree holds this output: ('digest' | 'hardlink',
        [paths]), ('symlink', [paths]) when it only points into work,
        ('changed', [paths]) when a copy no longer has the digested size,
        ('none', []), or ('unverified', [paths]) without --results. A copy
        with the output's own basename is preferred, since two outputs can
        share a digest.
        """
        rels = self.published.get(detail.get("digest") or "", [])
        named = [p for p in rels if Path(p).name == Path(detail["file"]).name]
        rels = named or rels
        if not rels:
            return "none", []
        if self.results is None:
            return "unverified", rels
        held, linked, changed = [], [], []
        for published in rels:
            kind = self.work.link_kind(f"{rel}/{detail['file']}", self.results, published)
            if kind == "symlink":
                linked.append(published)
            elif kind == "hardlink":
                held.append((kind, published))
            elif self.results.is_file(published):
                recorded = self.graph.get("published", {}).get(published, {}).get("size")
                if recorded is not None and self.results.size(published) != recorded:
                    changed.append(published)
                else:
                    held.append(("digest", published))
        if held:
            return held[0][0], [p for _, p in held]
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
            for published in copy["published"]:
                recorded = self.graph.get("published", {}).get(published, {}).get("digest") or ""
                if not self.results.is_file(published):
                    return f"published copy gone since the plan: {published}"
                if not recorded.startswith("sha256:"):
                    return f"published copy digest is not sha256, cannot re-hash: {published}"
                if self.results.sha256(published) != recorded:
                    return f"published copy changed since it was digested: {published}"
        return None

    def verdict(self, task_hash):
        """(verdict, cause, files the cause names, hint, published copies)."""
        task = self.graph["tasks"][task_hash]
        rel = self.local.get(task_hash)
        if rel is None:
            return KEEP, "task directory not placed under --work-root", [], "", []
        if not self.work.is_dir(rel):
            return GONE, "directory not found under --work-root", [], "", []

        details = {d["file"]: d for d in self.graph.get("output_details", {}).get(task_hash, [])}
        outputs = [name for name in self.graph.get("outputs", {}).get(task_hash, [])
                   if not any(fnmatch.fnmatch(name, glob) for glob in self.ignore)]

        if task.get("superseded"):
            if self.forward.get(task_hash):
                return KEEP, "marked superseded but downstream tasks consumed it", [], "", []
            return SUPERSEDED, "marked superseded by the extractor", [], "", []
        status, word = task_status(task.get("status")), task.get("status") or ""
        if status == STATUS_FAILED:
            if self.forward.get(task_hash):
                return KEEP, f"status {word} but downstream tasks consumed it", [], "", []
            return FAILED, f"status {word}", [], "", []
        if status == STATUS_UNKNOWN:
            return KEEP, f"status {word} is not a finished state", [], "", []

        copies, settled, linked, changed, unverified, nodigest = [], set(), [], [], [], []
        for name in outputs:
            detail = details.get(name, {"file": name})
            if not detail.get("digest"):
                nodigest.append(name)
                continue
            how, rels = self.published_copy(rel, detail)
            if how == "none":
                continue
            copies.append({"output": name, "published": sorted(rels), "verified": how})
            {"symlink": linked, "changed": changed,
             "unverified": unverified}.get(how, []).append(name)
            if how in ("digest", "hardlink"):
                settled.add(name)

        if nodigest:
            return KEEP, "no content digest", nodigest, "record them in the engine or run clew digest", copies
        if linked:
            return KEEP, "published copy is a symlink into work", linked, "", copies
        if changed:
            return KEEP, "published copy is not the size that was digested", changed, "", copies
        if unverified:
            return KEEP, ("published copies recorded but --results not given to "
                          "check they still exist"), unverified, "", copies
        if outputs and all(name in settled for name in outputs):
            return REDUNDANT, "every output published, same digest", [], "", copies

        unpublished = sorted(name for name in outputs if name not in settled)
        if not self.intermediates:
            return KEEP, UNPUBLISHED, unpublished, "--intermediates not given", copies
        loose = [name for name in unpublished if name not in self.consumed.get(task_hash, set())]
        if loose:
            return KEEP, "terminal output without a published copy", loose, "", copies
        if not outputs:
            return KEEP, "no outputs recorded", [], "", copies
        ok, why = self.can_recompute(task_hash)
        return (INTERMEDIATE if ok else KEEP), why, [], "", copies

    def plan(self):
        """One item per task on this target, in task order."""
        items = []
        for task_hash in sorted(self.graph["tasks"]):
            task = self.graph["tasks"][task_hash]
            if self.target is not None and task.get("target", "") != self.target:
                continue
            verdict, cause, files, hint, copies = self.verdict(task_hash)
            rel = self.local.get(task_hash)
            item = {
                "task": task_hash,
                "process": task.get("process", ""),
                "name": task.get("name", ""),
                "target": task.get("target", ""),
                "dir": self.work.describe(rel) if rel else "",
                "verdict": verdict,
                "reason": said(cause, files, hint),
                "cause": said(cause, (), hint),
                "bytes": self.work.dir_bytes(rel) if verdict != GONE and rel and self.work.is_dir(rel) else 0,
            }
            if files:
                item["files"] = sorted(files)
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


def short_name(process):
    return process.split(":")[-1]


def by_process(rows):
    """['10 SAMTOOLS_STATS (1.9 MB)', '5 FASTQC (9.0 MB)'], most first."""
    counts, sizes = Counter(), Counter()
    for item in rows:
        counts[short_name(item["process"])] += 1
        sizes[short_name(item["process"])] += item["bytes"]
    return [f"{n} {name} ({human(sizes[name])})"
            for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def wrapped(entries, indent, width=100):
    """Comma-separated entries, wrapped between entries only."""
    lines, line = [], indent
    for entry in entries:
        piece = entry if line == indent else ", " + entry
        if len(line) + len(piece) + 1 > width and line != indent:
            lines.append(line + ",")
            line, piece = indent, entry
        line += piece
    return "\n".join(lines + [line])


def print_plan(items, graph, work_root, results, intermediates, ignore=BOOKKEEPING,
               target=None, verbose=False, source=None):
    """
    The plan as a reader wants it: what was checked, what came out, and the
    reasons grouped, since a run has three or four reasons and eighty
    directories. --verbose lists every directory under its reason.
    """
    counts, size = summarise(items)
    deletable = [v for v in (REDUNDANT, SUPERSEDED, FAILED, INTERMEDIATE) if counts.get(v)]
    reclaimable = sum(size.get(v, 0) for v in deletable)
    on_disk = len(items) - counts.get(GONE, 0)
    where = f"{on_disk} task directories under {work_root}"
    if target is not None:
        where += f", target {target}"
    print(f"clew reclaim: {where}, {human(sum(size.values()))}"
          + (f"; lineage extracted from {source}" if source else ""))
    digests = len(graph.get("published", {}))
    if results:
        print(f"published copies checked against {results} ({digests} digests recorded)"
              + (f"; {', '.join(ignore)} ignored" if ignore else ""))
    else:
        print(f"published copies not checked: no --results given ({digests} digests recorded)")
    print(f"reclaimable: {human(reclaimable)} in "
          f"{sum(counts.get(v, 0) for v in deletable)} directories")

    for verdict in ORDER:
        rows = [i for i in items if i["verdict"] == verdict]
        if not rows:
            continue
        print(f"\n{verdict}  {len(rows)}  {human(size.get(verdict, 0))}   {EXPLAIN[verdict]}")
        if verdict == GONE:
            continue
        causes = Counter(i["cause"] for i in rows)
        grouped = len(causes) > 1 or verdict == KEEP
        for cause, n in sorted(causes.items(), key=lambda kv: (-kv[1], kv[0])):
            group = [i for i in rows if i["cause"] == cause]
            indent = "  "
            if grouped:
                print(f"  {n:>3}  {cause}")
                indent = "       "
            print(wrapped(by_process(group), indent))
            if verbose:
                for item in group:
                    files = ", ".join(item.get("files", []))
                    print(f"{indent}{item['task']}  {short_name(item['process']):<28} "
                          f"{human(item['bytes']):>10}  {files}".rstrip())

    print()
    for line in caveats(graph, results, intermediates, items, work_root):
        print(line)


def caveats(graph, results, intermediates, items=(), work_root=""):
    """Only the lines that apply to this plan."""
    lines = ["Verdicts rest on the lineage as extracted from the engine's record and on "
             "the disk as it is now. Re-run before applying."]
    if not str(work_root).startswith("s3://"):
        lines.append("Sizes count files with one link; a file hard-linked elsewhere survives the removal.")
    if not graph.get("published"):
        lines.append("No published digests in this graph, so nothing can be REDUNDANT: "
                     "run clew digest --results over the published tree.")
    elif not results:
        lines.append("Without --results nothing can be REDUNDANT.")
    if not intermediates and any(i["verdict"] == KEEP and i["cause"].startswith(UNPUBLISHED)
                                 for i in items):
        lines.append("--intermediates also assesses directories whose outputs can be "
                     "recomputed from inputs still on disk.")
    if any(i["verdict"] == INTERMEDIATE for i in items):
        lines.append("INTERMEDIATE directories cost compute to get back: their "
                     "producers must be re-run.")
    return lines


def plan_to_dict(items, graph, work_root, results, intermediates, ignore=BOOKKEEPING,
                 target=None, source=None):
    counts, size = summarise(items)
    return {
        "clew_reclaim_version": 1,
        "lineage": str(source) if source else None,
        "run": (graph.get("run") or {}).get("name"),
        "work_root": str(work_root),
        "results": str(results) if results else None,
        "target": target,
        "intermediates": intermediates,
        "ignored_outputs": list(ignore),
        "tasks_total": len(items),
        "verdicts": dict(sorted(counts.items())),
        "bytes": dict(sorted(size.items())),
        "plan": items,
        "caveats": caveats(graph, results, intermediates, items, work_root),
    }


def apply(items, work_root, receipt_path, verdicts, recheck=None):
    """
    Remove each directory in `verdicts`, one receipt line before each
    removal. `recheck(item)` may return a reason to leave one alone; those
    come back as (item, reason) pairs. Returns (removed, refused).
    """
    tree = work_root if not isinstance(work_root, (str, Path)) else open_tree(work_root)
    removed, refused = 0, []
    with open(receipt_path, "a") as receipt:
        for item in items:
            if item["verdict"] not in verdicts:
                continue
            rel = tree.inside(item["dir"])
            if rel is None:
                raise SystemExit(f"refusing to remove {item['dir']}: outside {tree.describe()}")
            reason = recheck(item) if recheck else None
            if reason:
                refused.append((item, reason))
                continue
            entry = {**item, "removed_at": datetime.now(timezone.utc).isoformat()}
            receipt.write(json.dumps(entry) + "\n")
            receipt.flush()
            tree.remove_dir(rel)
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
                        help="the run's work directory: a path on this machine, or "
                             "s3://bucket/prefix")
    parser.add_argument("--results", metavar="DIR",
                        help="the published results tree, a path or s3://, to check "
                             "that recorded copies still exist and are not links into work")
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
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="list every directory under its reason, not only the counts")
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
    if not args.work_root.startswith("s3://") and not Path(args.work_root).is_dir():
        raise SystemExit(f"--work-root {args.work_root} is not a directory")
    if bool(args.graph) == bool(args.runs):
        raise SystemExit("give --graph or --runs, not both")

    if args.runs:
        from clew.extract.runs import Runs
        runs = Runs(args.runs)
        graph = runs.load(args.run)
        source = f"the {runs.kind} record in {args.runs}"
    else:
        graph = core.load_graph(args.graph)
        source = f"the graph file {args.graph}"
    ignore = tuple(args.ignore) if args.ignore else BOOKKEEPING
    reclaimer = Reclaimer(graph, args.work_root, args.results, args.intermediates,
                          ignore, args.target)
    items = reclaimer.plan()
    for line in reclaimer.warnings:
        print(f"clew: {line}", file=sys.stderr)
    if args.target is not None and not items:
        raise SystemExit(f"no task in this graph ran on target {args.target!r}")

    print_plan(items, graph, args.work_root, args.results, args.intermediates, ignore,
               args.target, args.verbose, source)
    if args.json_out or args.html_out:
        built = plan_to_dict(items, graph, args.work_root, args.results, args.intermediates,
                             ignore, args.target, source)
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
        removed, refused = apply(items, reclaimer.work, args.receipt, verdicts,
                                 reclaimer.recheck)
        for item, reason in refused:
            print(f"kept {item['dir']}: {reason}")
        print(f"\nremoved {removed} directories; receipt in {args.receipt}")


if __name__ == "__main__":
    main()
