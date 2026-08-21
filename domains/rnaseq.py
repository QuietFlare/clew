"""
Clew domain adapter — nf-core/rnaseq (bulk RNA sequencing).

The subject is the SAMPLE. The headline invalidation trigger here is the
ANNOTATION: gene models come from the GTF, so an annotation release bump
("Ensembl updated the GTF") invalidates every count matrix and differential
expression result computed against the old one — while the raw alignments
against the unchanged genome sequence may survive. That split is exactly
what per-input blast radii are for.
"""

from . import nfcore

LOAD_BEARING_INPUTS = (
    "genome.fasta",   # iGenomes R64-1-1 sequence
    "genes.gtf",      # iGenomes annotation — the frequent-update trigger
)


def load_subjects(samplesheet_path):
    """{sample: []} — each sample stands alone."""
    return nfcore.load_subjects(samplesheet_path, "sample")


task_tag = nfcore.task_tag
subject_entry_nodes = nfcore.subject_entry_nodes
container_entry_nodes = nfcore.container_entry_nodes
external_input_entry_nodes = nfcore.external_input_entry_nodes
load_assertions = nfcore.load_assertions
outputs_for = nfcore.outputs_for
describe = nfcore.describe
classify = nfcore.classify
