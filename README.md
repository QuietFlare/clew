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

No dependencies beyond Python 3.11 — the demo, the extractors and
`blast.py` are stdlib-only and stay that way. The repo ships a real graph
extracted from an nf-core/sarek run with 5 synthetic donors, 81 tasks,
344 edges.

```bash
python3 demo.py
```

It answers three questions, one per audience, then crosses a run boundary —
all from the same engine.

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
python3 blast.py --graph graph5.json --samplesheet donors.csv --input genome.fasta
```

On the sample run: genome.fasta was consumed directly by 41 tasks, and 72 of
81 tasks are calibrated against it. The other 9 are provably out of scope,
with the derivation chain printed as evidence for every claim.

### QA: "A defect was reported in a GATK4 container. What did it produce?"

```bash
python3 blast.py --graph graph5.json --samplesheet donors.csv --container gatk4
```

On the sample run: 16 tasks ran the container, 68 of 81 tasks are suspect.
Note that nothing is destroyed. A defect casts doubt, it does not remove a
source, so artifacts are rebuilt rather than deleted.

### Compliance: "A donor withdrew consent. What happens now?"

```bash
python3 blast.py --graph graph5.json --samplesheet donors.csv \
    --donor donor_003 --assertions assertions.json
```

On the sample run: 16 of 81 tasks are affected. The 15 that exist only
because of this donor are destroyed, where the artifacts are still on disk.
Add `--work-root` pointing at the run's work directory to get those verdicts;
without it the storage-dependent ones come back `UNDETERMINED`, which is the
honest answer rather than a guess.
The cohort report that also serves the other donors, and was cited in a
publication, resolves to `NOTIFY_ONLY` instead: you cannot unpublish, so the
answer there is disclosure, not deletion. One traversal, two verdicts, which
is the whole point.

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

## Storage is checked, never assumed

Every verdict above depends on two different kinds of fact, and they are not
the same kind at all:

- **Lineage** — what was derived from what. Permanently true, and it is what
  the graph holds.
- **Storage** — whether the bytes are still there. True only at the instant
  you look, and not in the graph at all.

Whoever asks Clew a question is often not standing where the pipeline ran: a
different host, a CI runner, a laptop reading a graph someone emailed over.
So Clew does not check unless you tell it where to look:

```bash
python3 blast.py --graph graph.json --samplesheet samplesheet.csv \
    --donor donor_003 --work-root /path/to/work
```

Without `--work-root`, storage is unverified and any verdict that *depends*
on it is reported `UNDETERMINED` rather than guessed. Verdicts that hold
whatever the disk says are still returned — under v2 a published artifact is
`NOTIFY_ONLY` either way, and that is an answer, not a guess:

```
  UNDETERMINED  (57)  — no verdict; see below
    storage state not verified, and the verdict depends on it. Verifying
    would decide between ALREADY_GONE, DESTROY or QUARANTINE. Refusing to
    guess: assuming the artifact survives over-claims work, and assuming it
    is gone reports an obligation as already discharged.
```

With it, Clew looks and says what it found — on the cross-run graph, 46 tasks
whose scratch really was cleaned, and 11 whose artifacts are still there:

```
AFFECTED: 57 of 183 tasks
  ALREADY_GONE  (46)  — no longer exists; nothing to do
  REGENERATE    (11)  — recompute from the remaining sources
```

**`ALREADY_GONE` is now only ever reached by looking and not finding.** It
used to be returned whenever the recorded path failed to resolve — which
fired identically when the volume was not mounted, when the graph came from
another machine, when the extractor never recorded a workdir at all (every
RO-Crate task), and when the published fixtures had their paths anonymised.
All of those produced *"no longer exists; nothing to do"*, silencing exactly
the artifacts that carry obligations. A false negative that discharges an
obligation is worth far more care than a false positive that wastes work.

Recorded paths belong to whichever machine ran the pipeline, so only the
two-character prefix and task hash are joined onto the root you supply. A
graph stays portable between hosts without pretending its absolute paths mean
anything locally.

An undetermined item is **not clean, it is unanswered** — it carries the set
of verdicts still in play, so a CI gate can fail on it and a reader can see
exactly what checking the disk would settle.

## The event log

Everything above is a computation over facts. If the facts can be edited
afterwards, none of it is worth anything — so the facts get their own store,
on Postgres, whose only job is to make revision impossible for the
application and detectable for everyone else.

```bash
pip install 'psycopg[binary]'
```

```bash
python3 logbook.py --dsn "$CLEW_ADMIN_DSN" init \
    --writer-password "$W" --auditor-password "$A"
