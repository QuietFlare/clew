# Changelog

All notable changes to Clew. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions
follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `clew impact --pipeline snakemake`: a subject adapter that attributes a
  job to a sample named in its output path.
- ADR 0007: extractors translate, core never interprets an engine string.
- ADR 0008: a bundle verifies against something it does not control.
- ADR 0009: an effective date is an instant, and the gate has a now.

### Fixed

- `clew impact`: evidence chains come from one breadth-first pass over the
  graph. The per-target search had no visited set, so a reference-update
  trigger on a run with scatter-gather stages did not finish.
- `clew impact`: a cleaned work directory is only `ALREADY_GONE` once the
  published tree has been checked too. Without `--results` the verdict is
  withheld. Directory outputs are now found under `--results` by name.
- `clew impact`: a subject that matches no task tag exits non-zero instead
  of reporting zero affected tasks.
<<<<<<< HEAD
- `clew reclaim`: a task is `FAILED` only when the engine recorded a
  failure. Every extractor now maps its engine's status word (`Done`,
  `SUCCEEDED`, `skipped`, `CACHED`) to one of `COMPLETED`, `FAILED`,
  `CACHED`, `UNKNOWN`, keeps the original as `engine_status`, and reclaim
  keeps anything it does not recognise instead of proposing it.
- `clew extract-store`: a task is `superseded` only by a same-named task
  from a later run in the resume chain, and never while another task in
  the session reads its outputs. Same-named tasks in one run, such as
  scatter shards, were all but one marked superseded and proposed for
  deletion. Reclaim also keeps a superseded task with downstream consumers.
- `clew reclaim`: a published copy whose size no longer matches the
  recorded one withholds the directory, and `--apply` re-hashes every copy
  a `REDUNDANT` verdict rests on before removing anything.
- `clew stitch`: an input digest produced by more than one task is bridged
  to every producer and reported, instead of silently taking the first.
- `clew reclaim`: files with more than one hard link are left out of the
  reclaimable byte count, and the caveats say so.
- `clew reclaim`: when two outputs share a digest, the published copy with
  the output's own file name is the one named in the plan and receipt; all
  candidates are listed only when none matches.
- Storage checks joined the last two components of a task's recorded path
  onto `--work-root`, which fits Nextflow only. Every extractor now records
  `workpath`, the task directory relative to the engine's root, and
  `impact`, `reclaim` and `digest` join that. Cromwell shards and
  subworkflow calls resolve; Snakemake tasks, which share one directory,
  are left unverified instead of read as destroyed or deletable.
- `reclaim`, `digest` and `impact` leave storage unchecked for every task
  that resolves to a directory another task also resolves to, with one
  warning, so one redundant output cannot make a whole workflow deletable.
- `impact`: when none of a graph's task directories exists under
  `--work-root`, storage is left unchecked and a warning says the root
  looks wrong, instead of every task reading `ALREADY_GONE`.
- `--runs` on a `.lineage` store: the digest sidecar is keyed by session,
  so a digest written under one run name of a resume chain is found under
  the other.
- `clew extract-snakemake`: an output's digest is filled from the checksum
  its consumers recorded, so `drift` and `stitch` can compare Snakemake
  runs without `clew digest`.
- `clew evidence verify`: the bundled chain is checked from genesis, or from
  the previous bundle's log head, which the manifest now records under
  `anchors.since` and `anchors.previous_log_head`. It was checked against
  its own first `prev_hash`, so a forged chain verified. A bundle claiming
  a log head with no `events.json` now fails the log check.
- `clew gate`, `policy_in_force`: `effective_from` and `--as-of` are
  compared as instants, not as text. A date-only `--as-of` covers the whole
  day, mixed offsets order by the moment they name, and an unparseable
  `effective_from` is refused at append time.
- `clew gate`: `as_of` defaults to now (UTC) and the value used is recorded
  in the result and the bundle, so a sealed gate result can be re-derived
  after the log has grown.
- `policy.decide`: a value outside a dimension's possible values is an error
  rather than a silent non-match, and `None` on `exclusive` or `terminal` is
  unverified, evaluated the way storage already was. A plan with
  `terminal: null` can no longer replay to a settled verdict.
- `clew evidence build`, `clew gate --out`: a non-empty output directory is
  refused unless `--force`; `verify` fails on any subdirectory or other
  entry the manifest does not list.
- `clew evidence verify`: replay also recomputes each undetermined item's
  `possible` map and the plan's `actions` counts and `tasks_affected`.
- `clew evidence build --input`: `inputs.json` is keyed by content hash and
  records the basename only, so the path spelling no longer changes the
  bundle hash. Two files with the same content under different names are
  refused.
- Manifest file names containing `/`, `\`, or `..` fail verification and
  are skipped by the bundle store.
- `eventlog.append`: `recorded_at` is the database server's clock, read in
  the appending transaction. Callers can no longer supply it.
=======
- `clew extract-work`: refuses when any task's work directory is missing,
  naming them; `--allow-partial` writes the graph with the gap as a
  `coverage` note. A cleaned tree used to give an empty graph and exit 0.
- `--container` and `container:` triggers read the needle and each image as
  name and version. Wave, digest-pinned, Singularity and conda images no
  longer miss; a versioned needle against a versionless image matches on
  name and is reported as such.
- `--input` and `input:` triggers also reach companions such as
  `genome.fasta.fai`, and list them.
- nf-core attribution tries samplesheet ids longest first, so `donor` no
  longer claims `donor_003-L1`.
- A samplesheet with one sample id under two subjects is refused by name
  instead of the last row silently taking the tasks.
- `--trigger subject:X` with `--samplesheet` resolves through the domain
  adapter like `--subject X`; without one it says the graph carries no
  subject labels.
- Latch and Cromwell join a file read from inside another task's directory
  output to that task.
- DNAnexus resolves job-based and stage references to the producing job,
  reads `runInput` and `originalInput` as fallbacks, and counts what it
  cannot resolve in a `coverage` note instead of dropping it.
- `clew drift`: same-named tasks pair on input digests, superseded
  versions are left out, unpaired tasks are `UNVERIFIED`, and a task whose
  only differing upstream is unverified is `UNSETTLED` rather than
  `DOWNSTREAM`. `--runs` refuses two runs of one resume chain.
- `clew digest` keeps an engine digest of any algorithm.
- `--runs` orders "latest" by the record's own timestamp, says when it fell
  back to mtime, and accepts a session-id prefix.
- `clew extract-store` reads a bare-string path input as one input.
- `clew impact` prints the graph's `coverage` notes and unexpanded
  subworkflow count and carries them into the plan's caveats; the
  donor table without `--subject` now reads the samplesheet.
- `clew extract-work` refuses a six-character hash prefix that matches two
  work directories instead of merging them.
>>>>>>> f360682 (Attribute subjects longest first, surface graph limits in impact)

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
