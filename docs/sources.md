# Lineage sources

Clew computes over a lineage graph. Every extractor emits the same JSON, so
everything downstream is identical whichever engine ran the work.

## The line: content digests

Two files with the same name and size are evidence of the same bytes, not
proof. Engine checksums are computed over path and time and change on
copy. Clew therefore takes content digests from the engine as the first
source of identity, and reads files only where the engine recorded none.

That draws a line through the sources.

| Tier | Sources | What the record supports |
|---|---|---|
| Digests on every output | Nextflow lineage store with `cache 'deep'`, Horus through horus-lineage | Impact, reclaim and stitch, straight from the record |
| Digests on inputs only | Snakemake, Nextflow lineage store in standard mode | Impact. Reclaim and stitch after `clew digest` |
| No digests | Cromwell, DNAnexus, Latch, RO-Crate, work symlinks | Impact. Reclaim and stitch after `clew digest` |

Digests travel in the graph as `digest` fields, `<algorithm>:<value>`, on
output details, on edges, and on published files. Only equal strings
match, so a Nextflow deep-mode hash compares with another deep-mode hash
and never with a SHA-256. `clew reclaim` and `clew stitch` compare digests
and nothing else. A graph without them answers impact and is kept whole
by reclaim, with the reason on every directory.

For runs recorded without digests, `clew digest` reads each file once and
writes SHA-256 into the graph:

```bash
clew digest --graph graph.json --work-root work/ --results results/
```

Outputs under the work root fill their `digest`, and files under the
published tree land in a `published` map the graph carries from then on.

The engine's record is the record. Every question also takes `--runs`,
pointing at a `.lineage` store, a horus-lineage root, or a directory of
extracted graphs for engines that keep no store, and reads the run it
needs when it needs it, so no graph file has to be kept:

```bash
clew digest  --runs .lineage --run wise_hoover --work-root work/ --results results/
clew reclaim --runs .lineage --run wise_hoover --work-root work/ --results results/
clew drift   --runs .lineage --before goofy_nightingale --after wise_hoover
```

With `--runs`, `clew digest` writes what it computed to a sidecar under
`<runs>/.clew/`, one small file per run holding only the digests the
engine could not, and every later load merges it back. Nothing the engine
already says is copied.
DNAnexus exposes a file MD5 through its API that the extractor does not
read yet. Latch is unconfirmed. None of this replaces the engine hashing
at write time, which is the only cheap moment to do it.

## Nextflow native lineage (preferred)

