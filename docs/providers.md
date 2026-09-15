# Providers

Clew ships the engine and two contracts. A provider is a package that
fills one or both for its own site: a **domain** for a pipeline Clew has
never seen, an **extractor** for an engine Clew cannot read. Both are
found by name and need no change inside Clew.

```python
from clew.contracts import Adapter, Extractor
```

## A domain

A domain is what a site knows about one pipeline. Clew's core sees a
graph of task ids and nothing else. The domain is where a task named
`BWAMEM1_MEM (SPC-0412)` becomes "specimen SPC-0412 enters here".

Nothing is required. A domain declares the trigger kinds it understands,
in its own words, and each kind knows how to resolve a value and whether
losing that value is traced or removed. A domain that declares none
still answers the engine's kinds: `container`, `script`, `process`,
`input`, and any label the graph carries.

| Attribute | What it holds | Default |
|---|---|---|
| `triggers` | `{kind name: Trigger}` | `{}` |
| `load_bearing_inputs` | reference files that are triggers in their own right | `()` |
| `pending()` | triggers the site has recorded and not yet asked | `[]` |

### The short form: an nf-core pipeline

nf-core pipelines launch from a CSV samplesheet, and Nextflow names each
task `PROCESS (tag)` with an id from that sheet. `SheetKind`, from the
`clew-nextflow` provider, joins the two. You name the column and the word
your pipeline uses for it.

```python
from clew.provider.nextflow import NextflowAdapter, SheetKind

class QbcWgs(NextflowAdapter):
    name = "qbc-wgs"                                        # what --pipeline accepts
    triggers = {"specimen": SheetKind(column="specimen_id")}  # what --trigger accepts
    load_bearing_inputs = ("GRCh38_qbc.fa", "qbc_panel_v3.bed")
```

```bash
clew impact --graph run.json --pipeline qbc-wgs --trigger specimen:SPC-0412 --samplesheet sheet.csv
```

`--samplesheet` is there because `SheetKind` declared it. If one subject
owns several rows, say a patient with a normal and a tumour sample, add
`members="sample"` and a tag naming either resolves to the patient. That
is how sarek declares `patient`.

### The long form: your own kind

A kind is a small class. Its mode says what losing a value means, its
flags are whatever it needs, and `resolve` returns entry nodes. A
removal returns every value of the kind, since exclusive and shared are
computed against the others.

```python
from clew.contracts import Adapter, Trigger, REMOVE

class LotKind(Trigger):
    """Every task that ran while a reagent lot was in use."""
    mode = REMOVE

    def add_arguments(self, parser):
        parser.add_argument("--lots", help="JSON: {lot: [task hashes]}")

    def resolve(self, graph, value, args):
        # {"LOT-7": ["ab/cdef12", "3f/9a0b44"], ...}
        return json.load(open(args.lots))

    def values(self, args, graph=None):          # optional, for the gate
        return sorted(json.load(open(args.lots)))

class QbcLegacy(Adapter):
    name = "qbc-legacy"
    triggers = {"lot": LotKind()}
```

Over-include. A task wrongly listed costs a re-run. A task wrongly
omitted tells someone their data is clean when it is not.

### Finding inputs without a flag

A kind that knows where its site keeps things needs no flag. `SheetKind`
takes a `locate` callable that turns the graph into a sheet path, from
the directory the tasks ran in:

```python
def sheet_beside_the_run(graph):
    workdir = next(iter(graph["tasks"].values()))["workdir"]
    return str(Path(workdir).parents[2] / "samplesheet.csv")

triggers = {"specimen": SheetKind(column="specimen_id", locate=sheet_beside_the_run)}
```

`--samplesheet` still overrides. A kind you write does the same inside
`resolve`.

### Running unattended: `pending()`

Return the triggers your site has recorded and not yet asked about. Each
is a kind and a value, plus who asserted it and when if the source knows.

```python
class QbcWgs(NextflowAdapter):
    ...
    def pending(self):
        rows = registry_client.withdrawals(status="new")
        return [{"kind": "specimen", "value": r.specimen_id,
                 "asserted_by": r.recorded_by, "date": r.recorded_at}
                for r in rows]
```

Then the whole question is:

```bash
clew impact --graph run_42.json --pipeline qbc-wgs
```

One plan per trigger, one output file per plan, exit 1 if any answer
failed. A `--trigger` on the command line asks that one question instead
and ignores the queue. See [triggers](triggers.md).

## An extractor

An extractor reads one engine's record of a run and returns the graph.
The base owns the parser, the schema check, the summary and `--json-out`.
You add the source flags and the extraction.

```python
from clew.contracts import Extractor

class QbcScheduler(Extractor):
    name = "qbc-sched"
    description = "the QBC scheduler's per-job manifests"

    def add_arguments(self, parser):
        parser.add_argument("--manifests", required=True)

    def extract(self, args):
        return build_graph(args.manifests)
```

`extract` returns `{"tasks": {...}, "edges": [...], "outputs": {...}}`.
The shape is checked before anything is written, and a violation names
the field. Return `None` when the command has already answered, such as a
`--list-runs` listing. Override `summarize(graph, args)` to print
engine-specific lines.

```bash
clew extract qbc-sched --manifests /jobs --json-out legacy.json
```

## Packaging

A provider installs into Clew's own namespace, `clew.provider.<name>`.
`clew` and `clew.provider` are namespace packages, so your distribution
contributes a directory to them and must not put an `__init__.py` at
either level:

```
clew-qbc/
  pyproject.toml
  clew/
    provider/
      qbc/
        __init__.py
        domain.py        class QbcWgs(NextflowAdapter)
        extractor.py     class QbcScheduler(Extractor)
  tests/
    fixtures/            one small real record from your engine
    test_extractor.py    what it emits passes contract_violations
```

One `pyproject.toml`, one entry point per contract you fill. Installing
the package is what delivers them.

```toml
[project]
name = "clew-qbc"
version = "0.1.0"
dependencies = ["clew-lineage>=0.5", "clew-nextflow>=0.5"]

[project.entry-points."clew.adapters"]
qbc-wgs = "clew.provider.qbc.domain"

[project.entry-points."clew.extractors"]
qbc-sched = "clew.provider.qbc.extractor"

[tool.setuptools.packages.find]
include = ["clew*"]
namespaces = true
```

The six providers Clew ships live under `providers/` in its repository
and are built exactly this way. Copy one to start.

The entry point names a module. Importing it defines the class, and
defining a class with a `name` is what registers it. A class that is
missing a required method fails at definition with the method named.

While developing, install it editable and every save is live:

```bash
python3 -m pip install -e ./clew-qbc
```

To see what Clew found and where each came from:

```bash
clew providers
```

A provider that failed to import is listed with the error. A module that
imported but defined no named class is listed as registering nothing.

Nothing leaves the machine. Clew reads the engine's files and your
adapter's answers, and writes a graph and a plan beside them.
