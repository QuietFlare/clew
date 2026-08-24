"""
Clew domain helpers shared by every nf-core pipeline adapter.

nf-core pipelines share launch and naming conventions: a CSV samplesheet
with one row per sample, and task display names that append the sample tag
in parentheses ("BWAMEM1_MEM (donor_003)", "BOWTIE2_ALIGN (ERR10000000)").
That convention, not anything pipeline-specific, is what Clew's subject
attribution parses — so it lives here, once.

A pipeline adapter (sarek.py, viralrecon.py, rnaseq.py) supplies only what
actually differs:
  - which samplesheet column names the subject (sarek: patient; most
    others: sample)
  - which external inputs are known to be load-bearing for that pipeline

This file is still domain code: it may know about pipelines and samples.
core/ may not.
"""

import csv
import re
from pathlib import Path

# The trailing parenthetical on a task's display name. Plenty of non-sample
# tasks use the same shape — "(genome)", "(MN908947.3)" — so a captured value
# is only accepted if it matches a subject from the samplesheet.
TAG_PATTERN = re.compile(r"\(([^()]+)\)\s*$")


def load_subjects(samplesheet_path, subject_column, member_column=None):
    """
    Read an nf-core samplesheet and return {subject_id: [member_ids]}.

    `subject_column` names the subject (sarek: "patient", most pipelines:
    "sample"). `member_column`, when given, collects the per-subject members
    (sarek: samples per patient); otherwise each subject stands alone.
    """
    subjects = {}
    with open(samplesheet_path, newline="") as handle:
        for row in csv.DictReader(handle):
            subject = (row.get(subject_column) or "").strip()
            if not subject:
                continue
            subjects.setdefault(subject, [])
            if member_column:
                member = (row.get(member_column) or "").strip()
                if member and member not in subjects[subject]:
                    subjects[subject].append(member)
    return subjects


def task_tag(task):
    """Pull the trailing parenthetical off a task's display name, if present."""
    match = TAG_PATTERN.search(task.get("name", ""))
    return match.group(1).strip() if match else None


def owner_of(tag, label_to_subject):
    """
    Resolve a task tag to a subject, tolerating nf-core suffixes.

    Exact matching is not enough: per-lane steps append the lane
    ("donor_003-L1", "ERR10000000_T1"). Prefixes are only accepted at a
    separator boundary, so "donor_1" does not swallow "donor_10".
    """
    if tag in label_to_subject:
        return label_to_subject[tag]
    for label, subject in label_to_subject.items():
        for separator in ("-", "_", "."):
            if tag.startswith(label + separator):
                return subject
    return None


def subject_entry_nodes(graph, subjects):
    """
    Map each subject to the task nodes where its material enters the graph.

    Core sees only opaque ids. We start from EVERY task tagged with the
    subject: over-inclusive is the safe direction — reporting a clean
    artifact as affected wastes work, reporting an affected artifact as
    clean is the failure that matters.
    """
    label_to_subject = {}
    for subject, members in subjects.items():
        label_to_subject[subject] = subject
        for member in members:
            label_to_subject[member] = subject

    entry = {subject: set() for subject in subjects}
    for task_hash, task in graph["tasks"].items():
        tag = task_tag(task)
        if not tag:
            continue
        owner = owner_of(tag, label_to_subject)
        if owner:
            entry[owner].add(task_hash)

    return {subject: sorted(nodes) for subject, nodes in entry.items()}


def container_entry_nodes(graph, needle):
    """Entry nodes for a tool-defect trigger: tasks whose container matches."""
    subject = f"container:{needle}"
    nodes = sorted(
        h for h, t in graph["tasks"].items() if needle in (t.get("container") or "")
    )
    return {subject: nodes}


