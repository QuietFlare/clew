# ADR 0008: A bundle verifies against something it does not control

Status: accepted

## Context

`clew evidence verify` re-chained the bundled event log and checked that
the last entry matched the head the manifest recorded. The first entry
was checked against its own `prev_hash`, which the bundle itself
supplied. A chain that began from an invented hash verified. The manifest
did not record where the window started, so a full log and a slice of one
looked alike, and `--previous` recorded the earlier bundle's hash without
its log head, so nothing tied one bundle's start to another's end.

Replay had the same shape of gap. It re-derived each item's action and
rule from the bundled facts and the bundled policy, and stopped there.
The candidate set on an undetermined item, and the counts the dashboard
and the MCP summary read, were copied from the plan and never recomputed.
A plan with a fact outside its domain, `terminal: null`, replayed to a
settled verdict because only storage could be unverified.

## Decision

Every check in `verify` compares the bundle against a value the bundle's
author could not choose. A chain that starts at the beginning of the log
must start from the log's genesis hash. A chain that starts later must
start from the log head the previous bundle recorded, and that bundle's
head is written into this manifest at build time. A manifest that claims
a log head and ships no events fails.

Replay recomputes everything a reader can act on: the action, the rule,
the candidate set of an undetermined item, and every count. A fact
outside its dimension's domain is an error, not a wildcard, and an
unverified value on any dimension withholds the verdict the way an
unchecked storage state already did.

## Consequences

A bundle is only as strong as its anchor, so building one without a log
still verifies but says it anchors to nothing. Bundles built before this
record carry no `since` and are read as full logs. The manifest grows by
two fields. A directory that already holds files is refused as a bundle
destination, because sealing whatever happens to be there is sealing an
unknown.
