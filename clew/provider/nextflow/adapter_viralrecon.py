"""
nf-core/viralrecon. The subject is the specimen (an ENA run accession such
as ERR10000000); there is no donor concept and none is invented. The
everyday triggers are a contaminated or swapped specimen, and reference
or primer-scheme updates.
"""

from . import adapter as base


class Viralrecon(base.NextflowAdapter):
    name = "viralrecon"
    triggers = {"sample": base.SheetKind(column="sample")}
    # Basenames from a real 2.6.0 run (219-task COG-UK run). Other releases
    # name the same files differently, which is why we match what the
    # store recorded, not what a config promises.
    load_bearing_inputs = (
        "nCoV-2019.reference.fasta",
        "nCoV-2019.primer.bed",
        "GCA_009858895.3_ASM985889v3_genomic.200409.gff.gz",
        "kraken2_human.tar.gz",
        "nextclade_sars-cov-2_MN908947_2022-06-14T12_00_00Z.tar.gz",
    )

