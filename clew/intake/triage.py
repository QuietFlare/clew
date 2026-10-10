"""
An incident report becomes a trigger, or is held for a person.

    clew triage --graph graph.json "toolkit 2.1 writes truncated indexes"
    clew triage --graph graph.json --incident-file advisory.txt --json triage.json
    clew triage --graph graph.json --print-request "..."       # ask elsewhere
    clew triage --graph graph.json --answer answer.json "..."  # record that answer
    clew triage --graph graph.json --dsn "$CLEW_DSN" "..."     # log both events

The options are the graph's own: every tool its containers name, every
external input, every label value, and none. A classifier picks one and
says how sure it is. Settings a person adopted decide what follows: ask
the trigger, hold the incident for a person, or dismiss it. Dismissing needs
more confidence than asking, because an unneeded plan is cheap and visible
and a missed problem is neither.

The answer is only ever a trigger. It enters `clew impact` as a typed one
would, and no verdict depends on the classifier.

Exit 0 with a trigger to ask, 1 when held, 3 when dismissed.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from clew.contracts import REMOVE, Adapter, discover
from clew.graph import blast_radius as core
from clew.graph import triggers
from clew.graph.graph import container_match, parse_image
from clew.intake import classifier
from clew.intake.classifier import NONE, QUESTION, Refused
from clew.ledger import eventlog

RECEIVED = "IncidentReceived"
TRIAGED = "IncidentTriaged"

ASK, HELD, DISMISSED = "ask", "held", "dismissed"
EXIT = {ASK: 0, HELD: 1, DISMISSED: 3}

# What an incident can be about: three things the graph records, and the kinds
# the pipeline's adapter declares.
KINDS = ("container", "input", "label", "pipeline")

# One choice question takes 255 options, and none is one of them.
MOST_OPTIONS = 254

# A kind with more values than this is offered only where the incident names
# the value. An id is a literal, and matching a literal needs no classifier.
NAMED_ONLY_ABOVE = 50

# First-guess bars from one trial: the only wrong answer scored 0.54 and the
# lowest right one 0.77. A site adopts its own in a settings file.
V1 = {
    "version": "v1",
    "description": "Clew's triage settings.",
    "model": "jev-1.13.0",
    "instructions": (
        "The incident report describes a defect, a change or a withdrawal. Which of "
        "the things this run used does it concern? Choose none when it "
        "concerns nothing this run used."),
    "ask_at": 0.6,
    "dismiss_at": 0.9,
}

DEFAULT = V1


# ----------------------------------------------------------------- settings

class InvalidSettings(ValueError):
    """Settings that cannot be trusted to sort an incident."""


def validate(settings):
    """Reject settings that could sort by accident; returns the settings."""
    if not isinstance(settings, dict):
        raise InvalidSettings("settings must be an object")
    for field in ("version", "model", "instructions"):
        if not isinstance(settings.get(field), str) or not settings[field].strip():
            raise InvalidSettings(f"settings need a non-empty {field}")
    if settings["model"] in classifier.ALIASES:
        raise InvalidSettings(
            f"model {settings['model']!r} is an alias that moves; name the "
            "build the bars were chosen against")
    for field in ("ask_at", "dismiss_at"):
        bar = settings.get(field)
        if isinstance(bar, bool) or not isinstance(bar, (int, float)) or not 0 <= bar <= 1:
            raise InvalidSettings(f"{field} must be a number from 0 to 1")
    return settings


def fingerprint(settings):
    """SHA-256 of the whole settings, wording included: the wording is the question."""
    return hashlib.sha256(eventlog.canonical(settings).encode("utf-8")).hexdigest()


def identify(settings):
    return {"version": settings["version"], "hash": fingerprint(settings)}


def load(path):
    return validate(json.loads(Path(path).read_text()))


# ------------------------------------------------------------------ options

def _listed(names, most=6):
    names = sorted(n for n in names if n)
    if not names:
        return "this run"
    shown = ", ".join(names[:most])
    return shown + (f" and {len(names) - most} more" if len(names) > most else "")


def _step(task):
    return (task.get("process") or "").rsplit(":", 1)[-1]


def _tools(image):
    """The names in one image that `container:` would match, coarsest first."""
    name = parse_image(image)[0]
    found = []
    for part in name.split("_"):
        if container_match(part, image):
            found.append(part)
        else:
            found += [p for p in re.split(r"[_-]+", part)
                      if p and container_match(p, image)]
    return found


ID_LIKE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*[A-Za-z0-9]")


def named_in_run(incident, graph):
    """
    The ids the incident writes out that this run's record also contains: in a
    task name, a file name or a label. An id is a word with a letter and a
    digit, so plain words and bare version numbers do not count.
    """
    ids = sorted({word for word in ID_LIKE.findall(incident)
                  if len(word) >= 3 and re.search(r"[A-Za-z]", word) and re.search(r"\d", word)})
    if not ids:
        return []
    recorded = []
    for task in graph["tasks"].values():
        recorded += [task.get("name") or "", task.get("process") or ""]
        recorded += [str(value) for value in (task.get("labels") or {}).values()]
    for edge in graph["edges"]:
        recorded.append(Path(edge.get("filename") or "").name)
        recorded += [str(value) for value in (edge.get("labels") or {}).values()]
    text = "\n".join(recorded)
    return [word for word in ids if classifier.names(text, word)]


def options(graph, kinds=KINDS, adapter=None, args=None, incident="", notes=None, unoffered=None):
    """
    [(option, trigger, meaning)] for everything an incident about this run
    could concern: what the graph records and what the pipeline's adapter
    declares. An adapter's kind shadows the engine's of the same name, as it
    does in impact, so whatever is offered here can be asked there. Every
    trigger resolves to at least one task. `notes` collects what was left
    out and why. The option is the bare value where that names one thing,
    and `kind value` where two kinds share a word.
    """
    notes = [] if notes is None else notes
    unoffered = [] if unoffered is None else unoffered
    declared = adapter.triggers if adapter is not None else {}
    depended_on = set(getattr(adapter, "load_bearing_inputs", None) or ())
    groups = {}                                  # kind -> {value: meaning}

    if "container" in kinds and "container" not in declared:
        ran = {}
        for task in graph["tasks"].values():
            for tool in _tools(task.get("container") or ""):
                ran.setdefault(tool, set()).add(_step(task))
        groups["container"] = {tool: f"tool in the image run by {_listed(steps)}"
                               for tool, steps in ran.items()}

    if "input" in kinds and "input" not in declared:
        read = {}
        for edge in graph["edges"]:
            if edge["producer"] == "EXTERNAL":
                name = Path(edge["filename"]).name
                consumer = graph["tasks"].get(edge["consumer"], {})
                read.setdefault(name, set()).add(_step(consumer))
        groups["input"] = {}
        for name, steps in read.items():
            # An index or dictionary named `<file>.<ext>` is reached by the
            # trigger for the file it belongs to, so it is not offered twice.
            if not any(name.startswith(other + ".") for other in read):
                what = "input file this pipeline depends on" if name in depended_on else "input file"
                groups["input"][name] = f"{what}, read by {_listed(steps)}"

    if "label" in kinds:
        for holder in list(graph["tasks"].values()) + list(graph["edges"]):
            for key, value in (holder.get("labels") or {}).items():
                if key not in triggers.KINDS and key not in declared \
                        and isinstance(value, str) and value:
                    groups.setdefault(key, {})[value] = f"{key} recorded on work in this run"

    if "pipeline" in kinds:
        for kind, declared_kind in declared.items():
            try:
                entries = declared_kind.resolve(graph, None, args)
            except (SystemExit, NotImplementedError, OSError) as bad:
                notes.append(f"{kind}: not offered. {bad}")
                unoffered.append(kind)
                continue
            about = declared_kind.about or f"{kind} of the {adapter.name} pipeline"
            reached = {value: nodes for value, nodes in entries.items() if nodes}
            if len(reached) < len(entries):
                notes.append(f"{kind}: {len(entries) - len(reached)} of {len(entries)} "
                             "reach no task in this run and are not offered")
            groups[kind] = {
                value: f"{about}, entering at "
                       f"{_listed(_step(graph['tasks'].get(node, {})) for node in nodes)}"
                for value, nodes in reached.items()}

    found = {}                                   # (kind, value) -> meaning
    for kind, values in groups.items():
        if len(values) > NAMED_ONLY_ABOVE:
            named = {value: meaning for value, meaning in values.items()
                     if classifier.names(incident, value)
                     or (kind == "input" and value in depended_on)}
            notes.append(f"{kind}: {len(values)} values, so only the {len(named)} "
                         "the incident names or the pipeline depends on are offered")
            values = named
        found.update({(kind, value): meaning for value, meaning in values.items()})

    if len(found) > MOST_OPTIONS:
        raise SystemExit(
            f"this run offers {len(found)} options and one question takes "
            f"{MOST_OPTIONS}. Narrow it with --kind.")

    shared = {}
    for kind, value in found:
        shared[value] = shared.get(value, 0) + 1
    return [(value if shared[value] == 1 and value != NONE else f"{kind} {value}",
             f"{kind}:{value}", meaning)
            for (kind, value), meaning in sorted(found.items())]


# ------------------------------------------------------------------ deciding

def request(incident, offered, settings):
    """The question as the classifier receives it. Printed by --print-request."""
    criteria = {option: meaning for option, _, meaning in offered}
    criteria[NONE] = "the incident concerns nothing this run used"
    return {
        "model": settings["model"],
        "state": {"incident": incident},
        "questions": {QUESTION: {"type": "choice",
                                 "instructions": settings["instructions"],
                                 "criteria": criteria}},
    }


def decide(answer, settings):
    """
    (outcome, reason) for one answer. An answer with no confidence came from
    name matching: a name found is worth asking about, and a name not found
    shows nothing, so that incident is held and never dismissed.
    """
    choice, confidence = answer["choice"], answer["confidence"]

    if confidence is None:
        named = answer.get("named") or []
        if choice != NONE:
            return ASK, "the incident names it"
        if named:
            return HELD, "the incident names several: " + ", ".join(named)
        return HELD, ("nothing in this run is named in the incident, and a "
                      "name match cannot show an incident is irrelevant")

    if choice == NONE:
        if confidence >= settings["dismiss_at"]:
            return DISMISSED, (f"none at {confidence:.2f}, at or above the "
                               f"dismissal bar {settings['dismiss_at']}")
        return HELD, (f"none at {confidence:.2f}, below the dismissal bar "
                      f"{settings['dismiss_at']}")
    if confidence >= settings["ask_at"]:
        return ASK, f"{confidence:.2f}, at or above the asking bar {settings['ask_at']}"
    return HELD, f"{confidence:.2f}, below the asking bar {settings['ask_at']}"


def incident_id(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def triage(incident, offered, settings, answer, backend, pipeline=None, notes=(), removes=(),
           named=(), unoffered=()):
    """
    The record of one incident: what was asked, what came back, what follows.
    `removes` names the kinds whose trigger takes a source away. Such an id
    must stand in the incident as written: a confident guess at who or what
    is removed is still a guess, so it is held whatever the confidence.

    A dismissal says the incident concerns nothing in this run, and it can
    only say that about what was offered. So it is refused when the incident
    writes out an id the run's record contains (`named`), or when a kind
    could not be offered at all (`unoffered`).
    """
    outcome, reason = decide(answer, settings)
    by_option = {option: trigger for option, trigger, _ in offered}
    if outcome == DISMISSED and named:
        outcome = HELD
        reason = (f"the incident names {', '.join(named)}, which this run's record contains, "
                  "and no option offered covers it. "
                  + ("Its kind may need a file the adapter was not given"
                     if pipeline else "Choose the pipeline's adapter so its own kinds are offered")
                  + ", so a person decides")
    elif outcome == DISMISSED and unoffered:
        outcome = HELD
        reason = (f"{', '.join(unoffered)} could not be offered, so none of the options "
                  "cannot rule it out and a person decides")
    if outcome == ASK:
        kind, _, value = by_option[answer["choice"]].partition(":")
        if kind in removes and not classifier.names(incident, value):
            outcome = HELD
            reason = (f"{value} is a {kind}, and the incident does not name it. Removing a "
                      f"{kind} needs its id as written, so a person decides")
    meanings = {option: meaning for option, _, meaning in offered}
    asked = request(incident, offered, settings)
    return {
        "incident": {"sha256": incident_id(incident), "text": incident},
        "settings": identify(settings),
        "request_sha256": hashlib.sha256(
            eventlog.canonical(asked).encode("utf-8")).hexdigest(),
        "backend": backend,
        "model": answer["model"],
        "pipeline": pipeline,
        "options": by_option,
        "notes": list(notes),
        "named_only": sorted(removes),
        "named_in_run": list(named),
        "choice": answer["choice"],
        # What the chosen option stood for, so the record says what was matched.
        "meaning": meanings.get(answer["choice"]),
        "confidence": answer["confidence"],
        "probabilities": answer["probabilities"],
        "outcome": outcome,
        "reason": reason,
        "trigger": by_option[answer["choice"]] if outcome == ASK else None,
    }


# ------------------------------------------------------------------- events

def received(incident, source=None):
    """The event that says an incident arrived, written before anything reads it."""
    return {"event_type": RECEIVED, "subject": incident_id(incident),
            "body": {"text": incident, "source": source}}


def triaged(record, received_seq=None):
    """The event that says how it was sorted. The text stays in the first event."""
    body = {key: value for key, value in record.items() if key != "incident"}
    body["received_seq"] = received_seq
    return {"event_type": TRIAGED, "subject": record["incident"]["sha256"],
            "body": body}


# ---------------------------------------------------------------- the command

def show(record, graph_path, flags=""):
    text = " ".join(record["incident"]["text"].split())
    print(f"incident   {record['incident']['sha256'][:12]}  "
          f"{text[:60]}{'...' if len(text) > 60 else ''}")
    counts = {}
    for trigger in record["options"].values():
        kind = trigger.split(":", 1)[0]
        counts[kind] = counts.get(kind, 0) + 1
    print("options  " + ", ".join(f"{n} {kind}" for kind, n in sorted(counts.items()))
          + ", and none")
    for note in record["notes"]:
        print(f"note     {note}")
    print(f"asked    {record['backend']}"
          + (f" {record['model']}" if record["model"] else "")
          + f", settings {record['settings']['version']} "
            f"{record['settings']['hash'][:12]}")
    sure = "" if record["confidence"] is None else f"  confidence {record['confidence']:.2f}"
    print(f"choice   {record['choice']}{sure}")
    print(f"outcome  {record['outcome']}: {record['reason']}")
    if record["trigger"]:
        print(f"\nnext: clew impact --graph {graph_path}{flags} --trigger {record['trigger']}")
    elif record["outcome"] == HELD:
        print("\nA person decides this one. Nothing was asked and nothing was dismissed.")


def read_incident(args):
    if args.incident_file:
        text = (sys.stdin.read() if args.incident_file == "-"
                else Path(args.incident_file).read_text())
    else:
        text = args.incident or ""
    text = text.strip()
    if not text:
        raise SystemExit("no incident: give the text, or --incident-file PATH, or - for stdin")
    return text


def installed_adapters():
    try:
        return discover(Adapter)
    except SystemExit:
        return {}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    adapters = installed_adapters()
    first = argparse.ArgumentParser(add_help=False)
    first.add_argument("--pipeline")
    adapter = adapters.get(first.parse_known_args(argv)[0].pipeline)

    parser = argparse.ArgumentParser(
        prog="clew triage", conflict_handler="resolve",
        description="Turn an incident report into a trigger, or hold it for a person.")
    parser.add_argument("incident", nargs="?", help="the incident text")
    parser.add_argument("--incident-file", metavar="PATH", help="read the incident from a file, - for stdin")
    parser.add_argument("--graph", required=True, help="graph JSON from an extractor")
    parser.add_argument("--kind", action="append", choices=KINDS,
                        help="offer only these kinds of option; repeatable")
    parser.add_argument("--settings", metavar="PATH",
                        help="triage settings as JSON; default: the shipped v1")
    parser.add_argument("--backend", choices=("jev", "name"),
                        help=f"default: jev when {classifier.KEY_VARIABLE} is set, else name")
    parser.add_argument("--print-request", action="store_true",
                        help="print the question and send nothing")
    parser.add_argument("--answer", metavar="PATH",
                        help="record an answer to that question made elsewhere")
    parser.add_argument("--json", dest="json_out", metavar="PATH",
                        help="write the triage record")
    parser.add_argument("--dsn", help="log the incident and its triage to the event log")
    parser.add_argument("--actor", default="clew-triage",
                        help="who the log says recorded it (default: clew-triage)")
    parser.add_argument("--source", help="where the incident came from, for the log")
    parser.add_argument("--pipeline", choices=sorted(adapters),
                        help="the adapter whose own trigger kinds are offered too")
    shared_flags = {action.dest for action in parser._actions}
    for kind in (adapter.triggers.values() if adapter else ()):
        kind.add_arguments(parser)
    args = parser.parse_args(argv)
    # What impact needs to answer the same trigger: the adapter and its own flags.
    flags = f" --pipeline {adapter.name}" if adapter else ""
    for action in parser._actions:
        if action.dest not in shared_flags and getattr(args, action.dest, None):
            flags += f" {action.option_strings[0]} {getattr(args, action.dest)}"

    incident = read_incident(args)
    try:
        settings = load(args.settings) if args.settings else DEFAULT
    except (InvalidSettings, OSError, ValueError) as bad:
        raise SystemExit(f"settings rejected: {bad}")
    graph = core.load_graph(args.graph)
    notes, unoffered = [], []
    offered = options(graph, tuple(args.kind) if args.kind else KINDS,
                      adapter=adapter, args=args, incident=incident, notes=notes, unoffered=unoffered)
    if not offered:
        raise SystemExit("this run offers nothing an incident could be about")
    asked = request(incident, offered, settings)

    if args.print_request:
        print(json.dumps(asked, indent=2))
        return 0

    conn = received_seq = None
    if args.dsn:
        try:
            conn = eventlog.connect(args.dsn)
        except ImportError:
            raise SystemExit("logging needs psycopg: pip install 'clew-lineage[log]'")
        # Logged before anything reads it, so an incident that is later held or
        # dismissed, or whose triage fails, is still on the record.
        received_seq = eventlog.append(conn, actor=args.actor,
                                       **received(incident, args.source))["seq"]

    criteria = asked["questions"][QUESTION]["criteria"]
    key = os.environ.get(classifier.KEY_VARIABLE)
    try:
        if args.answer:
            backend = "supplied"
            answer = classifier.checked(json.loads(Path(args.answer).read_text()),
                                        criteria, settings["model"])
        elif args.backend == "jev" or (args.backend is None and key):
            if not key:
                raise SystemExit(f"--backend jev needs {classifier.KEY_VARIABLE}")
            backend = "jev"
            answer = classifier.checked(classifier.jev(asked, key, classifier.endpoint()),
                                        criteria, settings["model"])
        else:
            backend = "name"
            if args.backend is None:
                print(f"no {classifier.KEY_VARIABLE}: matching by name\n")
            words = {option: trigger.split(":", 1)[1] for option, trigger, _ in offered}
            answer = classifier.by_name(incident, words)
    except (Refused, OSError, ValueError) as bad:
        raise SystemExit(f"triage refused: {bad}")

    removes = {kind for kind, declared in (adapter.triggers.items() if adapter else ())
               if declared.mode is REMOVE}
    record = triage(incident, offered, settings, answer, backend,
                    pipeline=adapter.name if adapter else None, notes=notes, removes=removes,
                    named=named_in_run(incident, graph), unoffered=unoffered)
    if conn is not None:
        eventlog.append(conn, actor=args.actor, **triaged(record, received_seq))

    show(record, args.graph, flags)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(record, indent=2) + "\n")
        print(f"wrote {args.json_out}")
    return EXIT[record["outcome"]]


if __name__ == "__main__":
    sys.exit(main())
