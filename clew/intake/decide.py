"""
A person's decision on a held incident.

    clew decide --record triage.json --ask container:toolkit --actor qa.lead@example.org
    clew decide --record triage.json --dismiss --actor qa.lead@example.org --reason "another tool"
    clew decide --record triage.json --ask unit:U3 --actor qa.lead@example.org --dsn "$CLEW_DSN"

Triage holds an incident when the classifier is unsure, or when an id that
would be removed is not written out. A person then asks one of the
triggers triage offered, or dismisses the incident. Nothing else can be
asked: a trigger that was never offered was never checked against the run.

The decision is written beside the record as decision.json, and with
--dsn it is logged as IncidentDecided under the person's name, and asking a
removal also logs the fact itself, Withdrawn on that subject, so the gate
reads it with nobody typing it in.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from clew.ledger import eventlog

DECIDED = "IncidentDecided"
WITHDRAWN = "Withdrawn"
ASK, DISMISS = "ask", "dismiss"


class Refused(ValueError):
    """A decision that cannot be recorded as given."""


def decide(record, actor, ask=None, dismiss=False, reason="", at=None):
    """The decision on one held triage record, or Refused."""
    if not (actor or "").strip():
        raise Refused("a decision needs the name of the person who made it")
    if record.get("outcome") != "held":
        raise Refused(f"this incident was {record.get('outcome')}, not held; "
                      "only a held incident takes a decision")
    if bool(ask) == bool(dismiss):
        raise Refused("give one of --ask TRIGGER or --dismiss")
    if ask and ask not in record["options"].values():
        raise Refused(f"{ask} is not a trigger triage offered for this run")
    return {
        "incident": record["incident"]["sha256"],
        "decision": ASK if ask else DISMISS,
        "trigger": ask or None,
        "actor": actor.strip(),
        "reason": (reason or "").strip(),
        "decided_at": at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # What the person was looking at when they decided.
        "triage": {key: record.get(key) for key in
                   ("choice", "confidence", "reason", "request_sha256", "settings")},
    }


def decided(decision):
    """The event that puts the decision in the log, under the person's name."""
    return {"event_type": DECIDED, "subject": decision["incident"], "actor": decision["actor"],
            "body": {key: value for key, value in decision.items() if key != "actor"}}


def fact(decision, record, event_type=WITHDRAWN):
    """
    The fact a decision asserts, or None. Asking a removal says the subject
    is withdrawn, under the person's name, from the moment they decided. A
    trace or a dismissal asserts nothing about the run's inputs.
    """
    if decision["decision"] != ASK:
        return None
    kind, _, value = decision["trigger"].partition(":")
    if kind not in (record.get("named_only") or []):
        return None
    return {"event_type": event_type, "subject": value, "actor": decision["actor"],
            "effective_from": decision["decided_at"],
            "body": {"incident": decision["incident"], "trigger": decision["trigger"],
                     "reason": decision["reason"]}}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="clew decide", description="Record a person's decision on a held incident.")
    parser.add_argument("--record", required=True, metavar="PATH", help="the triage record")
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--ask", metavar="TRIGGER", help="ask this trigger, one that triage offered")
    choice.add_argument("--dismiss", action="store_true", help="the incident concerns nothing in this run")
    parser.add_argument("--actor", required=True, help="who decided")
    parser.add_argument("--reason", default="", help="why, in a sentence")
    parser.add_argument("--json", dest="json_out", metavar="PATH",
                        help="where to write the decision; default: decision.json beside the record")
    parser.add_argument("--dsn", help="log the decision, and the fact it asserts, to the event log")
    parser.add_argument("--fact", default=WITHDRAWN, metavar="TYPE",
                        help=f"the fact type a removal asserts in the log; default {WITHDRAWN}")
    args = parser.parse_args(argv)

    source = Path(args.record)
    target = Path(args.json_out) if args.json_out else source.with_name("decision.json")
    try:
        record = json.loads(source.read_text())
        if target.exists():
            raise Refused(f"{target} already holds a decision; a decision is not rewritten")
        decision = decide(record, args.actor, ask=args.ask, dismiss=args.dismiss, reason=args.reason)
    except (OSError, ValueError, KeyError) as bad:
        raise SystemExit(f"decision refused: {bad}")

    if args.dsn:
        try:
            conn = eventlog.connect(args.dsn)
        except ImportError:
            raise SystemExit("logging needs psycopg: pip install 'clew-lineage[log]'")
        eventlog.append(conn, **decided(decision))
        asserted = fact(decision, record, args.fact)
        if asserted:
            eventlog.append(conn, **asserted)
    target.write_text(json.dumps(decision, indent=2) + "\n")

    print(f"incident   {decision['incident'][:12]}")
    print(f"decided  {decision['decision']}" + (f" {decision['trigger']}" if decision["trigger"] else ""))
    print(f"by       {decision['actor']}" + (f": {decision['reason']}" if decision["reason"] else ""))
    asserted = fact(decision, record, args.fact)
    if asserted:
        print(f"fact     {asserted['event_type']} {asserted['subject']}"
              + ("" if args.dsn else "  (not logged: no --dsn)"))
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
