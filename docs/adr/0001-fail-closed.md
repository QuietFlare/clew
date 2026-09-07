# ADR 0001: Unknown is never clean

Status: accepted

## Context

Every question Clew answers ends in someone acting: deleting, re-running,
disclosing, or leaving alone. The two ways to be wrong are not equal. An
answer that over-claims wastes compute. An answer that under-claims tells
someone their data is clean when it is not, and that is the error that
ends up in front of a regulator.

## Decision

Anything Clew cannot establish is reported as unknown, never as clean.
A contribution class it cannot read is `IRREDUCIBLE`. A storage state it
was not told where to check is `UNDETERMINED`. A directory whose outputs
carry no digest is `KEEP`. A task with no digest on one side of a drift is
`UNVERIFIED`. Each carries the reason, so the reader knows what checking
would settle.

## Consequences

Plans are longer and more cautious than a guess would be. A run recorded
with little evidence gets correct answers that are expensive to act on;
the richer the record, the cheaper the remediation. Every new question
inherits the rule: it must have an unknown verdict, and that verdict must
never be the empty case.
