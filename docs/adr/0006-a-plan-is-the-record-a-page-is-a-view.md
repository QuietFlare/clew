# ADR 0006: A plan is the record, a page is a view

Status: accepted

## Context

Every question ends in something a person reads and, often, something an
auditor later checks. A rendered page that carries a timestamp, a script,
or a fetch from the network is a different document each time it is
made, and it cannot be checked against the plan it came from.

## Decision

The output of a question is a plan: JSON, clock-free, byte-identical for
the same inputs, carrying its policy version and every caveat. A page is
rendered from a plan and adds nothing: one file, no scripts, no network,
no generation time. What the question could not settle is shown in the
same weight as what it could, never folded into small text below the
fold. Deletion, where a question can delete, writes a receipt line per
directory before the directory goes, so the record precedes the act.

## Consequences

Re-running a question on the same inputs proves determinism by diff. A
page can be regenerated from a stored plan years later and match. The
receipt, not the page, is what an assessor gets for reclaim. Every new
question inherits the shape: a plan first, a page second, and no verdict
that only exists on screen.
