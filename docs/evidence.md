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
  FAIL replay     1 of 57 verdicts do not reproduce:
                  da:06/31c01f: recorded ALREADY_GONE, recomputes to REGENERATE
```

Bundles are clock-free. The same inputs produce the same bundle hash, and a
test asserts it. A timestamp inside would change the hash on every build and
destroy the reproducibility claim. Time lives in the log, and sealing is
itself a logged event.

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
