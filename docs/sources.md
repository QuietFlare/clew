# Lineage sources

Clew computes over a lineage graph. Every extractor emits the same JSON, so
everything downstream is identical whichever engine ran the work. Four
extractors ship today.

## Nextflow native lineage (preferred)

Nextflow records lineage when you enable it in the
configuration, as the
[Nextflow docs](https://www.nextflow.io/docs/latest/data-lineage.html)
describe:

```groovy
lineage {
    enabled = true
}
```

Nextflow then writes every task, output file and link into a `.lineage`
store with content-addressed `lid://` identifiers. Clew has no opinion on
where that setting lives. It reads the store the engine writes.

```bash
clew extract-store --store /path/to/.lineage --list-runs
```

```bash
clew extract-store --store /path/to/.lineage --run <run-name> --json-out graph.json
```

The engine is the best witness of what it ran. Inputs are typed, external
files carry checksums, and every task names its run, so one store shared
across many runs is safe by construction. Seqera Platform users already
have this store. Platform displays it, and Clew reads
the same files.

## Horus

The [horus-lineage](https://github.com/QuietFlare/horus-lineage) plugin
records one JSON record per task and one per run, with a content digest on
every input and output. Horus can run each task on a different machine, so
paths alone cannot join a run back together. Digests can, and that is what
this extractor joins on.

```bash
clew extract-horus --run-dir ~/.horus-lineage/<run-id>/ --json-out graph.json
```

Skipped tasks are recorded with their digests, so a cached run gives the
same graph as a fresh one. A record that says its digests were disabled or
partial still appears as a node, and its missing edges read as external
rather than being dropped.

## DNAnexus

DNAnexus records lineage on the platform. A job describe lists inputs and
outputs as file IDs, a file describe names the job that created it, and
file IDs are immutable and survive cloning between projects. Edges join
on file ID, so two analyses stitch with no path matching, even across
projects.

```bash
clew extract-dnanexus --analysis analysis-xxxx --json-out graph.json
```

The token comes from `DX_SECURITY_CONTEXT`, which `dx login` sets, or from
`--token`. The extractor only lists and describes. It never launches,
downloads or writes. A VIEW-level share of the project is enough. Saved
describe output works too, with `--records DIR` pointing at `jobs.json` and
`files.json`, which is how the tests run.

Two optional task fields carry what DNAnexus knows and Nextflow does not:
`price`, when the caller has billing access, and `duration_s`.

Not yet verified against a live analysis. The record shapes come from the
DNAnexus API documentation. Nextflow pipelines on DNAnexus run as a head
job plus one subjob per process, and whether those subjobs expose their
files as platform file IDs is one of the things a first real run will show.

## Latch

Latch keeps one execution graph node per task, with status, timings, cost
and the Flyte literal maps it ran with. Files in those maps are named by
`latch://` path, so edges join on path, and two executions stitch at a
shared path like two Nextflow runs do. The workflow's commit hash and
image hash identify the code and the environment.

```bash
clew extract-latch --execution <id> --json-out graph.json
```

The token is the one `latch login` stores, or `--token`. The extractor
reads one execution and downloads each task's inputs and outputs record.
It never launches or writes. Saved records work too, with `--records DIR`
holding `execution.json` and one literals file per node, which is how the
tests run.

Optional task fields: `price` and `duration_s`.

Not yet verified against a live execution. The schema comes from
introspecting the API the Latch SDK uses, which is not a published
contract, so the extractor pins the fields it reads and fails loudly if
they change.

## Workflow Run RO-Crate

The [nf-prov](https://github.com/nextflow-io/nf-prov) plugin writes a
Workflow Run RO-Crate. Labs that publish crates for journals or archives
already have lineage on disk.

```bash
clew extract-crate --crate ro-crate-metadata.json --json-out graph.json
```

A crate records what ran, not how to run it again. There is no script, no
work directory and no container image, because the crate names the module
rather than the image. So tasks from a crate classify as `IRREDUCIBLE`, and
their storage reads `DESTROYED` unless published copies are mapped. That is
fail-closed by design. Prefer the lineage store when both exist.

Validated against a real nf-prov crate: on the same sarek run, the crate and
the lineage store produce identical graphs and identical impact numbers for
every trigger.

## The work directory (fallback)

For runs that already happened without lineage enabled. With the default
stage-in mode on local and HPC executors, Nextflow stages inputs as
symlinks, and those symlinks record the whole history of the run. No
pipeline change, any Nextflow version:

```bash
clew extract-work --jsonl /path/to/weblog/<run-id>.jsonl --work /path/to/work --json-out graph.json
```

Do this during or right after the run. `nextflow clean` removes the
symlinks, and lineage that was never captured cannot be reconstructed.

Two limits. The trail only exists where inputs really are staged as
symlinks, which is `stageInMode 'symlink'` or `'rellink'`. Runs staged by
copy or hard link, including cloud executors reading from object storage,
leave nothing to read, and the extractor refuses with an error rather than
returning an empty graph that would report every task as clean. And when a
task re-emits an input unchanged, Nextflow stages that file for the next
task by pointing at the original, so on disk the hop does not exist and the
consumer reads as externally fed. The lineage store records channel lineage
instead of filesystem layout, so it keeps that edge.

On the same sarek pipeline the store and symlink extractors produce
identical impact numbers, and the test suite enforces that equivalence.

## What each source can prove

All sources yield the same graph shape. They differ in how much evidence
they carry, and evidence is what verdicts are made of.

| | lineage store | Horus | RO-Crate | work/ symlinks |
|---|---|---|---|---|
| tasks and edges | yes | yes | yes | yes, minus forwarded files |
| external inputs | yes | yes | yes | yes |
| script and container image | yes | yes | no | yes |
| output sizes | yes | yes | no | yes |
| content digests | external inputs | every artifact | no | no |
| storage checkable | `--work-root` | `--work-root` | published copies only | `--work-root` |
| best verdict for a shared, surviving artifact | REGENERATE | REGENERATE | QUARANTINE | REGENERATE |

The last row is the practical difference. A crate carries no re-execution
evidence, so every crate task fails closed to `IRREDUCIBLE`. The blast
radius is exact and published artifacts still resolve to `NOTIFY_ONLY`, but
Clew will never recommend re-running from a crate, only blocking. The answer
stays correct and becomes more expensive to act on. The richer the record,
the cheaper the remediation.

## Joining runs

One run publishes a file, a second run consumes it. Clew joins the two
graphs at that file:

```bash
clew stitch --graph rna=rnaseq_run.json --results rna=/path/to/rnaseq/results \
    --graph da=de_run.json --out graph_chain.json
```

Give each run a label and point `--results` at the published output of the
run that produced the shared file. The join is by path, so the paths in the
graph must be the paths on this machine. The shipped `graph_rna.json` and
`graph_da.json` cannot be re-stitched for that reason: their paths were
anonymised before publication, and `clew stitch` reports zero bridges rather
than inventing one. Horus graphs join by digest instead, which works across
machines.
