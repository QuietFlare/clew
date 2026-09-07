# Drift: where two runs part ways, and why

Every version bump, every resumed run, every "it worked last month" is the
same question: did the workflow produce what it produced before, and if
not, where did it start to differ. `clew drift` answers it from two graphs.

```bash
clew drift --before a.json --after b.json
```

Or name two runs in the engine's record and skip the graph files:

```bash
clew drift --runs .lineage --before goofy_nightingale --after wise_hoover
```

Tasks are paired by name across the two runs and compared by the content
digests of their outputs. Both graphs need digests; [sources](sources.md)
says which engines record them and `clew digest` fills them in otherwise.

Two runs of one resume chain share a session, and a `.lineage` store
holds one graph per session, so `--runs` refuses to compare them: there
is one graph, not two.

## Pairing

One task per name on each side pairs by name. When a name repeats, as a
per-interval step does, the tasks on each side are told apart by the
digests of their inputs, and pair where those agree. What that cannot
settle is reported `UNVERIFIED` rather than paired by hash order, which
paired shards crosswise. A version a later run superseded is left out.

## Verdicts

| Verdict | Meaning |
|---|---|
| `DRIFTED` | Outputs differ and no upstream task did. This is where a chain diverges, and the reason names the cause. |
| `UNSETTLED` | Outputs differ and an upstream task could not be verified. The divergence may start here or there; a root can hide behind a missing digest, so this is not filed as downstream. |
| `DOWNSTREAM` | Outputs differ because an upstream task drifted. Explained, not a finding of its own. |
| `REPRODUCED` | Every output has the same digest in both runs. When an upstream task drifted and this one still reproduced, the reason says so. |
| `UNVERIFIED` | An output has no digest on one side, or same-named tasks could not be paired. Not compared, and never reported as reproduced. |
| `ADDED`, `REMOVED` | Present in one run only. A renamed process shows as one of each. |

## Causes

A root's cause is read from the record, in this order: an external input
whose digest changed, a different container, a different script. When all
three match and the outputs still differ, the reason says so. That is a
finding in itself: a tool that is not deterministic, or an input the
engine did not record.

## What it does not settle

Pairing is by task name, so it holds within one workflow and its versions,
not across two different workflows. Bookkeeping outputs such as
`versions.yml` are ignored by default, since they change with every tool
version whether or not the science did; `--ignore` takes other globs.
Drift says that outputs differ, not by how much. Comparators that measure
the difference for a data type are not built.
