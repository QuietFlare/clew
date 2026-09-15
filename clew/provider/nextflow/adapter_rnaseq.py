"""
nf-core/rnaseq. The subject is the sample. The frequent trigger is the
annotation: a GTF bump invalidates every count matrix and differential
result computed against it, while alignments to the unchanged genome
sequence may survive.
"""

from . import adapter as base


class Rnaseq(base.NextflowAdapter):
    name = "rnaseq"
    triggers = {"sample": base.SheetKind(column="sample")}
    # Basenames as recorded in a real 3.26.0 run (iGenomes R64-1-1). The GTF
    # has one direct consumer and 149 of 171 tasks in its blast radius.
    load_bearing_inputs = (
        "genome.fa",
        "genes.gtf",
        "genes.bed",
    )

