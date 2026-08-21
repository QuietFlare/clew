# Clew

Clew works out what has to happen downstream when something upstream goes bad.
A buggy tool, a contaminated sample, a reference update, a withdrawn consent.
It rebuilds the lineage of your Nextflow runs and tells you exactly what to
delete, re-run, or disclose, with a plan you can hand to an auditor.

A clew is the ball of thread Ariadne gave Theseus. You follow it back out.

## The problem

Something upstream is invalidated after the fact. It was already aligned,
recalibrated, aggregated into a cohort report, maybe cited in a paper.
Nobody can mechanically answer "what do I now have to delete, re-run, or
disclose?" In practice a person answers it with a spreadsheet, or it is not
answered at all.

Provenance tools record where data came from. None of them record whether a
contribution can be taken back out. That is the difference between a history
and a recall plan, and it is the gap Clew fills. Every derivation edge gets a
contribution class:

| Class | Meaning | Remediation |
|---|---|---|
| `SEPARABLE` | The contribution can be removed, the artifact survives | `PURGE` |
| `REGENERABLE` | Cannot be isolated, but the artifact can be recomputed from the remaining sources | `REGENERATE` |
| `IRREDUCIBLE` | Neither | `QUARANTINE` |

Anything unknown fails closed to `IRREDUCIBLE`. Telling someone their data is
clean when it is not is the one error that ends up in front of a regulator.

## See it in two minutes

No dependencies beyond Python 3.11. The repo ships a real graph extracted
from an nf-core/sarek run with 5 synthetic donors, 81 tasks, 344 edges.

```bash
python3 demo.py
```

The demo answers three questions, one per audience, all from the same engine.

## Use it on your own run

Rebuild file-level lineage from a Nextflow `work/` directory. No pipeline
modification, no plugin. Nextflow stages inputs as symlinks to save disk, and
those symlinks accidentally record the entire history of the run:

```bash
python3 extract_lineage.py \
    --jsonl /path/to/weblog/<run-id>.jsonl \
    --work  /path/to/work \
    --json-out graph.json
```

Do this during or right after the run. `nextflow clean` removes the symlinks,
and lineage that was never captured cannot be reconstructed.

## Where the lineage comes from

Clew computes over a lineage graph. It does not care who produced it, and
there are two ways to feed it today plus one planned:

1. **The `work/` symlink extractor** (above, ships now). Works retroactively
   on any run whose `work/` directory still exists, on any Nextflow version.
   This is the only option for runs that already happened.
2. **Nextflow's native data lineage** (25.04+). Enable it before the run and
   Nextflow records every task, output file, and link into a `.lineage`
   store with content-addressed `lid://` identifiers:

   ```bash
   nextflow run <pipeline> -c lineage.config
   ```

   The repo ships [lineage.config](lineage.config) for this. An adapter that
   reads the `.lineage` store directly is the planned primary ingest path
   going forward, since the engine itself is the best witness of what it ran.
3. **nf-prov output** (planned). Runs that emit Workflow Run RO-Crate via the
   nf-prov plugin carry the same edges in a standard format, and Clew should
   read that rather than invent its own.

Capture and computation stay separate on purpose. The engines are getting
good at recording what happened. Clew's job starts where they stop: whether a
contribution can be taken back out, and what must happen when it cannot.

## Three questions it answers

### Pipeline engineer: "We bumped the reference genome. What must be re-run?"

```bash
python3 blast.py --graph graph.json --samplesheet samplesheet.csv --input genome.fasta
```

On the sample run: genome.fasta was consumed directly by 41 tasks, and 72 of
81 tasks are calibrated against it. The other 9 are provably out of scope,
with the derivation chain printed as evidence for every claim.

### QA: "A defect was reported in a GATK4 container. What did it produce?"

```bash
python3 blast.py --graph graph.json --samplesheet samplesheet.csv --container gatk4
```

On the sample run: 16 tasks ran the container, 68 of 81 tasks are suspect.
Note that nothing is destroyed. A defect casts doubt, it does not remove a
source, so artifacts are rebuilt rather than deleted.

### Compliance: "A donor withdrew consent. What happens now?"

```bash
python3 blast.py --graph graph.json --samplesheet samplesheet.csv \
    --donor donor_003 --assertions assertions.json
```

On the sample run: 16 of 81 tasks are affected. The 15 that exist only
because of this donor are destroyed. The cohort report that also serves the
other donors, and was cited in a publication, resolves to `NOTIFY_ONLY`
instead: you cannot unpublish, so the answer there is disclosure, not
deletion. One traversal, two verdicts, which is the whole point.

Publication is an external assertion, not something Clew infers. The
assertions file records who claimed it and when:

```json
{
  "published": [
    {
      "task": "c9/023b13",
      "what": "cohort MultiQC report, cited as Supplementary Fig 1",
      "asserted_by": "name@example.org",
      "date": "2026-08-21",
      "reference": "doi:10.0000/example"
    }
  ]
}
```

## What Clew claims, and what it does not

Clew is a system of record, not an attester. It claims three things, all
checkable: the computation is deterministic, the result follows from the
inputs, and anyone can re-run it and get the same answer. It claims nothing
about whether your inputs were true or your policy was right. Your tags are
inputs, not Clew's claims.

Honest edges, reported rather than hidden:

- Traversal follows derivation and stops at influence. A published finding
  that informed a business decision is recorded as a terminal reference, not
  pretend-tracked.
- Uninstrumented systems are reported as unknown, never as clean.
- Clew proves the record was tombstoned and nothing later referenced it.
  It does not prove physical destruction. No cryptography reaches a freezer.

## Architecture

```
core/       traversal, contribution classes, remediation. Zero domain vocabulary.
domains/    the layer allowed to know about sarek, samplesheets, donors.
tests/      46 tests, stdlib unittest, no dependencies.
```

The boundary is enforced by a grep: `core/` must never mention a sample, a
donor, a consent, or a workflow engine. Adding a new domain (say, AI training
data with opt-out semantics) means adding a directory, not editing core.

```bash
python3 -m unittest discover -s tests
```

## Status

Working: lineage extraction from real runs, blast radius for three trigger
types, contribution classes with fail-closed defaults, remediation plans,
publication assertions, mixed verdicts from a single traversal.

Not built yet: append-only event log, policy versioning, signed evidence
bundles, CI gate. That is the roadmap, in that order.

## License

AGPL-3.0.
