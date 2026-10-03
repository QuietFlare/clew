"""
A written notice becomes a trigger, or is held for a person.

    clew triage --graph graph.json "toolkit 2.1 writes truncated indexes"
    clew triage --graph graph.json --notice-file advisory.txt --json triage.json
    clew triage --graph graph.json --print-request "..."       # ask elsewhere
    clew triage --graph graph.json --answer answer.json "..."  # record that answer
    clew triage --graph graph.json --dsn "$CLEW_DSN" "..."     # log both events

The options are the graph's own: every tool its containers name, every
external input, every label value, and none. A classifier picks one and
says how sure it is. Settings a person adopted decide what follows: ask
the trigger, hold the notice for a person, or dismiss it. Dismissing needs
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

from clew.graph import blast_radius as core
from clew.graph import triggers
from clew.graph.graph import container_match, parse_image
from clew.intake import classifier
from clew.intake.classifier import NONE, QUESTION, Refused
from clew.ledger import eventlog

RECEIVED = "NoticeReceived"
TRIAGED = "NoticeTriaged"

ASK, HELD, DISMISSED = "ask", "held", "dismissed"
EXIT = {ASK: 0, HELD: 1, DISMISSED: 3}

KINDS = ("container", "input", "label")

# One choice question takes 255 options, and none is one of them.
MOST_OPTIONS = 254

# First-guess bars from one trial: the only wrong answer scored 0.54 and the
# lowest right one 0.77. A site adopts its own in a settings file.
V1 = {
    "version": "v1",
    "description": "Clew's triage settings.",
    "model": "jev-1.13.0",
    "instructions": (
        "The notice reports a defect, a change or a withdrawal. Which of "
        "the things this run used does it concern? Choose none when it "
        "concerns nothing this run used."),
    "ask_at": 0.6,
    "dismiss_at": 0.9,
}

DEFAULT = V1


# ----------------------------------------------------------------- settings

class InvalidSettings(ValueError):
    """Settings that cannot be trusted to sort a notice."""


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


def options(graph, kinds=KINDS):
    """
    [(option, trigger, meaning)] for everything in the graph a notice could
    be about. Every trigger here resolves to at least one task, so a choice
    can always be asked. The option is the bare value where that names one
    thing, and `kind value` where two kinds share a word.
    """
    found = {}                                   # (kind, value) -> meaning

    if "container" in kinds:
        ran = {}
        for task in graph["tasks"].values():
            for tool in _tools(task.get("container") or ""):
                ran.setdefault(tool, set()).add(_step(task))
        for tool, steps in ran.items():
            found[("container", tool)] = f"tool in the image run by {_listed(steps)}"

    if "input" in kinds:
        read = {}
        for edge in graph["edges"]:
            if edge["producer"] == "EXTERNAL":
                name = Path(edge["filename"]).name
                consumer = graph["tasks"].get(edge["consumer"], {})
                read.setdefault(name, set()).add(_step(consumer))
        for name, steps in read.items():
            # An index or dictionary named `<file>.<ext>` is reached by the
            # trigger for the file it belongs to, so it is not offered twice.
            if not any(name.startswith(other + ".") for other in read):
                found[("input", name)] = f"input file read by {_listed(steps)}"

    if "label" in kinds:
        carried = set()
        for holder in list(graph["tasks"].values()) + list(graph["edges"]):
            carried.update((holder.get("labels") or {}).items())
        for key, value in carried:
            if key not in triggers.KINDS and isinstance(value, str) and value:
                found[(key, value)] = f"{key} recorded on work in this run"

    if len(found) > MOST_OPTIONS:
        raise SystemExit(
            f"this graph offers {len(found)} options and one question takes "
            f"{MOST_OPTIONS}. Narrow it with --kind.")

    shared = {}
    for kind, value in found:
        shared[value] = shared.get(value, 0) + 1
    return [(value if shared[value] == 1 and value != NONE else f"{kind} {value}",
             f"{kind}:{value}", meaning)
            for (kind, value), meaning in sorted(found.items())]


# ------------------------------------------------------------------ deciding

def request(notice, offered, settings):
    """The question as the classifier receives it. Printed by --print-request."""
    criteria = {option: meaning for option, _, meaning in offered}
    criteria[NONE] = "the notice concerns nothing this run used"
    return {
        "model": settings["model"],
        "state": {"notice": notice},
        "questions": {QUESTION: {"type": "choice",
                                 "instructions": settings["instructions"],
                                 "criteria": criteria}},
    }


def decide(answer, settings):
    """
    (outcome, reason) for one answer. An answer with no confidence came from
    name matching: a name found is worth asking about, and a name not found
    shows nothing, so that notice is held and never dismissed.
    """
    choice, confidence = answer["choice"], answer["confidence"]

    if confidence is None:
        named = answer.get("named") or []
        if choice != NONE:
            return ASK, "the notice names it"
        if named:
            return HELD, "the notice names several: " + ", ".join(named)
        return HELD, ("nothing in this run is named in the notice, and a "
                      "name match cannot show a notice is irrelevant")

    if choice == NONE:
        if confidence >= settings["dismiss_at"]:
            return DISMISSED, (f"none at {confidence:.2f}, at or above the "
                               f"dismissal bar {settings['dismiss_at']}")
        return HELD, (f"none at {confidence:.2f}, below the dismissal bar "
                      f"{settings['dismiss_at']}")
    if confidence >= settings["ask_at"]:
        return ASK, f"{confidence:.2f}, at or above the asking bar {settings['ask_at']}"
    return HELD, f"{confidence:.2f}, below the asking bar {settings['ask_at']}"


def notice_id(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def triage(notice, offered, settings, answer, backend):
    """The record of one notice: what was asked, what came back, what follows."""
    outcome, reason = decide(answer, settings)
    by_option = {option: trigger for option, trigger, _ in offered}
    asked = request(notice, offered, settings)
    return {
        "notice": {"sha256": notice_id(notice), "text": notice},
        "settings": identify(settings),
        "request_sha256": hashlib.sha256(
            eventlog.canonical(asked).encode("utf-8")).hexdigest(),
        "backend": backend,
        "model": answer["model"],
        "options": by_option,
        "choice": answer["choice"],
        "confidence": answer["confidence"],
        "probabilities": answer["probabilities"],
        "outcome": outcome,
        "reason": reason,
        "trigger": by_option[answer["choice"]] if outcome == ASK else None,
    }


# ------------------------------------------------------------------- events

def received(notice, source=None):
    """The event that says a notice arrived, written before anything reads it."""
    return {"event_type": RECEIVED, "subject": notice_id(notice),
            "body": {"text": notice, "source": source}}


def triaged(record, received_seq=None):
    """The event that says how it was sorted. The text stays in the first event."""
    body = {key: value for key, value in record.items() if key != "notice"}
    body["received_seq"] = received_seq
    return {"event_type": TRIAGED, "subject": record["notice"]["sha256"],
            "body": body}


# ---------------------------------------------------------------- the command

def show(record, graph_path):
    text = " ".join(record["notice"]["text"].split())
    print(f"notice   {record['notice']['sha256'][:12]}  "
          f"{text[:60]}{'...' if len(text) > 60 else ''}")
    counts = {}
    for trigger in record["options"].values():
        kind = trigger.split(":", 1)[0]
        counts[kind] = counts.get(kind, 0) + 1
    print("options  " + ", ".join(f"{n} {kind}" for kind, n in sorted(counts.items()))
          + ", and none")
    print(f"asked    {record['backend']}"
          + (f" {record['model']}" if record["model"] else "")
          + f", settings {record['settings']['version']} "
            f"{record['settings']['hash'][:12]}")
    sure = "" if record["confidence"] is None else f"  confidence {record['confidence']:.2f}"
    print(f"choice   {record['choice']}{sure}")
    print(f"outcome  {record['outcome']}: {record['reason']}")
    if record["trigger"]:
        print(f"\nnext: clew impact --graph {graph_path} --trigger {record['trigger']}")
    elif record["outcome"] == HELD:
        print("\nA person decides this one. Nothing was asked and nothing was dismissed.")


def read_notice(args):
    if args.notice_file:
        text = (sys.stdin.read() if args.notice_file == "-"
                else Path(args.notice_file).read_text())
    else:
        text = args.notice or ""
    text = text.strip()
    if not text:
        raise SystemExit("no notice: give the text, or --notice-file PATH, or - for stdin")
    return text


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="clew triage",
        description="Turn a written notice into a trigger, or hold it for a person.")
    parser.add_argument("notice", nargs="?", help="the notice text")
    parser.add_argument("--notice-file", metavar="PATH", help="read the notice from a file, - for stdin")
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
    parser.add_argument("--dsn", help="log the notice and its triage to the event log")
    parser.add_argument("--actor", default="clew-triage",
                        help="who the log says recorded it (default: clew-triage)")
    parser.add_argument("--source", help="where the notice came from, for the log")
    args = parser.parse_args(argv)

    notice = read_notice(args)
    try:
        settings = load(args.settings) if args.settings else DEFAULT
    except (InvalidSettings, OSError, ValueError) as bad:
        raise SystemExit(f"settings rejected: {bad}")
    graph = core.load_graph(args.graph)
    offered = options(graph, tuple(args.kind) if args.kind else KINDS)
    if not offered:
        raise SystemExit("this graph offers nothing a notice could be about")
    asked = request(notice, offered, settings)

    if args.print_request:
        print(json.dumps(asked, indent=2))
        return 0

    conn = received_seq = None
    if args.dsn:
        try:
            conn = eventlog.connect(args.dsn)
        except ImportError:
            raise SystemExit("logging needs psycopg: pip install 'clew-lineage[log]'")
        # Logged before anything reads it, so a notice that is later held or
        # dismissed, or whose triage fails, is still on the record.
        received_seq = eventlog.append(conn, actor=args.actor,
                                       **received(notice, args.source))["seq"]

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
            answer = classifier.checked(classifier.jev(asked, key),
                                        criteria, settings["model"])
        else:
            backend = "name"
            if args.backend is None:
                print(f"no {classifier.KEY_VARIABLE}: matching by name\n")
            words = {option: trigger.split(":", 1)[1] for option, trigger, _ in offered}
            answer = classifier.by_name(notice, words)
    except (Refused, OSError, ValueError) as bad:
        raise SystemExit(f"triage refused: {bad}")

    record = triage(notice, offered, settings, answer, backend)
    if conn is not None:
        eventlog.append(conn, actor=args.actor, **triaged(record, received_seq))

    show(record, args.graph)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(record, indent=2) + "\n")
        print(f"wrote {args.json_out}")
    return EXIT[record["outcome"]]


if __name__ == "__main__":
    sys.exit(main())
