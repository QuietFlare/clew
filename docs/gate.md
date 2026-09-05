# The CI gate

Impact analysis answers after the fact. The gate asks the opposite question
before anything runs: is any of this material something we are not allowed
to use?

```bash
clew gate --pipeline sarek --samplesheet samplesheet.csv \
    --dsn "$CLEW_DSN" --gate-policy gate-policy.json --out clew-evidence/
```

```
CLEW GATE  clew/data/donors.csv
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

Exit 1 stops the build. Compliance becomes a build check, at the point where
stopping is cheap.

## Three outcomes, not two

`BLOCKED`, `CLEARED`, and `UNKNOWN`, where the log has nothing to say about
the subject at all. A gate that reports unknown as "not blocked" passes
everything it failed to check, and goes green the day someone mistypes an
identifier or points it at the wrong log. So unknown stops the build unless
`--allow-unknown` says somebody decided otherwise, and even then it is
counted and reported as unknown rather than relabelled clean.

The report also says so outright when the log had never heard of any
subject in the samplesheet. That almost always means the identifiers do not
match between the two, not that everything is permitted.

## Every way of failing to check exits non-zero

| Condition | Result |
|---|---|
| the log is unreachable | stop, an unreachable log is not a clean one |
| no blocking types given | stop, that is no gate at all |
| a subject is `UNKNOWN` | stop, unless explicitly allowed |
| a subject is `BLOCKED` | stop |

A green build must mean checked and permitted. If it can also mean could not
check, the gate is decorative, and a decorative compliance gate is worse
than none because it manufactures a record of diligence that did not happen.

## Decisions get reversed, and dates matter

For each subject the latest fact in effect decides, ordered by
`effective_from`, when the decision was made in the world rather than when
it was entered. A subject withdrawn in March and reinstated in June is
usable in July. Same-day facts are broken by log order, the one tiebreak
nobody can backdate.

That makes "was this run permitted when we ran it?" answerable:

```bash
clew gate ... --as-of 2026-05-01
```

```
as of 2026-05-01          as of today
  BLOCKED  3                BLOCKED  1
  CLEARED  0                CLEARED  2
```

Facts effective after the date asked about are ignored, so a historical
gate result stays reproducible instead of changing every time someone
records something new. This is what the log's two clocks are for.

## It emits its own evidence

`--out` seals the result into a bundle, and `clew evidence verify`
re-derives the gate decision from the bundled facts and the bundled gate
policy, the same discipline as replaying a remediation plan:

```
  ok   files      6 files, all hashes match
  ok   log        5 entries re-chain to the recorded head (seq 5)
  ok   gate       passed=False; all 5 subject outcomes recompute identically
```

The shipped workflow at [examples/clew-gate.yml](../examples/clew-gate.yml)
uploads that bundle with `if: always()`. The evidence of a refusal matters
at least as much as the evidence of a pass. An artifact that only survives
on green builds documents the days nothing was wrong.

The gate runs with read-only credentials. It asks questions and has no
reason to hold a role that can write facts. CI is the last place to put one
that can.

Clew ships
[gate-policy.example.json](../clew/data/gate-policy.example.json) as a
template, not a recommendation. Which of your event types should stop work
is yours to author and yours to defend.
