# ADR 0011: Trigger kinds are the adapter's words

Status: accepted

## Context

The domain contract required two methods, `load_subjects` and
`subject_entry_nodes`, and `impact` had `--subject` and `--samplesheet`
flags. All four came from the sarek adapter. A pipeline with no
samplesheet, DNAnexus or Latch or an imaging workflow on Cromwell, had to
implement methods it had nothing to say about, and every user typed a
genomics word the engine had no basis to know. The engine already
resolved `container`, `script`, `process`, `input` and any label without
an adapter; only the owned kind was hardcoded.

## Decision

A trigger is `kind:value`. A kind is a small object with a mode, optional
flags of its own, and `resolve(graph, value, args)` returning entry
nodes. The engine ships the four kinds every graph can answer by
contract. A domain declares the rest in its own words:

```python
triggers = {"patient": SheetKind(column="patient", members="sample")}
```

Nothing is required of a domain. Lookup is the adapter's kinds, then the
engine's, then a label key the graph carries; an adapter's kind wins over
the engine's, and `clew providers` marks the shadow. The mode is a closed
enum, trace or remove, because a new verdict would change what
remediation means. The kind names are open, because each is a column in
someone's sheet or a field in someone's LIMS.

`--subject`, `--donor` and the engine's `--samplesheet` are gone.
`--samplesheet` now exists only where an nf-core kind declared it, and a
kind that can find its inputs from the run declares nothing. The gate
asks a kind for its values instead of reading a sheet itself.

## Consequences

The engine's whole vocabulary for what can go wrong is one sentence: a
kind resolves to entry nodes and is traced or removed. The words
inside it belong to providers. Three of the six shipped providers declare
no kinds and are complete.

Two hooks remain where the engine still holds a judgement that belongs to
a provider: assigning a contribution class from evidence alone, and the
shipped policy table being the default. Both are the same shape as this
decision and are the next two.
