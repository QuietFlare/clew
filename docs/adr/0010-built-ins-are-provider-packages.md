# ADR 0010: The built-in adapters and extractors are provider packages

Status: superseded by ADR 0012 for packaging; the contract boundary stands

## Context

Clew publishes two contracts, `Adapter` and `Extractor`, and finds
providers by entry point. A third party ships a package that declares
its modules. Clew's own adapters and extractors were declared the same
way but lived in `clew/domains/` and `clew/extract/`, folders inside the
engine. Two shapes for one thing, and the engine still reached into
them in four places a third party could not: results-tree helpers in
`domains/nfcore.py`, a bookkeeping default there too, `extract/runs.py`
recognising Nextflow and Horus records by name, and `demo.py` importing
`sarek`.

## Decision

`clew` is a namespace package. The engine is one distribution and each
provider is another, installing into `clew.provider.<name>`:

```
clew/                     clew-lineage: graph, ledger, contracts, questions, views,
                          and extract/ for the command, runs, stitch and digest
providers/
  clew-nextflow/          clew/provider/nextflow/: store, work, rocrate,
                          NextflowAdapter, sarek, rnaseq, viralrecon
  clew-snakemake/         extractor and domain
  clew-cromwell/          extractor
  clew-horus/             extractor
  clew-dnanexus/          extractor
  clew-latch/             extractor
```

Each has its own `pyproject.toml`, entry points and README, depends on
`clew-lineage`, and is built as a third party would build one:
`from clew.contracts import Adapter, Extractor`, and for an nf-core
adapter `from clew.provider.nextflow import NextflowAdapter`. The engine
depends on none of them and declares extras instead:

```bash
pip install "clew-lineage[nextflow]"
pip install "clew-lineage[all]"
```

The four leaks closed by moving engine-neutral code into the engine and
engine-specific code behind the contract:

| Was | Now |
|---|---|
| `index_results`, `published_copies` in nfcore | `clew/graph/results.py` |
| `BOOKKEEPING` default in nfcore | `clew/graph/results.py` |
| CSV reader and tag parser in nfcore | `clew/graph/subjects.py`, used by both Nextflow and Snakemake adapters |
| `runs.py` knows Nextflow and Horus | `Extractor.records(path)` and `load(root, run_id)`, optional; `Runs` asks every installed extractor |
| `demo.py` imports sarek | asks `discover(Adapter)` for `sarek` and names the package to install if absent |

A test holds providers to the same line: a provider may import
`clew.graph`, `clew.contracts` and `clew.extract`, never the questions
and never another provider.

## Consequences

The engine can be released without the providers, and a provider
without the engine. The check that a provider works from outside the
engine is no longer a thought experiment: the built-ins are that check.

A checkout needs all seven distributions installed editable: `make dev`.
The `PYTHON` variable matters on a machine with more than one Python.
PyPI grows from one distribution to seven.

Each provider's tests live in its own `tests/` with its own fixtures, and
`make test` runs the engine's suite then each provider's. Where a test
exercised an engine feature over one engine's record, `--runs` on a
`.lineage` store or a Horus root, it moved with that record. The engine's
own tests that use the shipped sarek run need `clew-nextflow` installed,
which `make dev` guarantees.
