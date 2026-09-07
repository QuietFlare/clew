# ADR 0002: The engine knows no field and no workflow engine

Status: accepted

## Context

Clew started on nf-core genomics runs, where the natural words are
sample, donor, consent. A traversal engine that speaks those words works
for one field and has to be rewritten for the next. The same holds for
workflow engines: code that knows what a Nextflow work directory looks
like cannot read a Horus run.

## Decision

`graph/` and `ledger/` contain no domain and no engine vocabulary. A
domain adapter under `domains/` translates human facts, which subject
owns which task, into graph terms before calling in. An extractor under
`extract/` translates an engine's record into the one graph JSON. Both
rules are tests: a grep for forbidden words, and an import-direction
check that lets each package reach only the layers below it.

## Consequences

Adding a field means adding a directory, not editing the engine. Adding
an engine means adding one extractor. Some things that feel like engine
concerns, contribution classes for instance, live in the engine anyway,
because it cannot traverse without them; the domain owns only the mapping
from its events onto them.
