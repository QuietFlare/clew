# Rules

The non-negotiables a diagram cannot show. Each is a decision in
[docs/adr/](../docs/adr/) or a boundary in [docs/architecture.md](../docs/architecture.md); this
page is the short form the design reviewer holds every pull request to.
A change to one is a new ADR that supersedes the old, then the code.

- Packages import downward only. `graph/` imports nothing from clew. A
  provider imports the graph, the contracts and the extract tools, never
  a question and never another provider.
- `graph/`, `ledger/` and `contracts/` carry no domain and no engine
  vocabulary: no sample, donor, consent, Nextflow or Snakemake.
- Anything Clew cannot establish is reported as unknown, never as clean.
- A file is identified by its content digest and nothing else.
- No model touches a verdict. A model proposes from a fixed option set;
  versioned rules decide; the record names the model that was asked.
- Clew keeps no record of a run. The engine's record is the record.
- A plan is the record: JSON, clock-free, byte-identical for the same
  inputs. A page is a view of it.
- A bundle verifies against something it does not control.
- The core has no dependencies beyond the standard library and runs on
  Python 3.9.
