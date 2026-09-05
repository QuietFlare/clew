# Storage is checked, never assumed

Every verdict depends on two kinds of fact, and they are not the same kind.

Lineage is what was derived from what. It is permanently true, and it is
what the graph holds.

Storage is whether the bytes are still there. It is true only at the instant
you look, and it is not in the graph at all.

Whoever asks Clew a question is often not standing where the pipeline ran.
It might be a different host, a CI runner, or a laptop reading a graph
someone emailed over. So Clew does not check unless you tell it where to
look:

```bash
clew impact --graph graph.json --samplesheet samplesheet.csv \
    --subject donor_003 --work-root /path/to/work
```

Without `--work-root`, storage is unverified and any verdict that depends on
it comes back `UNDETERMINED` rather than guessed. Verdicts that hold
whatever the disk says are still returned. Under policy v2 a published
artifact is `NOTIFY_ONLY` either way, and that is an answer, not a guess:

```
  UNDETERMINED  (57)  no verdict; see below
    storage state not verified, and the verdict depends on it. Verifying
    would decide between ALREADY_GONE, DESTROY or QUARANTINE. Refusing to
    guess: assuming the artifact survives over-claims work, and assuming it
    is gone reports an obligation as already discharged.
```

With it, Clew looks and reports what it found. On the cross-run graph, 46
tasks whose scratch really was cleaned and 11 whose artifacts are still
there:

```
AFFECTED: 57 of 183 tasks
  ALREADY_GONE  (46)  no longer exists; nothing to do
  REGENERATE    (11)  recompute from the remaining sources
```

## Why `ALREADY_GONE` requires looking

`ALREADY_GONE` is only ever reached by looking and not finding. An earlier
version returned it whenever the recorded path failed to resolve, which
fired identically when the volume was not mounted, when the graph came from
another machine, when the extractor never recorded a work directory, and
when published fixtures had their paths anonymised. Every one of those
produced "nothing to do", silencing exactly the artifacts that carry
obligations. A false negative that discharges an obligation deserves far
more care than a false positive that wastes work.

Recorded paths belong to whichever machine ran the pipeline, so only the
two-character prefix and task hash are joined onto the root you supply. A
graph stays portable between hosts without pretending its absolute paths
mean anything locally.

An undetermined item is not clean, it is unanswered. It carries the set of
verdicts still in play, so a CI gate can fail on it and a reader can see
what checking the disk would settle.
