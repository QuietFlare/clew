"""
Clew domain adapter for Snakemake workflows.

Snakemake has no sample tag. A job is named by its rule and first output
path, "trim (trimmed/sample_1.fq)", so the only place a sample id shows
is inside that path. This adapter reads the ids from a samplesheet column
and looks for each one in the output path: as a whole path component, or
as a token bounded by the separators file names are built from. That is
what keeps "sample_1" out of "sample_10.fq".

Over-inclusion is the safe direction. A merge whose output is named after
two samples enters the graph for both.
"""

import re

from . import nfcore

SEPARATORS = "-_./"


def load_subjects(samplesheet_path, column="sample"):
    """{sample id: []} from one samplesheet column."""
    return nfcore.load_subjects(samplesheet_path, column)


def mentions(path, subject):
    """Whether `subject` appears in `path` bounded by separators or the ends."""
    pattern = (rf"(?:^|[{re.escape(SEPARATORS)}])"
               rf"{re.escape(subject)}"
               rf"(?:$|[{re.escape(SEPARATORS)}])")
    return re.search(pattern, path) is not None


def subject_entry_nodes(graph, subjects):
    """
    {subject: [task hashes]} for every task whose tagged output path names
    the subject. Ids are tried longest first; the separator bound in
    `mentions` is what stops a shorter id matching inside a longer one.
    """
    entry = {subject: set() for subject in subjects}
    ordered = sorted(subjects, key=lambda s: (-len(s), s))
    for task_hash, task in graph["tasks"].items():
        tag = nfcore.task_tag(task)
        if not tag:
            continue
        for subject in ordered:
            if mentions(tag, subject):
                entry[subject].add(task_hash)
    return {subject: sorted(nodes) for subject, nodes in entry.items()}


# Shared machinery, re-exported so callers need only this module.
task_tag = nfcore.task_tag
container_entry_nodes = nfcore.container_entry_nodes
external_input_entry_nodes = nfcore.external_input_entry_nodes
load_assertions = nfcore.load_assertions
outputs_for = nfcore.outputs_for
describe = nfcore.describe
classify = nfcore.classify
