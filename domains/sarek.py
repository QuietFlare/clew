"""
Clew domain adapter — nf-core/sarek.

This file is allowed to know about sarek, samplesheets, donors and FASTQs.
core/ is not. Everything sarek-specific belongs here, so that adding another
domain later means adding a file next to this one, not editing core.

WHAT THIS SOLVES
----------------
Donor identity is NOT in the file graph. Demonstrated on a real 5-donor run:
every alignment task consumed files with identical names.

    65/b51de1   test_1.fastq.gz, test_2.fastq.gz
    80/10c05c   test_1.fastq.gz, test_2.fastq.gz
    9b/e1fa4d   test_1.fastq.gz, test_2.fastq.gz    <- five different donors
    cc/4270ae   test_1.fastq.gz, test_2.fastq.gz
    ff/69020d   test_1.fastq.gz, test_2.fastq.gz

Same filenames, five donors. The graph cannot tell them apart, and never
could - a FASTQ contains sequences, not identity.

The identity comes from the samplesheet, which sarek copies into each task's
display name:

    BWAMEM1_MEM (donor_003)

So donor attribution is an ASSERTION carried in from outside, not something
derived from the pipeline. That is exactly the position Clew takes about
every domain fact: record who claimed it, do not infer it.

FRAGILITY, ACKNOWLEDGED
-----------------------
Parsing a display name is brittle. It is also the correct place for brittle
code: domains/ is the layer designed to be wrong and cheap to replace. If
sarek changes its naming, this file changes and core does not.
"""

import csv
import re
from pathlib import Path

# sarek appends the sample tag in parentheses at the end of a task name:
#   "NFCORE_SAREK:SAREK:FASTQ_ALIGN:BWAMEM1_MEM (donor_003)"
# Plenty of non-donor tasks use the same shape - "(genome)",
# "(genome.interval_list)" - so the captured value is only accepted if it
# matches a donor actually listed in the samplesheet.
TAG_PATTERN = re.compile(r"\(([^()]+)\)\s*$")


def load_donors(samplesheet_path):
    """
    Read the sarek samplesheet and return {donor_id: [sample_ids]}.

    `patient` is the donor. sarek already models the concept - one patient can
    contribute several samples (e.g. normal and tumour), which is why this is
    a mapping rather than a flat list.
    """
    donors = {}
    with open(samplesheet_path, newline="") as handle:
        for row in csv.DictReader(handle):
            patient = (row.get("patient") or "").strip()
            sample = (row.get("sample") or "").strip()
            if not patient:
                continue
            donors.setdefault(patient, [])
            if sample and sample not in donors[patient]:
                donors[patient].append(sample)
    return donors


def task_tag(task):
    """Pull the trailing parenthetical off a task's display name, if present."""
    match = TAG_PATTERN.search(task.get("name", ""))
    return match.group(1).strip() if match else None


def subject_entry_nodes(graph, donors):
    """
    Map each donor to the task nodes where their material enters the graph.

    This is what core/ receives. Core sees only opaque ids - it never learns
    the word "donor".

    We start from EVERY task tagged with the donor, not just the earliest.
    Starting from all of them is over-inclusive rather than under-inclusive,
    and over-inclusive is the safe direction: reporting a clean artifact as
    affected wastes work, reporting an affected artifact as clean is the
    failure that matters.
    """
    # A sample id also identifies its donor, so accept either spelling.
    label_to_donor = {}
    for donor, samples in donors.items():
        label_to_donor[donor] = donor
        for sample in samples:
            label_to_donor[sample] = donor

    entry = {donor: set() for donor in donors}
    for task_hash, task in graph["tasks"].items():
        tag = task_tag(task)
        if not tag:
            continue
        owner = _owner_of(tag, label_to_donor)
        if owner:
            entry[owner].add(task_hash)

    return {donor: sorted(nodes) for donor, nodes in entry.items()}


def _owner_of(tag, label_to_donor):
    """
    Resolve a task tag to a donor, tolerating sarek's suffixes.

    Exact matching is not enough. Per-lane steps append the lane:

        BWAMEM1_MEM (donor_003)      exact
        FASTQC      (donor_003-L1)   donor plus lane

    Matching only exactly missed all five FASTQC tasks on a 5-donor run, and
    since FASTQC reads the FASTQ directly, nothing upstream reaches it - so
    those tasks were invisible, not merely mislabelled. Another false
    negative, the direction that matters.

    Prefixes are only accepted at a separator boundary, so "donor_1" does not
    swallow "donor_10".
    """
    if tag in label_to_donor:
        return label_to_donor[tag]
    for label, donor in label_to_donor.items():
        for separator in ("-", "_", "."):
            if tag.startswith(label + separator):
                return donor
    return None