Nextflow records lineage when you enable it in the
configuration, as the
[Nextflow docs](https://www.nextflow.io/docs/latest/data-lineage.html)
describe:

```groovy
lineage {
    enabled = true
}
process.cache = 'deep'
```

Nextflow then writes every task, output file and link into a `.lineage`
store with content-addressed `lid://` identifiers. The second line makes
the checksums it records content hashes rather than path and time, which
is what puts a store in the top tier above. Without it the store still
extracts and impact still works, but reclaim and stitch need `clew digest`
to run first. Clew has no opinion on where either setting lives. It reads
the store the engine writes.

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

## Cromwell

Cromwell runs WDL, on its own or behind Terra. Its workflow metadata
lists, for every call, the inputs it ran with and the outputs it produced
as paths, so an input that is another call's output is an edge, and a
path no call produced came from outside.

```bash
clew extract-cromwell --metadata metadata.json --json-out graph.json
```

The file is what `cromwell run -m metadata.json` writes. From a server,
fetch it yourself with subworkflows expanded, or let Clew do it:

```bash
clew extract-cromwell --server http://localhost:8000 --workflow <id> --json-out graph.json
```

`--token` sends a bearer token for a server behind auth. The extractor
only reads. A scattered call becomes one node per shard, named
`workflow.task/shard-N`. A subworkflow call is a wrapper that runs
nothing, so its children become the nodes and the wrapper disappears. If
the metadata came without `expandSubWorkflows=true` the wrapper is kept as
a node and marked, so the summary can say how much of the run is hidden.

Each call records its place under the workflow root, `call-x`,
`call-x/shard-N` or `call-x/shard-N/attempt-N`, with a subworkflow's calls
nested below the call that ran it. The task's own files are in `execution/`
under that, which is the directory `--work-root` questions look at; the
root is `cromwell-executions/<workflow>/<id>`.

The container is the image digest Cromwell resolved, or the declared image
when it did not. Cromwell hashes inputs for call caching and never hashes
outputs, so there are no content digests, and a String input containing a
slash reads as a file, which errs towards reporting an edge. Optional task
fields: `duration_s`, and `cached` on a call-cache hit.

Verified on Cromwell 92 with a scatter and an imported subworkflow, on the
local backend. Terra and the cloud backends record the same metadata with
`gs://` paths, which join the same way, and a first run there is welcome.

## Snakemake

Snakemake remembers, for every output file, the rule, inputs, command,
parameters and software environment that produced it, because that is how
it decides what to rerun. That memory is lineage. Nothing changes in the
workflow.

```bash
clew extract-snakemake --workdir /path/to/workflow --json-out graph.json
```

Both persistence backends are read: the JSON files under
`.snakemake/metadata/`, and the SQLite `metadata.db` written with
`--persistence-backend db`. A database shared by several workflows holds
one namespace per workflow, and `--namespace` picks one when the extractor
finds more than one.

Snakemake records a sha256 for every input file it consumed, so each edge
carries the digest of the file the consumer read. Only inputs are hashed,
so a digest reaches the graph for a file that some downstream job
consumed. Directory outputs, piped inputs, and files above Snakemake's
checksum size limit carry none. The container is the image the rule ran
in, or `conda@<hash>` for a conda environment. The script is the resolved
shell command.

An output's digest is known from the jobs that read it. The extractor
copies each consumer's recorded checksum onto the producer's output, so
`drift` and `stitch` see it. A final output nobody consumed carries none,
and an output whose consumers recorded different checksums is left
undigested, since the store then describes two runs at once.

Every job runs in the workflow directory. There is no per-task directory,
so tasks carry no `workpath` and `--work-root` leaves their storage
unverified. Reading the shared directory as each task's own would let one
redundant output make the whole workflow deletable.

`clew impact --pipeline snakemake` attributes a job to a sample when the
id appears in the output path that names the job, as a whole path
component or bounded by `-`, `_` or `.`, so `sample_1` does not match
`sample_10.fq`. The samplesheet's `sample` column lists the ids.

The store is a cache, not a history. Every run overwrites the records of
the outputs it rebuilt and leaves the rest, so after a partial rerun the
store describes a mix of runs, the same trap as the Nextflow work
directory. Extract after a run, and read the timestamps when two runs
might have been mixed.

Verified on Snakemake 9.26 with wildcards, multi-output rules and
directory outputs, on both backends.

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

| | lineage store | Horus | Cromwell | Snakemake | RO-Crate | work/ symlinks |
|---|---|---|---|---|---|---|
| tasks and edges | yes | yes | yes | yes | yes | yes, minus forwarded files |
| external inputs | yes | yes | yes | yes | yes | yes |
| script and container image | yes | yes | yes | yes | no | yes |
| output sizes | yes | yes | no | no | no | yes |
| content digests | every output with `cache 'deep'`, else external inputs only | every artifact | no | every consumed input | no | no |
| storage checkable | `--work-root` at `work/` and `--results` | `--work-root` at the run directory and `--results` | `--work-root` at the workflow root, `cromwell-executions/<workflow>/<id>`; each call's `execution/` directory is checked; no output sizes, so the published tree cannot be | no; jobs share one directory, so storage stays unverified | published copies only | `--work-root` at `work/` and `--results` |
| best verdict for a shared, surviving artifact | REGENERATE | REGENERATE | REGENERATE | REGENERATE | QUARANTINE | REGENERATE |

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
clew stitch --graph rna=rnaseq_run.json --graph da=de_run.json --out graph_chain.json
```

Give each run a label. The join is by content digest: an external input
of one run whose digest equals an output digest of another. Paths, hosts
and engines do not matter, so a Snakemake run that consumed a Nextflow
run's published file joins as readily as two Nextflow runs. Graphs whose
outputs or external inputs carry no digest report zero bridges, with a
count per graph of what does, and `clew digest` fills the gap for runs
the engine recorded without them.
