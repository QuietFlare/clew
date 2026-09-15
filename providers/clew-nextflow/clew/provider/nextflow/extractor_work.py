"""
Nextflow work/ symlinks, read into the graph.

Each task runs in its own folder under work/, and its inputs are symlinks
into the producing task's folder. That was meant to save disk; it also
records the run. Edges are stored consumer <- producer, the direction the
filesystem holds them.

Limits. Only symlink staging leaves a trail: copy, hard link and cloud
executors do not, and main() refuses rather than emit an empty graph. A file
a task forwards unchanged is staged from the original, so that hop is
missing and the next task looks externally fed. Prefer the lineage store
where pass-through is common.
"""

import json
import os
import sys
from pathlib import Path

from clew.graph.graph import task_status
from clew.contracts import Extractor

# Nextflow's own bookkeeping files. Not data, never lineage.
SKIP_PREFIXES = (".command", ".exitcode")


def load_run(jsonl_path):
    """
    Read one run's weblog events and return {task_hash: {metadata}}.

    We only care about tasks that actually ran in THIS run. The work/
    directory accumulates every run ever executed, so without this filter
    we would happily build a graph that mixes several runs together.
    """
    tasks = {}
    for line in Path(jsonl_path).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        trace = msg.get("trace") or {}
        task_hash = trace.get("hash")
        if not task_hash:
            continue

        # Later events carry more complete information, so overwrite freely.
        tasks[task_hash] = {
            "hash": task_hash,
            "task_id": trace.get("task_id"),
            "name": trace.get("name", ""),
            "process": trace.get("process", ""),
            "container": trace.get("container", ""),
            "status": task_status(trace.get("status")),
            "engine_status": trace.get("status") or "",
            "target": "",   # one machine per run; nothing to record
            "workdir": trace.get("workdir", ""),
            # The exact command that ran. Together with `container` this is
            # what makes a task re-executable, and therefore what decides
            # whether its output is REGENERABLE. Without it, classification
            # has to fail closed to IRREDUCIBLE.
            "script": trace.get("script", ""),
        }
    return tasks


def find_workdir(work_root, task_hash):
    """
    The folder on disk for a hash like fc/861a98: list the two-character
    directory and match the six-character prefix. Two folders sharing the
    prefix are refused rather than guessed; that takes about a million task
    directories.
    """
    prefix_dir, name_prefix = task_hash.split("/", 1)
    parent = Path(work_root) / prefix_dir
    if not parent.is_dir():
        return None
    found = sorted(child for child in parent.iterdir()
                   if child.is_dir() and child.name.startswith(name_prefix))
    if len(found) > 1:
        raise SystemExit(
            f"clew extract-work: task hash {task_hash} matches "
            f"{len(found)} work directories, so their inputs cannot be told "
            f"apart: {', '.join(c.name for c in found)}. Use the lineage "
            "store for this run, which keys tasks on the full hash.")
    return found[0] if found else None


def target_to_hash(target, work_root):
    """
    Which task produced the file a symlink points at: "XX/YYYYYY",
    "EXTERNAL", or None. External inputs are staged under
    work/stage-<uuid>/3e/d2a534.../, which looks exactly like a task hash
    and is not one, so the stage- check runs before any hash parsing.
    """
    target = Path(target)
    work_root = Path(work_root).resolve()

    try:
        rel = target.resolve().relative_to(work_root)
    except ValueError:
        # Points somewhere outside work/ entirely - an original input file.
        return "EXTERNAL"

    parts = rel.parts

    # Check these FIRST. This is the trap.
    #
    #   stage-<uuid>/  files entering the pipeline from outside
    #   tmp/           files Nextflow generates itself from the run config
    #                  (workflow_summary_mqc.yaml and similar)
    #
    # Neither is a task, but both contain a two-level path that parses as a
    # perfectly plausible task hash if you don't look.
    if parts[0] == "tmp" or any(p.startswith("stage-") for p in parts):
        return "EXTERNAL"

    if len(parts) < 2:
        return None

    return f"{parts[0]}/{parts[1][:6]}"


def _record_symlink(edges, task_hash, entry, workdir, work_root):
    """Record one symlink as an input edge: this task <- whoever produced it."""
    raw_target = os.readlink(entry)
    # Symlinks may be relative; resolve against the directory containing them.
    if not os.path.isabs(raw_target):
        raw_target = os.path.join(entry.parent, raw_target)

    producer = target_to_hash(raw_target, work_root)

    # Skip self-edges. Some tools (Strelka) symlink within their own output
    # directory, which parses as "this task consumed its own output". That is
    # a cycle, and traversal must stay acyclic.
    if producer == task_hash:
        return

    edges.append(
        {
            "consumer": task_hash,
            "producer": producer,
            # Keep the path relative to the task folder, so staged
            # subdirectories stay visible: "18/test.strelka.variants.summary"
            "filename": str(Path(entry).relative_to(workdir)),
            "target": str(raw_target),
        }
    )


