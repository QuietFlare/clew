# Changelog

All notable changes to Clew. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions
follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- `clew impact`: evidence chains come from one breadth-first pass over the
  graph. The per-target search had no visited set, so a reference-update
  trigger on a run with scatter-gather stages did not finish.
- `clew impact`: a cleaned work directory is only `ALREADY_GONE` once the
  published tree has been checked too. Without `--results` the verdict is
  withheld. Directory outputs are now found under `--results` by name.
- `clew impact`: a subject that matches no task tag exits non-zero instead
  of reporting zero affected tasks.

## [0.4.0] - 2026-09-07

### Added

- `clew drift`: where two runs of the same workflow part ways, and why.
  Tasks paired by name, outputs compared by content digest, the cause of
  each root read from the record.
- `--runs` on digest, reclaim and drift: read a run straight from a
  `.lineage` store, a horus-lineage root, or a directory of graphs. The
  digests `clew digest` computes go to a sidecar under `<runs>/.clew/`.

## [0.3.0] - 2026-09-06

### Changed

- Modules are grouped by layer: `clew.graph`, `clew.ledger`,
  `clew.extract`, `clew.questions`, `clew.views`. `clew.core` is gone. The public API is
  `clew.load_graph`, `blast_radius`, `classify`, `parse_trigger` and
  `resolve_trigger`. Commands are unchanged.
- `clew stitch` joins runs by content digest. `--results` is gone.

### Added

- `clew reclaim`: which work directories are safe to delete, with the
  proof for each. Nothing is removed without `--apply` and a receipt.
- Content digests in the graph, `digest` fields of the form
  `<algorithm>:<value>`, recorded by the Horus, Snakemake and Nextflow
  store extractors where the engine has them.
- `clew digest`: hash a run's files once, for runs the engine recorded
  without content digests.
- DNAnexus support: `clew extract-dnanexus` builds a graph from an
  analysis, over the API or from saved describe output. Edges join on
  file ID. Optional `price` and `duration_s` per task.
- Latch support: `clew extract-latch` builds a graph from an execution,
  over the API or from saved records. Edges join on `latch://` path.
- Cromwell support: `clew extract-cromwell` builds a graph from workflow
  metadata, from a file or a server. Scatters become one node per shard,
  subworkflows are flattened, edges join on path. Verified on Cromwell 92.
- Snakemake support: `clew extract-snakemake` reads the `.snakemake`
  metadata store, file or SQLite backend, and carries Snakemake's own
  input checksums onto the edges. Verified on Snakemake 9.26.
- A graph contract in core, `contract_violations`, run by the tests over
  every shipped and fixture graph.

### Fixed

- The runtime version now matches the package version. 0.2.0 reported
  itself as 0.1.1.

## [0.2.0] - 2026-09-05

### Added

- Horus support: `clew extract-horus` reads a horus-lineage run directory
  and joins edges by content digest, so a graph closes across machines.
- A trigger registry and the generic `--trigger kind:value` form. An
  unknown kind is read as a label key, so engines that record labels answer
  label queries with no adapter.
- `--html` on `clew impact` writes a self-contained impact report.
- The lineage store extractor follows resumed runs: tasks cached from an
  earlier session in the same chain stay in the graph, replaced tasks are
  marked rather than dropped, and an unknown store version is refused.
- A comparison of what each lineage source can prove, in the docs.
- Output sizes in the work-directory extractor.

### Changed

- `--donor` is now `--subject`. The old flag still works.
- Generic graph queries moved from the domain adapters into core.
- Shipped demo graphs are anonymised.
- The README is a front page. The design documentation moved to `docs/`.

### Fixed

- External inputs that nf-prov crates record as URLs are kept rather than
  dropped.
- The work-directory extractor documents that forwarded files lose their
  producer, and the lineage store is preferred for that reason.

## [0.1.1] - 2026-08-30

### Added

- Python 3.9 support, which is what macOS ships.

### Fixed

- The work-directory extractor refuses copy-staged work directories instead
  of returning an empty graph that would report every task as clean.

## [0.1.0] - 2026-08-27

First release on PyPI as `clew-lineage`.

- Lineage extraction from Nextflow's native lineage store, nf-prov RO-Crates
  and work-directory symlinks, all emitting one graph format.
- Blast radius for subject, container and external-input triggers, with
  remove and distrust modes.
- Contribution classes with fail-closed defaults and a versioned,
  content-hashed remediation policy.
- Storage checked against a work root, never assumed.
- Cross-run stitching at published files.
- An append-only, hash-chained event log on Postgres with two clocks.
- Sealed evidence bundles that replay offline, witnessed back into the log
  and countersigned with OpenSSH keys.
- A CI gate that fails closed on unknown subjects.
- A self-contained dashboard and a read-only MCP server for auditors.

[Unreleased]: https://github.com/QuietFlare/clew/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/QuietFlare/clew/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/QuietFlare/clew/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/QuietFlare/clew/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/QuietFlare/clew/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/QuietFlare/clew/releases/tag/v0.1.0
