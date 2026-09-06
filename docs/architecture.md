# Architecture

Two streams meet in the middle. One is machine facts, whatever the engine
recorded, merged into one graph. The other is human facts, things no engine
can know, translated by the domain layer into graph terms. The core knows
nothing about either world. It traverses, applies the versioned policy, and
logs.

```mermaid
flowchart TB
    subgraph ENG["The engine already writes this"]
        LS["Nextflow lineage store"]
        HL["horus-lineage records"]
        RC["nf-prov RO-Crate"]
        WS["work/ symlinks"]
    end
    subgraph PA["People assert this"]
        W["Withdrawal"]
        P["Publication"]
        D["Tool defect"]
    end
    subgraph CORE["graph/ and ledger/: the engine, zero domain vocabulary"]
        T["Traversal<br/>blast radius, classes"]
        POL["Policy<br/>versioned, hashed"]
        LOG["Log + evidence<br/>append-only, replayable"]
    end
    ENG -- extractors --> G["One stitched graph<br/>every run, one JSON"]
    PA --> DOM["domains/<br/>subject &rarr; graph nodes"]
    G --> CORE
    DOM --> CORE
    CORE --> PLAN["Remediation plan<br/>delete, re-run, disclose"]
    CORE --> GATE["CI gate<br/>blocks bad inputs"]
    CORE --> AUD["Dashboard + MCP<br/>answers with citations"]
```

```
clew/graph/      the one graph: loading, triggers, traversal, contribution
                 classes. Imports nothing else from clew.
clew/ledger/     versioned policy, event log, evidence bundles, the gate
                 decision, the query surface, and their commands.
clew/domains/    the layer allowed to know about sarek, samplesheets, donors.
clew/extract/    one extractor per lineage source, all emitting the same
                 JSON, plus stitch.
clew/questions/  one module per question asked of the graph: impact, gate.
clew/views/      dashboard, report, MCP server.
tests/           stdlib unittest.
```

Packages import downward only. `graph/` imports nothing from clew, and
each package above it may reach only the layers below it. A new question
lives in `questions/` and imports `clew.graph`, which is also the public
API: `load_graph`, `blast_radius`, `classify`, `parse_trigger`,
`resolve_trigger`.

The vocabulary boundary is enforced by a grep. `graph/` and `ledger/` must
never mention a sample, a donor, a consent, or a workflow engine. Adding a
new domain, say AI training data with opt-out semantics, means adding a
directory rather than editing the engine. Both rules are
[tests/test_core_boundary.py](../tests/test_core_boundary.py). An unenforced
rule stays true right up until it doesn't.

## Tests

```bash
python3 -m unittest discover -s tests
```

308 tests need nothing installed. The other 25 exercise the log's storage
behaviour, the role grants, the triggers and concurrent appends, and skip
unless you point them at a database you own:

```bash
CLEW_TEST_DSN=postgresql://user:pw@localhost:5432/clew python3 -m unittest discover -s tests
```

The log's arithmetic sits on the other side of that line on purpose.
Hashing and chain verification are pure functions on plain dicts, so an
auditor checking an exported bundle needs a JSON file and an interpreter,
not a database driver and a server.

## What Clew claims, and what it does not

Clew is a system of record, not an attester. It claims three things, all
checkable: the computation is deterministic, the result follows from the
inputs, and anyone can re-run it and get the same answer. It claims nothing
about whether your inputs were true or your policy was right. Your
assertions are inputs, not Clew's claims.

The edges of the model, reported rather than hidden:

- Traversal follows derivation and stops at influence. A published finding
  that informed a business decision is recorded as a terminal reference. It
  is not pretend-tracked.
- Uninstrumented systems are reported as unknown, never as clean.
- Clew proves the record was tombstoned and nothing later referenced it. It
  does not prove physical destruction. No cryptography reaches a freezer.

## Not built

A log identity, so bundles from different logs are detected rather than
distinguished. A subject-facing transparency log. Domain adapters beyond
nf-core pipelines, though the generic label trigger covers engines that
record labels, Horus among them.
