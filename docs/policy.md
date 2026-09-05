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
  R3   exclusive=True, storage=WRITABLE                     -> DESTROY
       Exists only because of this subject and the bytes can be changed.
       Nothing else needs it, so it goes entirely.
```

Every plan names the policy in its header and every line cites the rule that
decided it. "Policy v1, rule R5, these hashes, re-run and get the same
answer" is a checkable sentence.

## Rule order is semantics

First match wins, and an omitted dimension is a wildcard. That is the whole
difference between the two shipped versions.

```bash
clew rulebook diff v1 v2
```

```
order  v1: R1 R2 R3 R4 R5 R6 R7 R8
       v2: R2 R1 R3 R4 R5 R6 R7 R8

  R2  position 2 -> 1
      - Immutable history: published, or already past a trust boundary.
        Terminates remediation, not notification: you cannot unpublish...
      + Immutable history: published, or already past a trust boundary.
        Asked first, before existence: destroying our copy does not reach
        the published or transferred one, so the obligation to disclose
        survives the bytes.
```

v1 asked "does it still exist?" before "was it published?", so a published
artifact whose working copy had been deleted came back `ALREADY_GONE`.
Deleting your copy of something does not unpublish it.

The consequence was sharper than it looks. `R1` is the only rule that can
yield `ALREADY_GONE`, so putting it first made every verdict depend on the
storage state. Under v1 nothing was decidable without a disk check. Under v2
a published artifact resolves without one, because the answer does not
depend on it:

```bash
clew impact --graph clew/data/graph5.json --samplesheet clew/data/donors.csv \
    --subject donor_003 --assertions clew/data/assertions.json --policy v1
```

```
POLICY: v1  dbb59de6d85fc0f8       POLICY: v2  e6ba60ffe6763949
  UNDETERMINED  (16)                 NOTIFY_ONLY   (1)   c9/023b13  MULTIQC
    c9/023b13  MULTIQC               UNDETERMINED (15)
```

## Old versions stay, byte for byte

`--policy v1` still resolves, and a plan computed in January replays under
the table that produced it rather than under today's. A semantic change is a
new version, never an edit. The shipped hashes are frozen as literals in the
test suite, so editing one fails the build and says to add a version
instead.

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
counts as published, and what a given withdrawal tier may reach are a
separate object that lives in `domains/`.
