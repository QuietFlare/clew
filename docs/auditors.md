# For auditors: a dashboard and an MCP server

Everything else in Clew produces evidence. These two surfaces read it. Both
go through the same core layer, `core/bundlestore.py` to load bundles and
`core/query.py` to answer, because two surfaces answering the same question
two different ways would eventually disagree, and on that day nobody could
say which was wrong. A test asserts that neither surface imports the other.

## A page you can open from a USB stick

```bash
clew dashboard --bundles /path/to/bundles --out evidence.html
```

One self-contained HTML file. No server, no network, no scripts, and it
prints legibly. It carries no timestamp, so regenerating it from unchanged
bundles produces an identical file. Two auditors comparing pages should be
comparing evidence, not diffing dates.

What is not known sits above the findings, deliberately. A compliance
dashboard that renders its gaps in small grey text below the fold
manufactures the impression of a clean bill of health out of an incomplete
record. Withheld verdicts and subjects unknown to the log get counters of
their own.

The page says outright that it is not the record. The bundles are, and
every panel names the bundle hash it was drawn from.

## An MCP server, so an auditor can ask

```bash
clew mcp --bundles /path/to/bundles
```

MCP over stdin and stdout, stdlib JSON-RPC, no SDK. Point any MCP client at
it.

| Tool | Answers |
|---|---|
| `list_bundles` | what evidence exists at all |
| `subject_history` | every recorded fact about one subject, both clocks, actors |
| `policy_in_force` | which table the log says applied on a date |
| `verdict` | why one task got the verdict it did: rule id, rationale, chain |
| `was_affected` | whether this output used that material |
| `check_integrity` | the deterministic verifier's own output |
| `gate_result` | what a pre-flight gate blocked, and on what basis |

Clew ships no model and calls none. It exposes tools, and the auditor's own
client supplies the conversation. Every verdict was computed before the
server started, by code that has never seen a prompt, so a model cannot talk
the tools into a different answer.

The server is read-only by construction. No tool writes anything, and the
server never opens a database connection. It reads sealed bundles. Recording
a fact is `clew log`, run by a person with an actor identity, and an
auditor's chat session is the last place a new fact should be able to enter
a compliance record. A test asserts the server module contains no connection
call.

Every answer carries its citations, and `query.answer()` refuses to return
without them:

```json
{ "kind": "log_entry", "seq": 1, "hash": "01fd6803a761d58a…",
  "actor": "registry@example.org", "effective_from": "2026-03-01" }
```

That matters because the consumer is a language model, and an auditor
cannot tell fluent-and-wrong from fluent-and-right by reading it. Facts
welded to their citations make a bad paraphrase checkable. The guardrail the
server hands the model at startup says so: quote the citations, read the
coverage out loud, and never conclude compliance. There is no tool that says
an obligation was met, and the model must not supply one.

That is a guardrail, not a guarantee. Treat the prose as a convenience and
the citations as the record.

## A log has no identity yet

Two logs both number their entries from 1. Merging bundles sealed from
different logs would interleave two unrelated histories into one plausible
timeline, and no field distinguishes them. So the loader compares what must
agree if the logs are the same, one hash per sequence number, and says so
loudly when they do not:

```
THESE BUNDLES DISAGREE: 2 sequence numbers (including 1, 2) carry different
entries in different bundles, which means they were sealed from DIFFERENT
LOGS. Answers drawn from the combined history are not trustworthy.
```

The warning travels on every answer drawn from the merged entries, and the
dashboard renders it as a stop panel above everything else. Giving the log a
real identity is the proper fix and is not built.
