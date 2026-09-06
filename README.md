# Clew

[![PyPI](https://img.shields.io/pypi/v/clew-lineage.svg)](https://pypi.org/project/clew-lineage/)
[![Python](https://img.shields.io/pypi/pyversions/clew-lineage.svg)](https://pypi.org/project/clew-lineage/)
[![Tests](https://github.com/QuietFlare/clew/actions/workflows/ci.yml/badge.svg)](https://github.com/QuietFlare/clew/actions/workflows/ci.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)

Clew answers questions about workflow runs that the engine cannot: what a
bad input reached, what can safely be deleted, and what one run took from
another. It reads the record the engine already writes, for Nextflow,
Snakemake, Cromwell, Horus, DNAnexus and Latch, and changes nothing in
your pipeline.

The examples are from genomics because that is where it was first used.
The graph underneath is neutral: tasks that read files and write files,
whatever the field.

A clew is the ball of thread Ariadne gave Theseus. You follow it back out.

## Install

```bash
pip install clew-lineage
```

```bash
clew demo
```

Python 3.9 or later, no dependencies. The demo runs over a real
nf-core/sarek run that ships with the package.

## Three questions

**Something upstream went bad.** A reference update, a broken container, a
withdrawn sample.

```bash
clew impact --graph graph.json --container gatk4
```

Every affected task gets a verdict, re-run, quarantine, delete or disclose,
with the derivation chain as evidence. Nothing unknown is reported as clean.

**The disk is full and nothing is wrong.**

```bash
clew reclaim --graph graph.json --work-root work/ --results results/
```

Proposes only the directories the graph proves redundant, and deletes
nothing without `--apply` and a receipt.

**One run consumed another's output.**

```bash
clew stitch --graph a=a.json --graph b=b.json --out chain.json
```

Joins runs by content digest, so a question follows a change across
launches, machines and engines.

Add `--html` to `impact` or `reclaim` for a one-page report.

![An impact report: how much of the run a bad container reaches, and what to do about each task it touches](docs/impact.png)

## Your runs

One command per engine turns a run into a graph.

| Engine | Command |
|---|---|
| Nextflow, including Seqera Platform | `clew extract-store --store .lineage --run <run> --json-out graph.json` |
| Snakemake | `clew extract-snakemake --workdir . --json-out graph.json` |
| Cromwell and WDL, including Terra | `clew extract-cromwell --metadata metadata.json --json-out graph.json` |
| Horus, through [horus-lineage](https://github.com/QuietFlare/horus-lineage) | `clew extract-horus --run-dir <run> --json-out graph.json` |
| DNAnexus | `clew extract-dnanexus --analysis <id> --json-out graph.json` |
| Latch | `clew extract-latch --execution <id> --json-out graph.json` |

Content digests make every answer exact. Horus records them, and Nextflow
does with `cache 'deep'`. For any other run, `clew digest` reads each file
once and fills them in. [Sources](docs/sources.md) has what each engine
records and what that limits.

## Every answer can be checked

Clew is built for the day someone else has to verify what it said. Storage
is checked, never assumed. Facts go in an append-only log. The rules are
versioned data, so an old plan replays under the rules that produced it.
An evidence bundle re-derives every verdict offline, with no database and
no credentials. A gate can block a run before it starts.

[Storage](docs/storage.md), [event log](docs/event-log.md),
[policy](docs/policy.md), [evidence](docs/evidence.md), [gate](docs/gate.md),
[auditor surfaces](docs/auditors.md), [architecture](docs/architecture.md).

## Status

Verified on real Nextflow, Snakemake, Cromwell and Horus runs. DNAnexus and
Latch are built from the platform APIs and await their first live runs.
[CHANGELOG.md](CHANGELOG.md) lists what changed in each release.

## Contributing

Issues and pull requests are welcome, especially from people who run
workflows for a living and can say where the model is wrong. See
[CONTRIBUTING.md](CONTRIBUTING.md). No agreement to sign.

## License

[AGPL-3.0](LICENSE).
