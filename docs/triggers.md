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
new flag. Horus records carry labels natively.

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
