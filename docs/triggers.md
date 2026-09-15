# Triggers

A trigger is `kind:value`. The kind says where the problem enters the
graph and whether it is traced or removed. The value says which one.

```bash
clew impact --graph g.json --trigger container:gatk4
clew impact --graph g.json --trigger patient:donor_003 --samplesheet sheet.csv
clew impact --graph g.json --trigger patient --samplesheet sheet.csv    # every patient
```

`--container X` and `--input X` are short for the two engine kinds people
type most.

## Where a kind comes from

The engine ships four kinds, one per field every graph carries by
contract. Any other word is looked up in this order:

1. The pipeline's adapter, if it declares a kind by that name.
2. The engine's four: `container`, `script`, `process`, `input`.
3. A label key the graph carries on its tasks or edges.

| Kind | Declared by | Entry nodes | Mode |
|---|---|---|---|
| `container` | engine | every task whose image matches | trace |
| `script`, `process` | engine | every task whose field contains the value | trace |
| `input` | engine | every task that consumed that outside file | trace |
| any label key | engine | every task or artifact carrying `labels[key] == value` | trace |
| `patient` | the sarek adapter | every task tagged with the patient or one of its samples | remove |
| `sample` | rnaseq, viralrecon, snakemake | every task tagged with the sample | remove |
| yours | your adapter | whatever your code says | your choice |

The engine never sees the words `patient` or `sample`. It asks the
adapter, and the adapter's kind resolves the value with whatever it needs,
declaring its own flags. `--samplesheet` exists because the nf-core kinds
put it there; on a pipeline with no samplesheet it does not appear. A kind
can also find its inputs from the run itself, and then no flag is typed.

An unknown kind is refused by name, and the message lists the kinds the
adapter declares, the engine's four, and whether the graph carries such a
label. `clew providers` lists every adapter's kinds and marks any that
shadow an engine kind, since an adapter's kind wins over the engine's.

Horus records carry labels natively, so `--trigger site:north` works on a
Horus graph with no adapter at all.

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
the plan's caveats.

### How an input is matched

`input:genome.fasta` matches the exact basename, plus companions named
`genome.fasta.<ext>`, such as `genome.fasta.fai`, since an index is
regenerated with the file it belongs to. A directory input matches by its
own basename. The companions reached are listed under `TRIGGER NOTES`.

## The mode: what kind of wrong is it?

`remove` means the source is withdrawn. Ownership matters: an artifact
that exists only because of this value has nothing left to serve, so it
can be destroyed. A removal kind resolves every value of its kind, not
only the one asked about, because exclusive and shared are computed
against the others.

`trace` means follow what the value touched and leave everything in
place, as with contamination, a tool defect or a stale reference. A
removal traces too; the difference is only what may happen at the end.
Under trace nothing is destroyed and the worst verdict is quarantine.

The kind declares its default. `--mode` overrides it in one direction: a
removal kind can be traced (contamination of one patient is
`--trigger patient:X --mode trace`), but a trace kind cannot be asked
as a removal, because nothing is owned.

## The stories, mapped

| Story | Kind | Mode |
|---|---|---|
| Reference or annotation update | `input` | trace |
| Tool or container defect | `container` | trace |
| Contamination, swap, QC failure | the adapter's owning kind | trace |
| Primer scheme correction | `input` | trace |
| Consent withdrawal | the adapter's owning kind | remove |
| Upstream dataset retraction | `input` | remove, not yet supported |

Retraction is unsupported because removal needs an owner, and computing
what exists only because of one input needs multi-root traversal.

## Extending it

A kind is a small class: a mode, optional flags, and `resolve(graph,
value, args)` returning `{id: [entry nodes]}`. Candidates a site might
write: one exact artifact by checksum, every task in a time window for a
bad reagent lot, a facility, or everything calibrated against a control
rather than merely derived from it. [Providers](providers.md) shows one.

Modes are the closed part. A new verdict would change what remediation
means, so a new mode is a design decision, not a plugin.