def extract(jsonl_path, work_root):
    """Walk every task folder in the run and collect its input edges."""
    tasks = load_run(jsonl_path)
    edges = []
    outputs = {}
    output_details = {}
    missing_dirs = []

    for task_hash in sorted(tasks):
        workdir = find_workdir(work_root, task_hash)
        if workdir is None:
            missing_dirs.append(task_hash)
            continue
        tasks[task_hash]["workpath"] = str(workdir.relative_to(work_root))

        produced = []

        # Walk the whole task folder, not just its top level.
        #
        # WHY: when a process receives a *collection* of files, Nextflow
        # stages each one inside a numbered subdirectory (./1/, ./18/, ...)
        # so their names cannot collide. MULTIQC is the obvious case - it
        # aggregates reports from every other step, and every one of those
        # inputs lives one level down.
        #
        # Looking only at the top level makes such a task appear to have no
        # inputs at all. That is a FALSE NEGATIVE: the graph reports a task
        # as carrying no donor data when it carries plenty. Wrong in the
        # dangerous direction, so we walk recursively.
        for root, dirnames, filenames in os.walk(workdir, followlinks=False):
            root_path = Path(root)

            # A symlinked *directory* is itself an input (e.g. a bwa index
            # bundle). Record it and do not descend into it.
            for dirname in list(dirnames):
                entry = root_path / dirname
                if entry.is_symlink():
                    dirnames.remove(dirname)
                    _record_symlink(edges, task_hash, entry, workdir, work_root)

            for filename in filenames:
                if filename.startswith(SKIP_PREFIXES):
                    continue
                entry = root_path / filename
                if entry.is_symlink():
                    _record_symlink(edges, task_hash, entry, workdir, work_root)
                else:
                    produced.append(entry)

        outputs[task_hash] = sorted(str(p.relative_to(workdir))
                                    for p in produced)
        # Size is recorded here, while the workdir still exists, because it
        # is the only join key that survives publishDir copying a file: the
        # copy keeps its name and byte count but gets a new path and mtime.
        # Read it now or lose it, this extractor runs against a work tree
        # that is about to be cleaned.
        details = []
        for path in produced:
            try:
                size = path.stat().st_size
            except OSError:
                continue
            details.append({"file": str(path.relative_to(workdir)),
                            "size": size})
        output_details[task_hash] = sorted(details,
                                           key=lambda d: d["file"])

    return tasks, edges, outputs, output_details, missing_dirs


def coverage_notes(missing):
    """
    What this graph could not see, carried on the graph like the store
    extractor does, so it reaches evidence bundles and the dashboard.
    """
    notes = []
    if missing:
        notes.append(
            f"{len(missing)} of the run's tasks have no work directory, so "
            "their inputs and outputs are absent from this graph and "
            "anything downstream of them cannot be reached: "
            + ", ".join(missing) + ".")
    return notes


class NextflowWork(Extractor):
    name = "nextflow-work"
    description = "a Nextflow work/ tree, from its symlinks, any engine version"

    def add_arguments(self, parser):
        parser.add_argument("--jsonl", required=True, help="Petri weblog JSONL for one run")
        parser.add_argument("--work", required=True, help="Nextflow work/ directory")
        parser.add_argument("--allow-partial", action="store_true",
                            help="write the graph even when some tasks' work "
                                 "directories are gone; the missing tasks are "
                                 "recorded as a coverage note on the graph")

    def extract(self, args):
        tasks, edges, outputs, output_details, missing = extract(args.jsonl, args.work)
        self.missing = missing

        # A task whose directory is gone contributes no edges, so a withdrawal
        # stops short of everything it fed. Refuse rather than under-report.
        if missing and not args.allow_partial:
            sys.exit(
                f"clew extract nextflow-work: {len(missing)} of {len(tasks)} tasks have "
                f"no work directory under {args.work}:\n  "
                + "\n  ".join(missing)
                + "\nEither the work tree was cleaned, or --work names another "
                "run's tree. A graph missing these tasks would under-report; "
                "pass --allow-partial to write it anyway with the gap recorded "
                "as a coverage note, or use the lineage store if the run "
                "recorded one.")

        # Tasks but no symlinks: inputs were staged by copy or hard link, so
        # there is no lineage here. An empty graph would be a false negative.
        if tasks and not edges and len(missing) < len(tasks):
            sys.exit(
                "clew extract nextflow-work: no symlinks found in any task directory.\n"
                "This extractor reads the symlinks Nextflow leaves with the\n"
                "default stage-in mode ('symlink'/'rellink') on local and HPC\n"
                "executors. Runs staged by copy or hard link, including cloud\n"
                "executors staging from object storage, leave no symlinks to\n"
                "read. For those runs, enable Nextflow's lineage store and use\n"
                "'clew extract nextflow' instead.")

        graph = {"tasks": tasks, "edges": edges, "outputs": outputs,
                 "output_details": output_details}
        coverage = coverage_notes(missing)
        if coverage:
            graph["coverage"] = coverage
        return graph

    def summarize(self, graph, args):
        tasks, edges = graph["tasks"], graph["edges"]
        known = set(tasks)
        resolved = [e for e in edges if e["producer"] in known]
        external = [e for e in edges if e["producer"] == "EXTERNAL"]
        dangling = [e for e in edges if e["producer"] not in known and e["producer"] != "EXTERNAL"]

        print(f"tasks in run       : {len(tasks)}")
        print(f"work dirs missing  : {len(self.missing)}")
        for task_hash in self.missing:
            print(f"    {task_hash}  {tasks[task_hash].get('name', '')}")
        print(f"input files (edges): {len(edges)}")
        print(f"  resolved to task : {len(resolved)}")
        print(f"  external inputs  : {len(external)}")
        print(f"  DANGLING         : {len(dangling)}")
        print()
        print("=== EDGES (consumer <- producer) ===")
        for e in edges:
            producer = e["producer"] or "???"
            print(f"{e['consumer']}  <-  {producer:<12} ({e['filename']})")
        if dangling:
            # Almost always the stage- check failed, or work/ belongs to another run.
            print()
            print("=== DANGLING (producer not a task in this run) ===")
            for e in dangling:
                print(f"{e['consumer']}  <-  {e['producer']}  ({e['filename']})")
                print(f"    target: {e['target']}")
        self.coverage(graph)


main = NextflowWork.main


if __name__ == "__main__":
    sys.exit(main())