```

```bash
python3 logbook.py --dsn "$CLEW_DSN" append --type ContainerDefectReported \
    --subject "gatk4:4.2.1" --actor qa.lead@example.org \
    --effective-from 2026-08-10T00:00:00+00:00 \
    --body '{"defect":"BQSR miscalibration","reference":"JIRA QA-4471"}'
```

**Two clocks, deliberately.** `effective_from` is when the fact became true;
`recorded_at` is when we learned it. A defect discovered in August was true
in March, and the release made in between was made in good faith and still
has to be disclosed. One timestamp cannot say that, and adding the second
later means re-interpreting every historical row.

The log has a clock; the computation does not. A plan stays a pure function
of the facts and byte-identical on replay. Learning is dated, deciding is not.

**Three layers, each stopping what the next cannot.** They are not redundant:

| Layer | Stops | How it fails |
|---|---|---|
| Role grants | the application | `permission denied for table events` |
| Triggers | the owner's mistake | `clew: the event log is append-only` |
| Hash chain | whoever defeats both | `verify` exits 1 and names the entry |

The writer role holds `SELECT` and `INSERT` and was never granted `UPDATE`,
`DELETE` or `TRUNCATE`; a role cannot grant itself a privilege it does not
hold. This is why the log is on a server rather than in a file — whoever
holds a file holds every privilege over it. `TRUNCATE` has its own
statement-level trigger because it fires no row triggers at all and would
otherwise empty the log in one statement.

Verification needs no database and no driver:

```bash
python3 logbook.py --dsn "$CLEW_AUDIT_DSN" verify
```

```
FAILED at seq 2
  content does not match its own hash: this entry was edited after it was written
  1 entries verified before the break
```

**What it does not prove.** The chain detects editing. It does not detect a
truncated tail — a shorter chain is still self-consistent — nor a full
rewrite by someone holding the owner's credentials. No hash chain closes
that alone; anchoring the head hash outside the database does, which is what
the evidence bundle is for. Owner and application must be different
identities and the owner's credentials must stay out of CI. Clew cannot
enforce that from inside, and says so rather than implying otherwise. Both
limits are asserted as passing tests, so nobody reads `ok` as "nothing was
lost".

## Policy versioning

Every verdict above is a verdict *under a table*. If that table is an
if-ladder in Python, editing it silently re-interprets every plan ever
produced: a plan from March says `QUARANTINE`, the code says `QUARANTINE`
today, and nobody can tell whether it said `QUARANTINE` in March. The history
is unfalsifiable, which is the same as worthless.

So the table is data, with a version and a content hash:

```bash
python3 rulebook.py show
```

```
  R3   exclusive=True, storage=WRITABLE                     -> DESTROY
       Exists only because of this subject and the bytes can be changed.
       Nothing else needs it, so it goes entirely.
```

Every plan names the policy in its header and every line cites the rule that
decided it, so "policy v1, rule R5, these hashes, re-run and get the same
answer" is a checkable sentence.

**Rule order is semantics** — first match wins, and an omitted dimension is a
wildcard. That is not a detail: it is the entire difference between the two
shipped versions.

```bash
python3 rulebook.py diff v1 v2
```

```
order  v1: R1 R2 R3 R4 R5 R6 R7 R8
       v2: R2 R1 R3 R4 R5 R6 R7 R8

  R2  position 2 -> 1
      - Immutable history — published, or already past a trust boundary.
        Terminates remediation, not notification: you cannot unpublish...
      + Immutable history — published, or already past a trust boundary.
        Asked first, before existence: destroying our copy does not reach
        the published or transferred one, so the obligation to disclose
        survives the bytes.
