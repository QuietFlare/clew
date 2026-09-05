# Changelog

All notable changes to Clew. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions
follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- DNAnexus support: `clew extract-dnanexus` builds a graph from an
  analysis, over the API or from saved describe output. Edges join on
  file ID. Optional `price` and `duration_s` per task.
- Latch support: `clew extract-latch` builds a graph from an execution,
  over the API or from saved records. Edges join on `latch://` path.
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

[Unreleased]: https://github.com/QuietFlare/clew/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/QuietFlare/clew/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/QuietFlare/clew/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/QuietFlare/clew/releases/tag/v0.1.0
