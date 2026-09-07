# Triggers

Clew has no hardcoded scenarios. Every trigger combines two independent
choices, a selector and a mode, and the familiar stories are named cells in
that grid.

## The selector: where does the problem enter the graph?

| Selector | Flag | Entry nodes |
|---|---|---|
| subject | `--subject X` | every task attributed to one sample, donor or batch |
| container | `--container Y` | every task that ran in a matching container |
| external input | `--input Z` | every task that consumed that outside file |
| generic | `--trigger kind:value` | see below |

`--donor` is the former name of `--subject` and still works.

The generic form takes `kind:value`, for example `container:gatk4`,
`script:prep.py`, `input:genome.fa` or `subject:batch_017`. An unknown kind
is read as a label key, so a graph whose tasks carry labels such as
`{tissue: liver}` answers `--trigger tissue:liver` with no adapter and no
new flag. Horus records carry labels natively. `subject:X` with
`--samplesheet` is the same as `--subject X`: nf-core tasks carry no
labels, so the samplesheet is what resolves one. Without a samplesheet it
reads the graph's `subject` labels, and says so when there are none.

### How a container is matched

The needle and each task's image are read as a name and a version before
they are compared. `samtools:1.21` matches `quay.io/biocontainers/samtools:1.21--h50ea8bc_0`
because the name agrees and the tag begins with the version. It does not
match `samtools:1.16.1`, nor `samtools:1.2`, since a version prefix must
end at a separator. A needle without a version matches every version.

The forms read are `registry/path/name:tag`, `name@sha256:...`, Wave
images such as `community.wave.seqera.io/library/bwa_htslib_samtools:56c9f8d5201889a4`
(tools joined by `_`, a hash for a tag, no version), Singularity cache
names with `/` and `:` turned to `-` and an `.img` suffix, `conda@hash`,
and a bare `name-version`. A tool inside a multi-tool image matches by
name, so `samtools` finds `bwa_htslib_samtools`.

When the needle carries a version and the image carries none, the task
matches on name alone and the output says so, under `TRIGGER NOTES` and in
the plan's caveats. On the shipped sarek run `samtools:1.21` reaches all
26 samtools tasks, 11 of them on name only, where substring matching found
15 and reported 46 affected instead of 72.

### How an input is matched

`--input genome.fasta` matches the exact basename, plus companions named
`genome.fasta.<ext>`, such as `genome.fasta.fai`, since an index is
regenerated with the file it belongs to. A directory input matches by its
own basename. The companions reached are listed under `TRIGGER NOTES`.

## The mode: what kind of wrong is it?

Two modes exist, and they are not interchangeable.

`remove` means the source must be taken out, as in a withdrawal. Ownership
matters. An artifact that exists only because of this subject has nothing
left to serve, so it can be destroyed.

`distrust` means the data is suspect but still wanted, as with
contamination, a tool defect or a stale reference. Nothing is destroyed. The
worst verdict is quarantine, because you will want these artifacts again
once the cause is fixed.

## The stories, mapped

| Story | Selector | Mode |
|---|---|---|
| Reference or annotation update | external input | distrust |
| Tool or container defect | container | distrust |
| Sample contamination, swap, QC failure | subject | distrust |
| Primer scheme correction | external input | distrust |
| Consent withdrawal | subject | remove |
| Upstream dataset retraction | external input | remove, not yet supported |

Retraction is unsupported because removal needs an owner, and computing
what exists only because of one input needs multi-root traversal.

Defaults preserve the common cases. `--subject` implies remove, the others
imply distrust, and `--mode` overrides. Contamination is
`--subject X --mode distrust`.

## Extending it

Selectors are the extension point. A selector is anything that can name a
set of entry nodes, and the engine only ever sees the set. Candidates: one
exact artifact by checksum, every task in a time window for a bad reagent
lot, a facility, or an edge kind such as everything calibrated against a
control rather than merely derived from it.

Modes are the closed part. A new verdict would change what remediation
means, so a new mode is a design decision, not a plugin.