```

v1 asked "does it still exist?" before "was it published?", so a published
artifact whose working copy had been deleted came back `ALREADY_GONE` —
*nothing to do*. Deleting your copy of something does not un-publish it.

The practical consequence was sharper than it first looks. `R1` is the only
rule that can yield `ALREADY_GONE`, so putting it first made **every** verdict
depend on the storage state — under v1, nothing at all is decidable without a
disk check. Under v2 a published artifact resolves without one, because the
answer genuinely does not depend on it:

```bash
python3 blast.py --graph graph5.json --samplesheet donors.csv \
    --donor donor_003 --assertions assertions.json --policy v1
```

```
POLICY: v1  dbb59de6d85fc0f8       POLICY: v2  e6ba60ffe6763949
  UNDETERMINED  (16)                 NOTIFY_ONLY   (1)   c9/023b13  MULTIQC
    c9/023b13  MULTIQC               UNDETERMINED (15)
```

**v1 is still here, byte for byte.** `--policy v1` resolves it, and a plan
computed in January replays under the table that produced it rather than
under today's. A semantic change is a new version, never an edit — the
shipped hashes are frozen as literals in the test suite, so editing one fails
the build and says to add a version instead.

**Adoption is a logged fact.** `rulebook.py register` writes a
`PolicyAdopted` event carrying the whole table, not a pointer to it — a
pointer to code is worthless six months and four releases later:

```bash
python3 rulebook.py register --dsn "$CLEW_DSN" --actor qa.lead@example.org
```

**Validation refuses, it does not warn.** A policy naming an unknown action,
an impossible value, a duplicate rule id, or a rule with no rationale is
rejected at load. The one that matters most is a mistyped dimension, which
would otherwise load cleanly and silently never match — and a rule that never
matches is indistinguishable from a deleted one, except that the file still
shows it and everyone believes it applies.

Two guards sit outside the rule list where no policy can reach them: an
unrecognised contribution class becomes `IRREDUCIBLE` before matching, and
falling off the end of the rules yields `QUARANTINE` rather than an error or
a pass.

**Be precise about what that guarantees.** It fixes the facts, not the
verdict. A policy mapping `IRREDUCIBLE` to `PURGE` is expressible, would be
wrong, and Clew will run it. That is not a hole — it is the reason the table
is data. Wrong logic in an if-ladder is invisible in a code review nobody
does; wrong logic in a hashed, versioned, rationale-carrying file sits in the
open with a rule id on it.

**This is the core table, not the customer's policy.** It defines what the
classes *mean*, so changing it changes the semantics of every historical
plan — which is exactly why it is versioned. Which of a customer's events map
to which class, what counts as published, what a given withdrawal tier may
reach: that is a separate object, it lives in `domains/`, and it is not this
file.

## Evidence bundles

A plan on someone's terminal is a claim. A bundle is the artifact that lets a
third party check it without trusting you, without your database, and without
your code being the thing that says so.

```bash
python3 evidence.py build --out bundle/ --plan plan.json --dsn "$CLEW_DSN" \
    --input graph.json --input samplesheet.csv --seal-into-log \
    --actor qa.lead@example.org
```

```bash
python3 evidence.py verify bundle/
```

`verify` reads a directory. **No database, no network, no credentials, no
driver installed** — an assessor who does not trust the party that produced a
bundle must be able to check it anyway, and any step routing through the
producer's infrastructure defeats that.

```
  ok   files      6 files, all hashes match
  ok   log        2 entries re-chain to the recorded head (seq 2)
  ok   policy     v2 matches the hash the plan cites
  ok   replay     all 57 verdicts recompute identically from the bundled facts and policy
  ok   signature  sealed by qa.lead@example.org
