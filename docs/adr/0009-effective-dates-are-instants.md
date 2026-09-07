# ADR 0009: An effective date is an instant, and the gate has a now

Status: accepted

## Context

The event log records two clocks per fact: when it became true and when
we learned it. The first, `effective_from`, was free text. Facts written
by the CLI carried a full timestamp with an offset; facts in the docs and
tests carried a bare date. The gate ordered facts and applied `--as-of`
by comparing those strings. A date-only `--as-of` excluded a same-day
fact that carried a time. Two facts with different offsets ordered by
their digits rather than by when they happened.

Without `--as-of` the gate skipped the filter and let every fact decide,
under a report line that said "as of now". A reinstatement dated next
year cleared a withdrawal today.

## Decision

`effective_from` is an ISO 8601 instant. The log refuses a value it
cannot parse. A bare date means midnight UTC. Ordering and `--as-of`
compare instants in UTC, with log order as the only tiebreak, since seq
is the one clock nobody can backdate.

The gate always has an as-of. When the caller gives none it is the
current time, and the value used is written into the gate result and the
bundle, so a later replay asks the same question of the same moment.

## Consequences

A fact dated in the future waits until its date. Historical gate results
stay reproducible because the moment they were asked about travels with
them. Facts already in a log with an unparseable `effective_from` will
fail verification of the gate that reads them, which is the correct
outcome: a fact whose date cannot be read cannot decide anything.
