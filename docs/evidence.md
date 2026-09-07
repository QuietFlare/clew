# Evidence bundles

A plan on someone's terminal is a claim. A bundle lets a third party check
it without trusting you, without your database, and without your code being
the thing that says so.

```bash
clew evidence build --out bundle/ --plan plan.json --dsn "$CLEW_DSN" \
    --input graph.json --input samplesheet.csv --seal-into-log \
    --actor qa.lead@example.org
```

```bash
clew evidence verify bundle/
```

`verify` reads a directory. No database, no network, no credentials, no
driver. An assessor who does not trust the party that produced a bundle must
be able to check it anyway, and any step that routes through the producer's
infrastructure defeats that.

```
  ok   files      6 files, all hashes match
  ok   log        2 entries re-chain to the recorded head (seq 2)
  ok   policy     v2 matches the hash the plan cites
  ok   replay     all 57 verdicts recompute identically from the bundled facts and policy
  ok   signature  sealed by qa.lead@example.org
```

`replay` is the check that matters. A folder of documents proves only that
somebody assembled a folder. Replay re-derives every verdict from the
bundled facts and the bundled table, offline. Rebuild the manifest so the
hashes match and change only the conclusion, and it still fails:

```
  ok   files      6 files, all hashes match
  FAIL replay     1 discrepancies across 57 items; the plan does not reproduce:
                  da:06/31c01f: recorded ALREADY_GONE, recomputes to REGENERATE
```

Replay covers the whole plan, not only the settled lines. An undetermined
item's `possible` map is recomputed, so "one of three" cannot quietly
become "one of one". The header's `actions` counts and `tasks_affected`
are recomputed from the items. A fact recorded as `null` on any dimension
is unverified and evaluated over every value it could take, so a plan with
`terminal: null` cannot replay to `NOTIFY_ONLY`. A fact outside its
dimension's possible values, `"writable"` for `"WRITABLE"` say, is a
discrepancy rather than a silent fall-through.

`files` requires the directory to be exactly what the manifest lists. A
subdirectory, or any entry the manifest does not name, fails the check, and
a manifest naming a path outside the directory (`../`, a separator, `..`)
is refused before anything is hashed. For the same reason `build` refuses
a non-empty `--out` unless `--force`, which empties it first.

Bundles are clock-free. The same inputs produce the same bundle hash, and a
test asserts it. A timestamp inside would change the hash on every build and
destroy the reproducibility claim. Time lives in the log, and sealing is
itself a logged event. `--input` files are recorded in `inputs.json` by
content hash with the basename only, so `graph.json` and `./graph.json`
seal to the same bundle.

## Where the chain starts

The `log` check does not take the chain's starting point from the entries
themselves. A chain checked against its own first `prev_hash` verifies
whatever it was forged to say. The manifest records where the bundled
entries begin under `anchors.since`, and the verifier checks that:

- a bundle starting at seq 0 must chain from the genesis hash;
- a bundle built with `--since N` must name the bundle it continues with
  `--previous`, whose log head (seq N and its hash) is recorded under
  `anchors.previous_log_head`, and the entries must chain from that hash.

```bash
clew evidence build --out march/ --plan plan.json --dsn "$CLEW_DSN"
clew evidence build --out april/ --plan plan.json --dsn "$CLEW_DSN" \
    --since 57 --previous march/
```

`--since` without `--previous` is refused, and so is a `--previous` whose
head is not at that seq. At build time the previous head is also checked
against the live log, so a window sealed from a different log, or after a
rewrite, is refused rather than sealed into a bundle that can never verify.
A bundle whose manifest claims a log head but carries no `events.json`
fails the log check; it is not a bundle with one check fewer.

Manifests written before this (`clew_bundle_version` 1) record no start
and are read as starting from genesis. A version 1 bundle covering the
whole log verifies as before; a version 1 window does not, and never
should have on its own.

## Closing the log's open gap

A hash chain detects editing but not truncation. Cutting entries off the end
leaves a shorter, self-consistent chain, and nothing inside the database can
fix that. The fix has to be a witness its owner does not control.

The bundle records the log head it covered, and `--seal-into-log` records
the bundle hash back into the log. Neither can be rolled back without
contradicting the other:

```bash
clew evidence witness bundle/ --dsn "$CLEW_DSN"
```

```
$ clew log verify                # the log alone, after entries 2-3 were deleted
OK  1 entries, chain intact      # a short chain is a valid chain

$ clew evidence witness bundle/
FAIL witness   the log has no entry at seq 2, but this bundle recorded one.
               Entries have been removed from the end since this bundle was issued.
```

To make a truncation stick, someone would now have to collect every copy of
every bundle ever issued. `witness` is a separate command from `verify` on
purpose. Verifying needs no credentials and must stay that way.

## Signing is delegated

Clew seals: a SHA-256 manifest over every file, plus a bundle hash over the
manifest. Standard library only, so anyone can check it.

Clew does not implement signing. A signature checkable only by someone
holding the signing key is not a signature in the sense an assessor means,
and inventing cryptography here would be indefensible. Countersigning is
detached and uses `ssh-keygen -Y`, which ships with OpenSSH and whose keys
your organisation already manages:

```bash
clew evidence sign bundle/ --key ~/.ssh/id_ed25519
clew evidence verify bundle/ --allowed-signers allowed_signers
```

A signature from a key not in `allowed_signers` fails as "by someone this
reader has no reason to trust". The seal is Clew's. Who sealed it belongs to
your key infrastructure.

The bundle is also a valid RO-Crate, adopted rather than invented, so it
survives being handed to tooling that has never heard of Clew.