```

**`replay` is the one that matters.** A folder of documents proves only that
somebody assembled a folder. Replay re-derives *every* verdict from the
bundled facts and the bundled table, offline. Rebuild the manifest so all the
hashes match again and change only the conclusion, and it still fails:

```
  ok   files      6 files, all hashes match
  FAIL replay     1 of 57 verdicts do not reproduce:
                  da:06/31c01f: recorded ALREADY_GONE, recomputes to REGENERATE
```

**Bundles are clock-free.** The same inputs produce the same bundle hash,
which is testable and tested. A timestamp inside would change the hash on
every build and quietly destroy the reproducibility claim; time lives in the
log, which is the thing with clocks, and sealing is itself a logged event.

### This is what closes the log's open gap

A hash chain detects editing but not truncation — lopping entries off the end
leaves a shorter, self-consistent chain. Nothing inside the database can fix
that. The fix has to be a witness its owner does not control.

The bundle records the log head it covered, and `--seal-into-log` records the
bundle hash back into the log. Neither can be rolled back without
contradicting the other:

```bash
python3 evidence.py witness bundle/ --dsn "$CLEW_DSN"
```

```
$ logbook.py verify              # the log alone, after entries 2-3 were deleted
OK  1 entries, chain intact      # a short chain is a valid chain

$ evidence.py witness bundle/
FAIL witness   the log has no entry at seq 2, but this bundle recorded one.
               Entries have been removed from the end since this bundle was issued.
```

To make a truncation stick, someone would now have to collect every copy of
every bundle ever issued. `witness` is a separate command from `verify` on
purpose: verifying needs no credentials and must stay that way.

### Signing is delegated, not invented

Clew **seals** — a SHA-256 manifest over every file, plus a bundle hash over
the manifest. Standard library only, so anyone can check it.

Clew does **not** implement signing. A signature checkable only by someone
holding the signing key is not a signature in the sense an assessor means,
and inventing crypto here would be indefensible. Countersigning is detached
and uses `ssh-keygen -Y`, which ships with OpenSSH and whose keys your
organisation already manages:

```bash
python3 evidence.py sign bundle/ --key ~/.ssh/id_ed25519
python3 evidence.py verify bundle/ --allowed-signers allowed_signers
```

A signature from a key not in `allowed_signers` fails as *"by someone this
reader has no reason to trust"*. The seal is Clew's; who sealed it belongs to
your key infrastructure.

The bundle is also a valid **RO-Crate** — adopted rather than invented, so it
survives being handed to tooling that has never heard of Clew.

## The CI gate

Everything above answers *after the fact*. The gate asks the opposite
question, before anything runs: is any of this material something we are not
allowed to use?

```bash
python3 gate.py --pipeline sarek --samplesheet samplesheet.csv \
    --dsn "$CLEW_DSN" --gate-policy gate-policy.json --out clew-evidence/
```

```
CLEW GATE  donors.csv
  blocking on     ConsentWithdrawn, QCFailed, SampleContaminated
  cleared by      ConsentReinstated, QCPassed
  log head        seq 5  0a9f436b2c7dca66

  BLOCKED  1
      donor_003    ConsentWithdrawn effective 2026-03-01, asserted by registry@example.org
          log seq 1, entry 01fd6803a761d58a
  UNKNOWN  2
      donor_002    the log holds no decisive fact about this subject
  CLEARED  2
      donor_001    QCPassed effective 2026-05-20, asserted by lab.qa@example.org

