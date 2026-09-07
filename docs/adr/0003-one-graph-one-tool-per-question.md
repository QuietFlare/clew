# ADR 0003: One graph, one tool per question

Status: accepted

## Context

Clew began as a single command answering a single question. New questions
arrived, reclaim, drift, and each could have grown inside the first, or
become a separate product with its own reading of the engine's record.
Both roads lead to tools that disagree about the same run.

## Decision

Clew is a stack. Recorders at the bottom, one per engine, all emitting the
same graph. One graph in the middle, `clew.graph`, which is also the
public API. Questions on top, one module each under `questions/`, reading
the graph and nothing else. Views render what a question produced. A
question never reaches into a recorder or into another question.

## Consequences

A new question is one module and one page. Any question that imports only
`clew.graph` can be lifted into its own package later without touching
the rest, so the split is an option held rather than a decision made. The
cost is a stable graph API: names in `clew.graph` cannot be changed
freely once a question depends on them.
