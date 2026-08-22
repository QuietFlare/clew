# Contributing to Clew

Thanks for looking. Issues, questions and pull requests are all welcome —
particularly from people who run pipelines for a living and can tell me
where the model is wrong.

## What is most useful right now

Clew is early. The single most valuable contribution is not code: it is
telling me how invalidation actually plays out in your lab. What triggered
it, what you had to find, what you could not find, and what you were
required to prove afterwards. The domain layer is designed to be wrong and
cheap to replace; it improves by contact with practitioners, not by more
thinking.

After that, in rough order:

- **A domain adapter for a pipeline you use.** See `domains/` — a new
  adapter is usually a few dozen lines on top of `domains/nfcore.py`, plus
  a regression test pinning real numbers from a real run.
- **Bug reports with a graph.** A blast radius that is wrong is the most
  serious class of bug here, especially one that reports something as
  unaffected when it is not. Attach the graph JSON if you can share it.
- **Ingest paths.** Other engines, other provenance formats.

## Ground rules for code

- **Explain why before how.** Comments carry the reasoning, not the
  mechanics.
- **Clear, boring code over clever abstractions.**
- **Stdlib first.** Clew has no runtime dependencies and would like to
  keep it that way.
- **`core/` holds no domain vocabulary.** No samples, donors, consent, or
  workflow engines — those live in `domains/`. There is a grep in the
  README that must keep returning nothing.
- **New behaviour comes with a test.** Regression fixtures from real runs
  are preferred over synthetic ones where the data can be shared.
- **No AI in the decision path.** Models may propose; the deterministic
  core disposes. An auditor asking "why was this flagged?" must be
  answerable with a policy version, hashes, and a re-run.

Run the suite before opening a PR:

```bash
python3 -m unittest discover -s tests
```

## Contributor Licence Agreement

Clew is licensed under AGPL-3.0. Before your first pull request can be
merged, you need to agree to the Contributor Licence Agreement below. It
is short, and it exists for one boring reason: without it, relicensing —
including any future dual-licensing — would require unanimous agreement
from every past contributor, which in practice means it can never happen.

**To agree**, add a comment to your first pull request saying:

> I have read the CLA in CONTRIBUTING.md and I agree to it.
> Signed: <your name>, <your GitHub username>, <date>

### The agreement

By contributing to this project, you certify that:

1. **You wrote it, or you have the right to submit it.** The contribution
   is your original work, or you have permission from the copyright holder
   to submit it under these terms. If your employer has rights to work you
   produce, you have their permission to contribute.

2. **You grant a copyright licence.** You grant the project maintainer a
   perpetual, worldwide, non-exclusive, royalty-free, irrevocable licence
   to reproduce, modify, distribute and sublicense your contribution,
   **including the right to distribute it under licences other than
   AGPL-3.0**.

3. **You grant a patent licence.** You grant the same parties a perpetual,
   worldwide, non-exclusive, royalty-free, irrevocable patent licence to
   make, use, sell and otherwise transfer your contribution, covering
   patent claims you own that are necessarily infringed by it.

4. **You keep your copyright.** You are not assigning ownership. You keep
   every right you already had in your contribution, including the right
   to use it elsewhere however you like.

5. **It is provided as-is.** Unless required by law, you provide your
   contribution without warranties or conditions of any kind.

If you cannot agree to this, please still open an issue — a description of
a bug or a missing case is a genuinely useful contribution and needs no
agreement at all.
