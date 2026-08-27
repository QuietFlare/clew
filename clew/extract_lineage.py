"""
Clew — lineage extractor for Nextflow runs.

WHY THIS EXISTS
---------------
Nextflow runs each pipeline step in its own private folder under work/.
A step's *input* files are not copied into that folder — that would waste
enormous disk space on genomic data. Instead Nextflow leaves a symlink
pointing at the file where it actually lives, which is the folder of the
step that produced it.

Those symlinks were never intended as a provenance system. They exist to
save disk. But they accidentally record the complete history of the run:

    step P's folder contains:  reads.bam -> work/<step N's folder>/reads.bam
    => "P consumed a file that N produced"

This script reads those symlinks and rebuilds the graph.

WHAT IT PRODUCES
----------------
An edge list, one line per input file:

    13/46f32e  <-  fc/861a98   (chr22_1-40001.bed.gz)
    13/46f32e  <-  EXTERNAL    (genome.fasta)

Edges point BACKWARDS (consumer <- producer), because that is the direction
the filesystem records. Traversing downstream means inverting them.

USAGE
-----
    clew extract-work \
        --jsonl /path/to/Petri/logs/<run-id>.jsonl \
        --work  /path/to/Petri/work
"""

import argparse
import json
import os
from pathlib import Path

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
            "status": trace.get("status", ""),
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
    Turn a task hash like 'fc/861a98' into its folder on disk.

    The hash is abbreviated: two-character directory, then the first six
    characters of a much longer directory name. So we list the parent and
    match by prefix.
    """
    prefix_dir, name_prefix = task_hash.split("/", 1)
    parent = Path(work_root) / prefix_dir
    if not parent.is_dir():
        return None
    for child in parent.iterdir():
        if child.is_dir() and child.name.startswith(name_prefix):
            return child
    return None


def target_to_hash(target, work_root):
    """
    Given the path a symlink points at, work out which task produced it.

    Returns one of:
        "XX/YYYYYY"  -- a task hash
        "EXTERNAL"   -- came from outside the pipeline
        None         -- unrecognisable

    THE TRAP
    --------
    Files entering the pipeline from outside are staged under directories
    named 'stage-<uuid>', like:

        work/stage-b3809b93-.../3e/d2a534.../genome.fasta

    That '3e/d2a534' looks EXACTLY like a task hash and is not one. Checking
    for 'stage-' must happen before any hash parsing, or the graph fills up
    with tasks that never existed.
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
    missing_dirs = []

    for task_hash in sorted(tasks):
        workdir = find_workdir(work_root, task_hash)
        if workdir is None:
            missing_dirs.append(task_hash)
            continue

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
                    produced.append(str(entry.relative_to(workdir)))

        outputs[task_hash] = sorted(produced)

    return tasks, edges, outputs, missing_dirs


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rebuild Nextflow lineage from work/ symlinks.")
    parser.add_argument("--jsonl", required=True, help="Petri weblog JSONL for one run")
    parser.add_argument("--work", required=True, help="Nextflow work/ directory")
    parser.add_argument("--json-out", help="Optional path to write the graph as JSON")
    args = parser.parse_args(argv)

    tasks, edges, outputs, missing = extract(args.jsonl, args.work)

    known = set(tasks)
    resolved = [e for e in edges if e["producer"] in known]
    external = [e for e in edges if e["producer"] == "EXTERNAL"]
    dangling = [e for e in edges if e["producer"] not in known and e["producer"] != "EXTERNAL"]

    print(f"tasks in run       : {len(tasks)}")
    print(f"work dirs missing  : {len(missing)}")
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
        # Dangling edges almost always mean the stage- check failed, or the
        # work/ directory belongs to a different run than the JSONL.
        print()
        print("=== DANGLING (producer not a task in this run) ===")
        for e in dangling:
            print(f"{e['consumer']}  <-  {e['producer']}  ({e['filename']})")
            print(f"    target: {e['target']}")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"tasks": tasks, "edges": edges, "outputs": outputs}, indent=2)
        )
        print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()
