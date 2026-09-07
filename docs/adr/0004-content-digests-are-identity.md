# ADR 0004: Content digests are the only identity

Status: accepted

## Context

Reclaim has to say a published copy holds the same bytes as a work copy.
Stitch has to say one run's output became another's input. Drift has to
say an output changed. Name and size are evidence, not proof: a re-run
can write a same-size file with different content. Engine checksums are
usually computed over path and time and change on copy. Reading files to
compare them costs a full read of every large output, each time anyone
asks.

## Decision

A file is identified by a content digest, `<algorithm>:<value>`, carried
on outputs, on edges, and on published files. Only equal strings match,
so a hash from one algorithm never compares with another. Reclaim, stitch
and drift compare digests and nothing else. Where an output has none, the
verdict is unknown, per ADR 0001.

Digests come from the engine first, since hashing at write time on the
node that wrote the file is the only cheap moment. `clew digest` is the
fallback for runs recorded without them: it reads each file once and
records SHA-256.

## Consequences

The line through the sources is explicit: engines that hash content at
write time answer everything from their record; the rest need one pass
of `clew digest` per run. Clew reads no file content in a question. The
cost moves to once, at write time or at the first digest pass, and every
answer afterwards is a string comparison.
