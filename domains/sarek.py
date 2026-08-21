"""
Clew domain adapter — nf-core/sarek.

What is actually sarek-specific, after the shared nf-core conventions moved
to nfcore.py: the samplesheet models a DONOR ("patient") who can contribute
several samples (normal and tumour), so the subject is the patient column
and samples are its members. Donor identity is an ASSERTION carried in from
the samplesheet, never derived from file contents — five different donors'
alignment tasks all consumed files named test_1.fastq.gz on a real run.

Known load-bearing external inputs for this pipeline: the reference bundle.
Invalidating any of them reaches everything calibrated against it.
"""

from . import nfcore

# Externals that must NOT propagate a donor withdrawal (they belong to no
# donor), but each is itself a valid invalidation trigger via --input.
LOAD_BEARING_INPUTS = (
    "genome.fasta",
    "genome.fasta.fai",
    "genome.dict",
    "dbsnp_146.hg38.vcf.gz",
    "mills_and_1000G.indels.vcf.gz",
)


def load_subjects(samplesheet_path):
    """{donor: [sample_ids]} — `patient` is the donor; samples are members."""
    return nfcore.load_subjects(samplesheet_path, "patient", member_column="sample")


# Backwards-compatible name; blast.py and the tests grew up with it.
load_donors = load_subjects

# Shared nf-core machinery, re-exported so callers need only this module.
task_tag = nfcore.task_tag
subject_entry_nodes = nfcore.subject_entry_nodes
container_entry_nodes = nfcore.container_entry_nodes
external_input_entry_nodes = nfcore.external_input_entry_nodes
load_assertions = nfcore.load_assertions
outputs_for = nfcore.outputs_for
describe = nfcore.describe
classify = nfcore.classify


def _owner_of(tag, label_to_donor):
    """Kept under its old name for the regression tests."""
    return nfcore.owner_of(tag, label_to_donor)


def contribution_storage(workdir, work_root=None):
    """Kept for compatibility; the logic lives in nfcore.storage_state."""
    return nfcore.storage_state(workdir)
