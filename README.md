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

## The headline, first

One withdrawal, two pipelines. A yeast sample was withdrawn; its rnaseq run
had published a count matrix; a separate differentialabundance run consumed
it. Clew stitches the two runs' graphs at that published file and answers
across the boundary:

```
TRIGGER: withdrawal of SRR10441036_cox4d
AFFECTED: 57 of 183 tasks        (46 in the rnaseq run, 11 in the DE run)

da:29/ae3d99  DESEQ2_DIFFERENTIAL   REGENERABLE  shared
    via rna:f2/cefd0f[STAR_ALIGN] -> rna:0c/8143cf[SALMON_QUANT]
     -> rna:c9/9a30ba[CUSTOM_TX2GENE] -> rna:8e/b5be55[TXIMETA_TXIMPORT]
     -> da:e8/91c345[VALIDATOR] -> da:29/ae3d99[DESEQ2_DIFFERENTIAL]
```

The evidence chain starts at the sample's alignment in one Nextflow launch
and ends inside another, crossing at a checkable published path. Engine
lineage sees each run in isolation; this graph is the part nobody else has.

```bash
python3 stitch_graphs.py --graph rna=graph_rna.json --results rna=<rnaseq results dir> \
    --graph da=graph_da.json --out graph_chain.json
```

```bash
python3 blast.py --pipeline rnaseq --graph graph_chain.json \
    --samplesheet samplesheets/rnaseq_yeast.csv --donor SRR10441036_cox4d
```

## See it in two minutes

No dependencies beyond Python 3.11. The repo ships a real graph extracted
from an nf-core/sarek run with 5 synthetic donors, 81 tasks, 344 edges.

```bash
python3 demo.py
```

The demo answers three questions, one per audience, all from the same engine.

## Use it on your own run

Clew computes over a lineage graph. Two extractors produce one today, and
they emit the same JSON, so everything downstream is identical either way.

**Preferred: Nextflow's native data lineage** (25.04+). Enable it in your
Nextflow configuration before the run, exactly as the
[Nextflow docs](https://www.nextflow.io/docs/latest/data-lineage.html)
describe:

```groovy
lineage {
    enabled = true
}
```

Nextflow then records every task, output file, and link into a `.lineage`
store with content-addressed `lid://` identifiers. Clew has no opinion on
where you put that setting; it only reads the store the engine writes.
Build the graph from it:

```bash
python3 extract_from_lineage_store.py --store /path/to/.lineage --list-runs
```

```bash
python3 extract_from_lineage_store.py --store /path/to/.lineage \
    --run <run-name> --json-out graph.json
```

The engine is the best witness of what it ran: inputs are typed, external
files carry checksums, and every task names its run, so sharing one store
across many runs is safe by construction.

**Also supported: Workflow Run RO-Crate**, as written by the
[nf-prov](https://github.com/nextflow-io/nf-prov) plugin. Labs that already
publish crates for journals or archives have lineage on disk without
knowing it:

```bash
python3 extract_from_rocrate.py --crate ro-crate-metadata.json --json-out graph.json
```

A crate records what ran, not how to re-run it: no script, no workdir. So
tasks from a crate classify as `IRREDUCIBLE` and their storage reads
`DESTROYED` unless published copies are mapped — fail-closed, by design.
Prefer the lineage store when both exist.

**Fallback: the `work/` symlink extractor**, for runs that already happened
without lineage enabled. Nextflow stages inputs as symlinks to save disk,
and those symlinks accidentally record the entire history of the run. No
pipeline modification, works on any Nextflow version:

```bash
python3 extract_lineage.py \
    --jsonl /path/to/weblog/<run-id>.jsonl \
    --work  /path/to/work \
    --json-out graph.json
```

Do this during or right after the run. `nextflow clean` removes the
symlinks, and lineage that was never captured cannot be reconstructed.

On the same sarek pipeline the store and symlink extractors produce
identical impact numbers, and the test suite enforces that equivalence.

Capture and computation stay separate on purpose. The engines are getting
good at recording what happened. Clew's job starts where they stop: whether a
contribution can be taken back out, and what must happen when it cannot.

## Anatomy of a trigger: selector × mode

Clew has no hardcoded scenarios. Every trigger is the combination of two
independent choices, and the familiar stories are just named cells in that
grid.

**The selector answers "where does the problem enter the graph?"** Three
ship today:

| Selector | Flag | Entry nodes |
|---|---|---|
| subject | `--donor X` | every task attributed to one sample or donor |
| container | `--container Y` | every task that ran in a matching container |
| external input | `--input Z` | every task that consumed that outside file |

**The mode answers "what kind of wrong is it?"** Two exist, and they are
not interchangeable:

- `remove` — the source must be taken out (a withdrawal). Ownership
  matters: an artifact existing only because of this subject has nothing
  left to serve, so it can be destroyed.
- `distrust` — the data is suspect but still wanted (contamination, a tool
  defect, a stale reference). Nothing is destroyed; the worst verdict is
  quarantine, because you will want these artifacts again once the cause
  is fixed.

The stories, mapped:

| Story | Selector | Mode |
|---|---|---|
| Consent withdrawal | subject | remove |
| Sample contamination, swap, QC failure | subject | distrust |
| Tool or container defect | container | distrust |
| Reference / annotation update | external input | distrust |
| Primer scheme correction | external input | distrust |
| Upstream dataset retraction | external input | remove — not yet supported: removal needs an owner, and computing what exists *only* because of one input needs multi-root traversal |

Defaults preserve the common cases (`--donor` implies remove, the others
imply distrust), and `--mode` overrides them: contamination is
`--donor X --mode distrust`.

**Will selectors be extended? Yes — that is the extension point.** A
selector is anything that can name a set of entry nodes; the engine only
ever sees the set. Obvious future selectors: by file checksum (one exact
artifact), by batch or time window (every task in the run of a bad
reagent lot), by facility, by edge kind (everything *calibrated against*
a control, not merely derived from it). Modes are the closed part: new
verdicts would change what remediation means, so a new mode is a design
event, not a plugin.

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

## Contributing

Issues and pull requests are welcome — especially from people who run
pipelines for a living and can say where the model is wrong. See
[CONTRIBUTING.md](CONTRIBUTING.md); a first pull request needs the CLA
agreement described there, an issue needs nothing at all.

## License

[AGPL-3.0](LICENSE).
