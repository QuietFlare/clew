# ADR 0007: Extractors translate, core never interprets an engine string

Status: accepted

## Context

Two fields on a task were written by extractors in the engine's own words
and read by core as if they meant one thing. `status` carried COMPLETED
from Nextflow, DONE from Cromwell, SUCCEEDED from Latch and SKIPPED from a
Horus cache hit, and reclaim treated everything but COMPLETED as a failed
attempt it could delete. `workdir` carried an absolute path from whichever
machine ran the pipeline, and core located a task's files by joining the
last two components of it onto `--work-root`, which is how Nextflow lays
out `work/` and how nothing else does. On Snakemake every task resolved to
the workflow directory itself, so one redundant output made the whole
workflow deletable. On Cromwell, shards resolved nowhere.

Both are the same mistake: ADR 0002 kept engine vocabulary out of core, but
the graph contract still let an engine's raw strings through, and core
guessed what they meant.

## Decision

The graph contract owns two closed vocabularies, and every extractor
translates into them before core sees a task.

`status` is one of a fixed set that core defines. An extractor maps its
engine's states onto that set; a state it does not recognise maps to
unknown. Reclaim proposes a directory as a failed attempt only for an
explicit failure state. Anything else is kept.

`workpath` is where a task's files live, relative to a root the caller
names. Nextflow writes `xx/fullhash`. Cromwell writes the call root under
the workflow root, shard and attempt included. Horus writes its own
per-task directory. Snakemake writes nothing, because Snakemake has no
per-task directory. Core joins `workpath` onto `--work-root` and never
parses `workdir`. A task with no `workpath` has unverified storage, and so
does every task when several resolve to one directory or when none
resolves at all, since both mean the caller pointed at the wrong place.

## Consequences

Adding an engine means saying, in its extractor, what its statuses mean
and where its tasks live. Core has no engine-shaped branch to grow.
Storage checks, `digest`, `reclaim` and `drift` become real on Cromwell
and stop being wrong on Snakemake. Old graphs without `workpath` still
load; core falls back to the Nextflow rule for them and says so. ADR 0001
holds at the boundary: an engine string core does not understand can only
make an answer more cautious, never cleaner.
