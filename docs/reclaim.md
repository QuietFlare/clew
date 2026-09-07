# Reclaim: which work directories can go, with the proof

Work directories are the largest cost line in most facilities, and nobody
deletes because nobody can say what a file feeds. Age-based cleanup cannot
say either. `clew reclaim` reads the graph and proposes a task directory
only when its bytes are provably redundant. Anything it cannot prove stays,
with the reason.

```bash
clew reclaim --graph graph.json --work-root work/ --results results/
```

Or read the run straight from the engine's record, with no graph file:

```bash
clew reclaim --runs .lineage --run wise_hoover --work-root work/ --results results/
```

Nothing is touched. The plan lists every task directory under one verdict.
`--json` writes it as data and `--html` as one self-contained page, with
the kept directories and their reasons shown in the same weight as the
reclaimable ones.

## Verdicts

| Verdict | Proof | Proposed by default |
|---|---|---|
| `REDUNDANT` | Every output has a published copy with the same content digest, still present under `--results` | yes |
| `SUPERSEDED` | The extractor marked the task re-run by a later run in the resume chain, and nothing consumed its outputs | yes |
| `FAILED` | The engine recorded a failure and nothing consumed its outputs | yes |
| `INTERMEDIATE` | Every output is consumed downstream, the script and container were recorded, and every input is still on disk or recomputable | only with `--intermediates` |
| `KEEP` | None of the above could be shown | no |
| `GONE` | The directory is not under `--work-root` | nothing to do |

The reason on each `KEEP` names what withheld it: an output with no
published copy, a published match that was ambiguous, a missing recipe, an
input that could not be found at its recorded path.

`INTERMEDIATE` is opt-in because it trades disk for compute. The bytes are
regenerable, but regenerating them means re-running the producer, and the
plan says so.

## What it rests on

Identity is by content digest and nothing else. An output is published
when a file under the results tree carries the same digest, as recorded
by the engine or by `clew digest`. Reclaim reads no file content itself.
With `--results` it checks that each recorded copy still exists and how
it relates to the work file: a hardlink is the same bytes by construction,
and a symlink into work is not a copy at all, since deleting the directory
would break it, so it withholds the directory. Without `--results` a
recorded copy cannot be confirmed to still exist, and nothing is proposed.
An output with no digest withholds its directory, and the reason says
whether to record digests in the engine or run `clew digest`. A recorded
copy that is no longer the size it was digested at withholds too, and
`--apply` re-hashes every copy a `REDUNDANT` verdict rests on before the
directory goes. When two outputs share a digest, the copy with the
output's own file name is the one named; only when none has it are all
copies with that digest listed.

Status words differ by engine: Cromwell says `Done`, Latch `SUCCEEDED`,
Horus `skipped` for a cache hit. Each extractor maps its engine's word to
`COMPLETED`, `FAILED`, `CACHED` or `UNKNOWN` and keeps the original as
`engine_status`. Only `FAILED` can be proposed; a word the mapping does
not know, such as a task still running, is kept with the reason.

Sizes are measured on disk, counting regular files with a single link.
Staged inputs are symlinks to their producers and are not counted twice,
and a file hard-linked elsewhere survives the removal, so it is not
counted either.

A graph without output sizes cannot match published copies, and the command
says so. Extract from a source that records them; [sources](sources.md) has
the table.

## Bookkeeping outputs

Every nf-core task writes a `versions.yml` that no task consumes as a file
and no publishDir copies one by one. Left alone it would withhold every
directory. The nf-core adapter declares it as bookkeeping, and `--ignore`
takes globs for other pipelines. The ignored names are printed in the plan
header so the reader can see what was set aside.

## Tasks that ran elsewhere

Horus runs tasks on targets. Cloud engines keep work in object storage. The
graph side of reclaim does not care, but the disk side needs a filesystem
it can see. Two things help today:

- `--target ID` restricts the plan to the tasks that ran on one target, so
  reclaim can be run on that machine with that machine's work root.
- The JSON plan carries `target` and `dir` per item, so another tool can
  act on it remotely.

A backend that checks and removes through the engine's own channel, rather
than through the local filesystem, is not built.

## Applying

```bash
clew reclaim --graph graph.json --work-root work/ --results results/ \
    --apply --receipt receipt.jsonl
```

Only verdicts proposed by default are removed, plus `INTERMEDIATE` when
`--intermediates` was given. One JSON line is written to the receipt before
each directory is removed, carrying the verdict, the reason, the bytes, the
published copies and the time. A directory outside `--work-root` is refused.
The receipt is the record of what left the disk and why, and it is what an
auditor gets.

Re-run the plan before applying. Verdicts hold for the graph and the disk
as they are at the moment of asking.

## Not built

Duplicates across runs, where two tasks produced the same bytes. The
digests are there now; the verdict is not. Outputs that are directories,
which neither the engine nor `clew digest` hashes, so they withhold their
directory. Remote backends, as above.
