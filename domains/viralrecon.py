"""
Clew domain adapter — nf-core/viralrecon (viral surveillance).

The subject is the SAMPLE (a sequenced specimen, e.g. an ENA run accession
like ERR10000000): the samplesheet has no donor concept, and none is
invented. The everyday invalidation trigger in this domain is not consent —
it is a specimen found contaminated or swapped after its data was used, and
reference or primer-scheme updates.

Known load-bearing external inputs: the viral reference genome, its
annotation, and the amplicon primer scheme. A primer-scheme correction
invalidates every consensus built with it — the classic "quiet revision
with loud consequences".
"""

from . import nfcore

LOAD_BEARING_INPUTS = (
    "GCA_009858895.3_ASM985889v3_genomic.200409.fna.gz",   # SARS-CoV-2 reference
    "GCA_009858895.3_ASM985889v3_genomic.200409.gff.gz",   # annotation
    "nCoV-2019.artic.V3.scheme.bed",                       # amplicon primer scheme
)


def load_subjects(samplesheet_path):
    """{sample: []} — each specimen stands alone."""
    return nfcore.load_subjects(samplesheet_path, "sample")


task_tag = nfcore.task_tag
subject_entry_nodes = nfcore.subject_entry_nodes
container_entry_nodes = nfcore.container_entry_nodes
external_input_entry_nodes = nfcore.external_input_entry_nodes
load_assertions = nfcore.load_assertions
outputs_for = nfcore.outputs_for
describe = nfcore.describe
classify = nfcore.classify
