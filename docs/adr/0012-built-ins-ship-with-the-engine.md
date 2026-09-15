# ADR 0012: The built-in providers ship with the engine

Status: accepted

## Context

ADR 0010 made each built-in provider its own distribution, six beside the
engine, so that the check "could a third party build this" was the
built-ins themselves. The boundary it drew held: a provider imports
`clew.graph`, `clew.contracts` and `clew.extract`, never a question and
never another provider, and registers through an entry point.

The packaging did not earn its cost. Six PyPI projects, six trusted
publishers, seven version strings that can drift, extras that point at
packages not yet published, and a checkout that discovers nothing until
seven editable installs succeed. Each provider is a few stdlib files;
nobody needs one without the engine.

## Decision

One distribution. The providers live at `clew/provider/<name>/` inside
`clew-lineage` and are declared as entry points in its `pyproject.toml`.
`clew` and `clew.provider` stay namespace packages, so a third party's
package installs into `clew.provider.<theirs>` beside the built-ins and
declares the same groups. `clew providers` lists both, each with the
distribution it came from.

The boundary test is unchanged in what it holds: providers reach only the
public surface, no namespace level carries an `__init__.py`, every entry
point registers, and every provider directory is declared.

## Consequences

`pip install clew-lineage` is the whole tool. The extras `[nextflow]`,
`[all]` and the rest are gone; they never reached a release. A checkout
is one `pip install -e .`, and one suite under `tests/` with the provider
fixtures beside the engine's. Releasing is one tag.

What the mechanism proves is unchanged: the built-ins register exactly as
an outside package would, and the test that holds them to the public
surface is what makes the claim, not where they are published.
