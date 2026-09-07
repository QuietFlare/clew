# ADR 0005: The engine's record is the record

Status: accepted

## Context

Answering across runs needs every run. The obvious design is a Clew
folder holding one graph per run, filled at run end. But Nextflow's
lineage store and horus-lineage already are append-only records of every
run on a machine. A second copy of the same facts is the copy that drifts
out of sync and gets deleted first.

## Decision

Clew keeps no record of a run. `--runs` points at the engine's own store,
and Clew extracts the run it needs when it needs it. The one thing Clew
writes is a sidecar per run beside the store, `<runs>/.clew/`, holding
only what the engine could not: the content digests `clew digest`
computed, and the digests of the published tree. A sidecar never
contains a fact the engine already states.

For engines that keep no store, DNAnexus, Latch, Cromwell without a
server, the extracted graph is the only record, and a directory of those
graphs is accepted in the same place.

## Consequences

Nothing has to be remembered at run end for Nextflow or Horus. The
sidecar disappears the day the engine records digests itself, which is
the outcome to aim for. Answers across runs cost an extraction per run
at question time rather than a store maintained in the background.
