# The event log

A plan is a computation over facts. If the facts can be edited afterwards,
the plan is worth nothing. So the facts get their own store, on Postgres,
whose only job is to make revision impossible for the application and
detectable by everyone else.

```bash
pip install 'clew-lineage[log]'
```

```bash
clew log --dsn "$CLEW_ADMIN_DSN" init --writer-password "$W" --auditor-password "$A"
```

```bash
clew log --dsn "$CLEW_DSN" append --type ContainerDefectReported \
    --subject "gatk4:4.2.1" --actor qa.lead@example.org \
    --effective-from 2026-08-10T00:00:00+00:00 \
    --body '{"defect":"BQSR miscalibration","reference":"JIRA QA-4471"}'
```

## Two clocks

`effective_from` is when the fact became true. `recorded_at` is when we
learned it. A defect discovered in August was true in March, and the release
made in between was made in good faith and still has to be disclosed. One
timestamp cannot say that, and adding the second later means reinterpreting
every historical row.

The log has a clock. The computation does not. A plan stays a pure function
of the facts and byte-identical on replay. Learning is dated, deciding is
not.

## Three layers

Each layer stops what the next cannot.

| Layer | Stops | How it fails |
|---|---|---|
| Role grants | the application | `permission denied for table events` |
| Triggers | the owner's mistake | `clew: the event log is append-only` |
| Hash chain | whoever defeats both | `verify` exits 1 and names the entry |

The writer role holds `SELECT` and `INSERT` and was never granted `UPDATE`,
`DELETE` or `TRUNCATE`. A role cannot grant itself a privilege it does not
hold. This is why the log lives on a server rather than in a file: whoever
holds a file holds every privilege over it. `TRUNCATE` has its own
statement-level trigger because it fires no row triggers and would otherwise
empty the log in one statement.

Verification needs no database and no driver:

```bash
clew log --dsn "$CLEW_AUDIT_DSN" verify
```

```
FAILED at seq 2
  content does not match its own hash: this entry was edited after it was written
  1 entries verified before the break
```

## What it does not prove

The chain detects editing. It does not detect a truncated tail, since a
shorter chain is still self-consistent, and it does not detect a full
rewrite by someone holding the owner's credentials. No hash chain closes
that alone. Anchoring the head hash outside the database does, which is what
[evidence bundles](evidence.md) are for.

Owner and application must be different identities, and the owner's
credentials must stay out of CI. Clew cannot enforce that from inside and
says so. Both limits are asserted as passing tests, so nobody reads `ok` as
"nothing was lost".
