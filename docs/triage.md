# Triage: an incident report becomes a question

Most changes arrive as a sentence, not a trigger: a tool advisory, a
release note, a withdrawal. Triage turns the sentence into one of the
triggers this run can answer, or holds it.

```bash
clew triage --graph graph.json --json triage.json "the duplicate marking step flags optical duplicates wrongly"
```

The options offered are the run's own: every tool its containers name,
every outside input, every label, and the kinds a pipeline adapter
declares. A classifier picks one and says how sure it is. Versioned
settings then decide: ask the trigger, hold the incident for a person,
or dismiss it. Dismissing needs more confidence than asking, and is
refused outright when the sentence names an id the run's record
contains. With `TYPESAFE_API_KEY` set the classifier is TypeSafe's Jev;
without it, names are matched. The record carries the backend, the model
and the hash of the request, so the same question can be asked again.

A held incident waits for a person, who decides under their own name:

```bash
clew decide --record triage.json --ask container:gatk4 --actor "qa lead" --reason "the advisory names our version"
```

Asking a removal, `patient:donor_003` say, asserts the withdrawal as a
fact. With a log attached it is written there, under the person's name,
and the gate reads it. Nobody types a withdrawal in by hand.

Exit codes say what happened: 0 asks, 1 holds, 3 dismisses. A pipeline
plugin can do the asking itself with `--print-request` and `--answer`.

The kinds a trigger can take, and where they come from, are in
[triggers.md](triggers.md). The event log a decision is written to is in
[event-log.md](event-log.md).
