# Policy versioning

Every verdict is a verdict under a table. If that table is an if-ladder in
Python, editing it silently reinterprets every plan ever produced. A plan
from March says `QUARANTINE`, the code says `QUARANTINE` today, and nobody
can tell whether it said `QUARANTINE` in March. The history becomes
unfalsifiable, which is the same as worthless.

So the table is data, with a version and a content hash:

```bash
clew rulebook show
```

```
  R3   scope=exclusive, storage=WRITABLE                   -> DESTROY
       Exists only because of this subject and the bytes can be changed.
       Nothing else needs it, so it goes entirely.
```

Every plan names the policy in its header and every line cites the rule that
decided it. "Policy v1, rule R5, these hashes, re-run and get the same
answer" is a checkable sentence.

## Rule order is semantics

First match wins, and an omitted dimension is a wildcard. Release is asked
first: a released artifact is `NOTIFY_ONLY` whatever the disk says, because
deleting our copy does not reach the released one. Existence is asked
second, so nothing else is decidable without a storage check, and Clew
withholds rather than guesses. The mode is asked before purge: a separable
part that was corrected (`trace`) is recomputed, R5, while one that was
removed (`remove`) is cut out in place, R6.

```bash
clew impact --graph clew/data/graph5.json --trigger patient:donor_003 \
    --samplesheet clew/data/donors.csv --assertions clew/data/assertions.json
```

```
POLICY: v1  f1f49f91c8a49f7e
  NOTIFY_ONLY   (1)   c9/023b13  MULTIQC
  UNDETERMINED (15)
```

## Versions are immutable

A plan cites the table by version and hash, so a plan computed in January
replays under the table that produced it. A semantic change is a new
version, never an edit. The shipped hash is frozen as a literal in the test
suite, so editing the table fails the build and says to add a version
instead. Only `v1` ships today.

Adoption is a logged fact. `clew rulebook register` writes a
`PolicyAdopted` event carrying the whole table, not a pointer to it. A
pointer to code is worthless six months and four releases later.

```bash
clew rulebook register --dsn "$CLEW_DSN" --actor qa.lead@example.org
```

## Validation refuses rather than warns

A policy naming an unknown action, an impossible value, a duplicate rule id,
or a rule with no rationale is rejected at load. The case that matters most
is a mistyped dimension, which would otherwise load cleanly and never match.
A rule that never matches is indistinguishable from a deleted one, except
that the file still shows it and everyone believes it applies.

Two guards sit outside the rule list where no policy can reach them. An
unrecognised contribution class becomes `IRREDUCIBLE` before matching, and
falling off the end of the rules yields `QUARANTINE` rather than an error or
a pass.

## What versioning guarantees

It fixes the facts, not the verdict. A policy mapping `IRREDUCIBLE` to
`PURGE` is expressible, would be wrong, and Clew will run it. That is the
reason the table is data. Wrong logic in an if-ladder is invisible in a code
review nobody does. Wrong logic in a hashed, versioned file with a rationale
sits in the open with a rule id on it.

This is the core table, not a customer's policy. It defines what the classes
mean, so changing it changes the semantics of every historical plan, which is
why it is versioned. Which of a customer's events map to which class, what
counts as released, and what a given trigger may reach are the
adapter's, under `providers/`.