STOP  1 blocked, 2 unknown, 2 cleared
```

Exit 1 stops the build. Compliance as a build check, at the point where
stopping is cheap.

### Three outcomes, not two

`BLOCKED`, `CLEARED`, and **`UNKNOWN`** — the log has nothing to say about
this subject at all. A gate that reports unknown as "not blocked" passes
everything it failed to check, and goes green the day someone mistypes an
identifier or points it at the wrong log. So unknown stops the build unless
`--allow-unknown` says somebody decided otherwise, and even then it is still
counted and reported as unknown rather than relabelled clean.

The report also says so outright when the log had never heard of *any*
subject in the samplesheet — which almost always means the identifiers do not
match between the two, not that everything is permitted.

### Every way of failing to check exits non-zero

| | |
|---|---|
| the log is unreachable | stop — an unreachable log is not a clean one |
| no blocking types given | stop — that is not a lenient gate, it is no gate |
| a subject is `UNKNOWN` | stop, unless explicitly allowed |
| a subject is `BLOCKED` | stop |

A green build must mean *checked and permitted*. If it can also mean *could
not check*, the gate is decorative — and a decorative compliance gate is
worse than none, because it manufactures a record of diligence that did not
happen.

### Decisions get reversed, and dates matter

For each subject the **latest fact in effect** decides, ordered by
`effective_from` — when the decision was made in the world, not when it was
entered. A subject withdrawn in March and reinstated in June is usable in
July. Same-day facts are broken by log order, the only tiebreak nobody can
back-date.

Which makes "was this run permitted *when we ran it*?" answerable:

```bash
python3 gate.py ... --as-of 2026-05-01
```

```
as of 2026-05-01          as of today
  BLOCKED  3                BLOCKED  1
  CLEARED  0                CLEARED  2
```

Facts effective *after* the date asked about are ignored, so a historical
gate result stays reproducible instead of changing every time someone records
something new. This is what the log's two clocks were for.

### It emits its own evidence

`--out` seals the result into a bundle, and `evidence.py verify` re-derives
the gate decision from the bundled facts and the bundled gate policy — the
same discipline as replaying a remediation plan:

```
  ok   files      6 files, all hashes match
  ok   log        5 entries re-chain to the recorded head (seq 5)
  ok   gate       passed=False; all 5 subject outcomes recompute identically
```

The shipped workflow at
[.github/workflows/clew-gate.yml](.github/workflows/clew-gate.yml) uploads
that bundle with `if: always()`, because the evidence of a *refusal* matters
at least as much as the evidence of a pass — an artifact that only survives
on green builds documents the days nothing was wrong.

The gate runs with **read-only credentials**. It asks questions; it has no
reason to hold a role that can write facts, and CI is the last place to put
one that can.

Clew ships [gate-policy.example.json](gate-policy.example.json) as a
template, not a recommendation. Which of your event types should stop work is
yours to author and yours to defend.

## For auditors: a dashboard and a chat interface

Everything above produces evidence. These two read it, and both go through
the same `core/query.py` — two surfaces answering the same questions two
different ways would eventually disagree, and on the day they did nobody
could say which was wrong.

### A page you can open from a USB stick

```bash
python3 dashboard.py --bundles /path/to/bundles --out evidence.html
```

One self-contained HTML file. No server, no network, no scripts, prints
legibly. **It carries no timestamp**, so regenerating it from unchanged
bundles produces an identical file — two auditors comparing pages should be
comparing evidence, not diffing dates.

*What is not known* sits above *Findings*, deliberately. A compliance
dashboard that renders its gaps in small grey text below the fold is worse
than no dashboard: it manufactures the impression of a clean bill of health
out of an incomplete record. Withheld verdicts and subjects unknown to the
log get counters of their own.

The page says outright that it is **not the record** — the bundles are — and
every panel names the bundle hash it was drawn from.

### An MCP server, so an auditor can just ask

```bash
python3 mcp_server.py --bundles /path/to/bundles
```

MCP over stdin/stdout, stdlib JSON-RPC, no SDK. Point any MCP client at it:

| Tool | Answers |
|---|---|
| `list_bundles` | what evidence exists at all |
| `subject_history` | every recorded fact about one subject, both clocks, actors |
| `policy_in_force` | which table the log says applied on a date |
| `verdict` | why one task got the verdict it did — rule id, rationale, chain |
| `was_affected` | "show that this output did not use that material" |
| `check_integrity` | the deterministic verifier's own output |
| `gate_result` | what a pre-flight gate blocked, and on what basis |

**Clew ships no model and calls none.** It exposes tools; the auditor's own
client supplies the conversation. That is the architecture, not modesty about
scope — *no AI in the decision path* stays literally true, because every
verdict was computed before the server started, by code that has never seen a
prompt. A model cannot talk the tools into a different answer.

**Read-only by construction.** No tool writes anything and the server never
opens a database connection at all — it reads sealed bundles. Recording a
fact is `logbook.py`, run by a person with an actor identity, and an
auditor's chat session is the last place a new fact should be able to enter a
compliance record. There is a test asserting `mcp_server.py` contains no
connection call, so a future convenience has to break it to land.

**Every answer carries its citations**, structurally — `query.answer()`
refuses to return without them:

```json
{ "kind": "log_entry", "seq": 1, "hash": "01fd6803a761d58a…",
  "actor": "registry@example.org", "effective_from": "2026-03-01" }
