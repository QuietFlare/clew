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

from clew.core import contribution
from clew.core import graph as core_graph
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


# ---------------------------------------------------------------------------
# Moved to core: none of these read anything nf-core specific, they only read
# the graph schema. Re-exported here so existing callers keep working.
# ---------------------------------------------------------------------------
container_entry_nodes = core_graph.container_entry_nodes
external_input_entry_nodes = core_graph.external_input_entry_nodes
load_assertions = core_graph.load_assertions
outputs_for = core_graph.outputs_for
describe = core_graph.describe
storage_state = contribution.storage_state
classify = contribution.classify


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
