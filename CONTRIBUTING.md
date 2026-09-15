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

- A domain adapter for a pipeline you use, or an extractor for an engine
  Clew cannot read. Both are one subclass in your own package, found by
  name. [docs/providers.md](docs/providers.md) walks through each with
  examples. A regression test pinning real numbers from a real run is
  what makes an adapter trustworthy.
- A bug report with a graph. A wrong blast radius is the most serious class
  of bug here, above all one that reports something as unaffected when it
  is not. Attach the graph JSON if you can share it.

## Ground rules for code

- Explain why before how. Comments carry the reasoning, not the mechanics.
- Clear, boring code over clever abstractions.
- Stdlib first. Clew has no runtime dependencies and intends to keep it
  that way.
- `clew/graph/` and `clew/ledger/` hold no domain vocabulary. No samples,
  donors, consent, or workflow engines. Those live in `clew/domains/`.
- Packages import downward only. `clew/graph/` imports nothing from clew.
  `tests/test_core_boundary.py` enforces both rules.
- New behaviour comes with a test. Regression fixtures from real runs are
  preferred over synthetic ones where the data can be shared.
- No AI in the decision path. Models may propose. The deterministic core
  decides. An auditor asking why something was flagged must get a policy
  version, hashes, and a re-run that agrees.

The engine and the six providers under `providers/` are separate
distributions. Install all seven editable first, or nothing registers:

```bash
make dev
```

Pass `PYTHON=` if the first `python3` on your PATH is not the one `clew`
runs under.

Run every suite before opening a pull request. The engine's tests are in
`tests/`; each provider's are in its own `tests/`, beside its fixtures:

```bash
make test
```

## Licensing of contributions

Clew is licensed under [AGPL-3.0](LICENSE). By submitting a contribution
you agree that it is your own work, or that you have the right to submit
it, and that it is licensed under the same AGPL-3.0 terms as the rest of
the project. There is no separate agreement to sign.
