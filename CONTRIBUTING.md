# Contributing to Clew

Issues, questions and pull requests are welcome, particularly from people
who run pipelines for a living and can say where the model is wrong.

## What helps most

The most valuable contribution is not code. It is an account of how an
invalidation played out in your lab: what triggered it, what you had to
find, what you could not find, and what you were required to prove
afterwards. The domain layer is designed to be wrong and cheap to replace,
and it improves by contact with practitioners.

After that, in rough order:

- A domain adapter for a pipeline you use. See `clew/domains/`. A new
  adapter is usually a few dozen lines on top of `nfcore.py`, plus a
  regression test pinning real numbers from a real run.
- A bug report with a graph. A wrong blast radius is the most serious class
  of bug here, above all one that reports something as unaffected when it
  is not. Attach the graph JSON if you can share it.
- An extractor for another engine or provenance format. Every extractor
  emits the same graph JSON, so `clew/extract_from_horus.py` is a good
  model.

## Ground rules for code

- Explain why before how. Comments carry the reasoning, not the mechanics.
- Clear, boring code over clever abstractions.
- Stdlib first. Clew has no runtime dependencies and intends to keep it
  that way.
- `clew/core/` holds no domain vocabulary. No samples, donors, consent, or
  workflow engines. Those live in `clew/domains/`, and
  `tests/test_core_boundary.py` enforces it.
- New behaviour comes with a test. Regression fixtures from real runs are
  preferred over synthetic ones where the data can be shared.
- No AI in the decision path. Models may propose. The deterministic core
  decides. An auditor asking why something was flagged must get a policy
  version, hashes, and a re-run that agrees.

Run the suite before opening a pull request:

```bash
python3 -m unittest discover -s tests
```

## Licensing of contributions

Clew is licensed under [AGPL-3.0](LICENSE). By submitting a contribution
you agree that it is your own work, or that you have the right to submit
it, and that it is licensed under the same AGPL-3.0 terms as the rest of
the project. There is no separate agreement to sign.
