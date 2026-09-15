"""
nf-core/sarek. The subject is the patient, who can contribute several
samples (normal and tumour). Donor identity comes from the samplesheet,
never from file contents: five donors' alignment tasks all consumed files
named test_1.fastq.gz on a real run.
"""

from . import adapter as base


class Sarek(base.NextflowAdapter):
    name = "sarek"
    # One patient, several samples (normal, tumour); a tag naming either resolves to the patient.
    triggers = {"patient": base.SheetKind(column="patient", members="sample")}
    # The reference bundle. Invalidating any of it reaches everything
    # calibrated against it.
    load_bearing_inputs = (
        "genome.fasta",
        "genome.fasta.fai",
        "genome.dict",
        "dbsnp_146.hg38.vcf.gz",
        "mills_and_1000G.indels.vcf.gz",
    )