def external_input_entry_nodes(graph, filename):
    """
    Entry nodes for an upstream-input trigger: tasks that consumed an
    EXTERNAL file with this basename. This is the reference-update /
    load-bearing-input case.
    """
    subject = f"input:{filename}"
    nodes = set()
    for edge in graph["edges"]:
        if edge["producer"] != "EXTERNAL":
            continue
        if Path(edge["filename"]).name == filename:
            nodes.add(edge["consumer"])
    return {subject: sorted(nodes)}


def load_assertions(path):
    """
    Externally-asserted facts the pipeline cannot know about itself
    (publication, so far). Returns {task_hash: assertion_record}; a missing
    path honestly means "publication status unknown".
    """
    if not path:
        return {}
    import json

    data = json.loads(Path(path).read_text())
    return {rec["task"]: rec for rec in data.get("published", [])}


def outputs_for(graph, task_hashes):
    """Every file produced by the given tasks, as 'hash/filename'."""
    files = []
    for task_hash in sorted(task_hashes):
        for filename in graph["outputs"].get(task_hash, []):
            files.append(f"{task_hash}/{filename}")
    return files


def describe(graph, task_hash):
    """Short human label for a task: the process name without its full path."""
    task = graph["tasks"].get(task_hash, {})
    return (task.get("process", "") or "?").split(":")[-1]


def storage_state(workdir, work_root=None):
    """
    Whether the task's artifacts are still on disk — or None for "not checked".

    NONE IS NOT A THIRD OUTCOME, IT IS THE ABSENCE OF ONE. Storage is a live
    property of the world, and the person asking Clew a question is often not
    standing where the pipeline ran: a different host, a CI runner, a laptop
    reading a graph someone emailed them. Guessing there is not conservative
    in either direction, so this refuses.

    In particular DESTROYED is now only ever returned after actually looking
    and not finding. It used to be returned whenever `is_dir()` was false,
    which fired identically when the path was never recorded, when the volume
    was not mounted, when the graph came from another machine, and when the
    fixtures were anonymised for publication. All of those became
    ALREADY_GONE — "no longer exists; nothing to do" — which is the one error
    direction this project exists not to make. A false negative that silences
    an obligation is worth more care than a false positive that wastes work.

    `work_root` is the caller saying where to look. The recorded path is from
    whichever machine ran the pipeline, so only its last two components — the
    two-character prefix and the full task hash, which is how the engine lays
    out a work directory — are joined onto the root given here. That makes a
    graph portable between hosts without pretending the recorded absolute
    path means anything locally.
    """
    if not workdir or not work_root:
        return None
    parts = Path(workdir).parts
    if len(parts) < 2:
        return None
    local = Path(work_root, *parts[-2:])
    return "WRITABLE" if local.is_dir() else "DESTROYED"


def classify(graph, task_hash, exclusive, published=None, work_root=None):
    """
    Contribution class and storage for one affected task, from pipeline
    evidence alone: a task whose script and container were recorded can be
    re-executed (REGENERABLE); one without fails closed to IRREDUCIBLE.
    Publication arrives as an external assertion and sets `terminal`.

    `storage` is None unless `work_root` says where to look. See storage_state.
    """
    task = graph["tasks"].get(task_hash, {})
    storage = storage_state(task.get("workdir", ""), work_root)

    reproducible = bool(task.get("script")) and bool(task.get("container"))
    klass = "REGENERABLE" if reproducible else "IRREDUCIBLE"

    assertion = (published or {}).get(task_hash)
    if assertion:
        reason = (
            f"published ({assertion.get('what', 'unspecified')}), asserted by "
            f"{assertion.get('asserted_by', '?')} on {assertion.get('date', '?')}"
        )
    elif reproducible:
        reason = "script and container recorded; task can be re-executed"
    else:
        reason = "script or container missing; task cannot be reproduced"

    if storage is None:
        reason += "; storage not checked (no --work-root given)"

    return {
        "contribution": klass,
        "storage": storage,
        "exclusive": exclusive,
        "terminal": assertion is not None,
        "reason": reason,
    }