def container_entry_nodes(graph, needle):
    """
    Entry nodes for a tool-defect trigger: every task that ran inside a
    container whose name contains `needle`.

    This is the most frequent trigger in practice — "GATK x.y.z had a bug,
    what did we produce with it?" — and it needs nothing from the donor
    model. The subject is the tool, and its material "enters" the graph at
    every task that executed it.
    """
    subject = f"container:{needle}"
    nodes = sorted(
        h for h, t in graph["tasks"].items() if needle in (t.get("container") or "")
    )
    return {subject: nodes}


def external_input_entry_nodes(graph, filename):
    """
    Entry nodes for an upstream-input trigger: every task that consumed an
    EXTERNAL file with this basename.

    This is the reference-update / load-bearing-input case. genome.fasta is
    not downstream of any donor, yet invalidating it reaches everything
    calibrated against it. Matching is on basename because external staging
    paths vary per run while the file's identity does not.
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
    Read externally-asserted facts the pipeline cannot know about itself.

    Publication is the one modelled so far. The file records WHO asserted
    WHAT and WHEN — Clew's position is that these are inputs carried in from
    outside, never inferences:

        {"published": [
            {"task": "c9/023b13", "what": "cohort QC report, Fig 3",
             "asserted_by": "s.jagadeesh", "date": "2026-08-20",
             "reference": "doi:10.0000/example"}
        ]}

    Returns {task_hash: assertion_record}. Missing file means no assertions,
    which honestly means "publication status unknown, treated as unpublished"
    — the caveat blast.py prints exists precisely for this case.
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


def classify(graph, task_hash, exclusive, work_root=None, published=None):
    """
    Assign a contribution class and storage state to one affected task.

    Core owns the vocabulary; this function only decides which term applies to
    a sarek task. It is the "physics" layer of the three-layer rule: what can
    be determined from the pipeline itself, without asking a human.

    REGENERABLE IS THE DEFAULT HERE, AND THAT IS DOMAIN KNOWLEDGE
    ------------------------------------------------------------
    A workflow engine exists to make steps re-runnable. Nextflow records the
    exact script and the exact container image for every task, so given the
    remaining inputs a task can be executed again without the withdrawn
    donor. That is the definition of REGENERABLE.

    It is not free, though - it decays. If the script or container was never
    recorded, the step cannot be reproduced, and the class drops to
    IRREDUCIBLE. This is why contribution class must be a dated fact rather
    than a fixed property of an edge: a task that is regenerable today stops
    being regenerable when its container image is delisted.

    NOTHING HERE IS SEPARABLE, AND THAT IS HONEST
    ---------------------------------------------
    Separability needs a cheap operation that subtracts one contribution from
    a finished artifact. sarek produces none: MultiQC renders plots computed
    across every donor, and per-donor files belong wholly to one donor.

    A SEPARABLE instance would look like a merged BAM retaining per-donor read
    groups, where one donor's reads can be filtered out in place. Do not
    manufacture one to make the enum look used.
    """
    task = graph["tasks"].get(task_hash, {})

    # Storage: does the artifact still exist on disk?
    workdir = task.get("workdir", "")
    storage = contribution_storage(workdir, work_root)

    # Exclusive artifacts need no class - the whole thing goes. Report the
    # class anyway so the evidence bundle records why.
    reproducible = bool(task.get("script")) and bool(task.get("container"))
    klass = "REGENERABLE" if reproducible else "IRREDUCIBLE"

    # Publication cannot be observed in the pipeline; it arrives as an
    # assertion from outside, with an actor and a date attached. Core owns
    # `terminal`; this is the only place the sarek domain sets it.
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

    return {
        "contribution": klass,
        "storage": storage,
        "exclusive": exclusive,
        "terminal": assertion is not None,
        "reason": reason,
    }


def contribution_storage(workdir, work_root=None):
    """WRITABLE if the task directory still exists, DESTROYED if not."""
    if not workdir:
        return "DESTROYED"
    return "WRITABLE" if Path(workdir).is_dir() else "DESTROYED"


def find_samplesheet(graph):
    """Best-effort: locate donors.csv next to this project."""
    candidate = Path(__file__).resolve().parent.parent / "donors.csv"
    return candidate if candidate.exists() else None