```

That matters because the consumer is a language model, and an auditor cannot
tell fluent-and-wrong from fluent-and-right by reading it. Facts welded to
their citations make a bad paraphrase *checkable* rather than merely
persuasive. The guardrail the server hands the model at startup says it
plainly: quote the citations, read the coverage out loud, and **never
conclude compliance** — there is no tool here that says an obligation was
met, and the model must not supply one.

That is a guardrail, not a guarantee. Assume the prose is a convenience and
the citations are the record.

### A log has no identity, and that is handled rather than hidden

Two logs both number their entries from 1. Merging bundles sealed from
different logs would interleave two unrelated histories into one plausible
timeline, and no field distinguishes them. So the loader compares what must
agree if the logs are the same — an entry at a given sequence number has one
hash — and says so loudly when they do not:

```
THESE BUNDLES DISAGREE: 2 sequence numbers (including 1, 2) carry different
entries in different bundles, which means they were sealed from DIFFERENT
LOGS. Answers drawn from the combined history are not trustworthy.
```

The warning travels on every answer drawn from the merged entries, and the
dashboard renders it as a stop panel above everything else. Giving the log a
real identity is the proper fix and is not built.

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
core/       traversal, contribution vocabulary, versioned policy, event log,
            evidence bundles, the gate, the query surface. Zero domain
            vocabulary.
domains/    the layer allowed to know about sarek, samplesheets, donors.
tests/      287 tests, stdlib unittest.
```

The boundary is enforced by a grep: `core/` must never mention a sample, a
donor, a consent, or a workflow engine, and must import nothing from
`domains/`. Adding a new domain (say, AI training data with opt-out
semantics) means adding a directory, not editing core. That grep is
[tests/test_core_boundary.py](tests/test_core_boundary.py) — an unenforced
rule stays true right up until it doesn't.

```bash
python3 -m unittest discover -s tests
```

262 of the tests need nothing installed. The other 25 exercise the log's
storage behaviour — the role grants, the triggers, concurrent appends — and
skip unless you point them at a database you own:

```bash
CLEW_TEST_DSN=postgresql://user:pw@localhost:5432/clew python3 -m unittest discover -s tests
```

The log's arithmetic is deliberately on the other side of that line. Hashing
and chain verification are pure functions on plain dicts, so an auditor
checking an exported bundle needs a JSON file and an interpreter — not a
database driver and a server. "Anyone can check this without us" has to be a
fact rather than a slogan.

## Status

Working: lineage extraction from real runs, blast radius for three trigger
types, contribution classes with fail-closed defaults, remediation plans,
publication assertions, mixed verdicts from a single traversal, the
append-only event log, the versioned remediation policy, sealed evidence
bundles that replay offline, the CI gate, and the auditor surfaces — an
offline dashboard and a read-only MCP server.

Not built: a log identity, so bundles from different logs are detected rather
than distinguished. A donor-facing transparency log. Any domain beyond
nf-core pipelines.

## Contributing

Issues and pull requests are welcome — especially from people who run
pipelines for a living and can say where the model is wrong. See
[CONTRIBUTING.md](CONTRIBUTING.md); a first pull request needs the CLA
agreement described there, an issue needs nothing at all.

## License

[AGPL-3.0](LICENSE).
