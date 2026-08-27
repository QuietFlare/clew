"""
Clew — the event log, from the command line.

    # once, as the database owner: table, guards, and the two roles
    clew log --dsn "$CLEW_ADMIN_DSN" init \
        --writer-password "$W" --auditor-password "$A"

    # thereafter, as the writer role, which holds INSERT and SELECT only
    clew log --dsn "$CLEW_DSN" append --type ContainerDefectReported \
        --subject "gatk4:4.2.1" --actor qa.lead@example.org \
        --effective-from 2026-08-10T00:00:00+00:00 \
        --body '{"defect":"BQSR miscalibration","reference":"JIRA QA-4471"}'

    # by anyone, including someone who does not trust us
    clew log --dsn "$CLEW_AUDIT_DSN" verify

THREE DSNs, THREE IDENTITIES
----------------------------
That is not ceremony. The owner can drop a trigger; the writer cannot edit;
the auditor cannot write. If the pipeline runs as the owner, the separation
that makes this log worth having is gone, and no amount of Clew code can put
it back. Use different credentials, and keep the owner's out of CI.

WHY A SEPARATE ENTRY POINT FROM clew impact
----------------------------------------
Recording a fact and computing over it are different acts by different people
at different times. A coordinator enters a withdrawal months before anyone
runs a blast radius against it. Collapsing the two into one command would
imply they happen together, and would quietly invite writing a fact into the
log as a side effect of asking a question about it. Facts get their own door.

EVENT TYPES ARE NOT VALIDATED HERE, ON PURPOSE
----------------------------------------------
--type takes any string. Core defines no vocabulary; a domain adapter decides
what its types mean. Rejecting an unknown type here would put a domain's
vocabulary in core's entry point, which is the boundary this project exists
to keep. What the log guarantees is that whatever was written stays written.
"""

import argparse
import json
import os
import sys
from pathlib import Path


from clew.core import eventlog


def cmd_init(conn, args):
    result = eventlog.init(conn, writer=args.writer, auditor=args.auditor,
                           writer_password=args.writer_password,
                           auditor_password=args.auditor_password)
    print(f"database {result['database']}, schema {result['schema']}")
    print(f"  {result['writer']:<16} SELECT, INSERT   (cannot UPDATE, DELETE "
          f"or TRUNCATE — never granted, and cannot self-grant)")
    print(f"  {result['auditor']:<16} SELECT")
    print()
    print("Connect the pipeline as the writer. Keep the owner's credentials")
    print("out of CI: the owner can drop the triggers, and that is precisely")
    print("the privilege this separation exists to withhold.")


def cmd_append(conn, args):
    body = json.loads(args.body) if args.body else {}
    entry = eventlog.append(conn, event_type=args.event_type,
                            subject=args.subject, body=body, actor=args.actor,
                            effective_from=args.effective_from)
    print(f"seq {entry['seq']}  {entry['event_type']}  {entry['subject']}")
    print(f"  effective from  {entry['effective_from']}")
    print(f"  recorded at     {entry['recorded_at']}")
    print(f"  asserted by     {entry['actor']}")
    print(f"  hash            {entry['hash']}")
    print(f"  follows         {entry['prev_hash']}")


def cmd_list(conn, args):
    entries = eventlog.read(conn, since=args.since, event_type=args.event_type,
                            subject=args.subject)
    if args.json:
        print(json.dumps(entries, indent=2, sort_keys=True))
        return
    if not entries:
        print("log is empty")
        return
    print(f"{'seq':>4}  {'effective':<25} {'type':<24} {'subject':<20} "
          f"{'actor':<24} hash")
    for entry in entries:
        print(f"{entry['seq']:>4}  {entry['effective_from']:<25} "
              f"{entry['event_type']:<24} {entry['subject']:<20} "
              f"{entry['actor']:<24} {entry['hash'][:12]}")
        if entry["body"]:
            print(f"      {json.dumps(entry['body'], sort_keys=True)}")


def cmd_verify(conn, args):
    result = eventlog.verify(conn)
    if result["ok"]:
        print(f"OK  {result['entries']} entries, chain intact")
        print(f"head {result['head']}")
        print()
        print("This proves no entry was EDITED. It does not prove none were")
        print("dropped from the end, or that the whole chain was not rebuilt")
        print("by someone holding the owner's credentials. Anchor this head")
        print("hash outside the database — an evidence bundle, a build log —")
        print("and those become detectable too.")
        return 0
    print(f"FAILED at seq {result['broken_at']}")
    print(f"  {result['reason']}")
    print(f"  {result['entries']} entries verified before the break")
    return 1


def cmd_head(conn, args):
    current = eventlog.head(conn)
    print(f"{current['seq']}  {current['hash']}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Append to and verify Clew's append-only event log.")
    parser.add_argument("--dsn", default=os.environ.get("CLEW_DSN"),
                        help="Postgres connection string; defaults to $CLEW_DSN")
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("init", help="create the table, guards and roles "
                                        "(run as the database owner)")
    setup.add_argument("--writer", default=eventlog.WRITER_ROLE)
    setup.add_argument("--auditor", default=eventlog.AUDITOR_ROLE)
    setup.add_argument("--writer-password")
    setup.add_argument("--auditor-password")
    setup.set_defaults(run=cmd_init)

    add = sub.add_parser("append", help="record one fact")
    add.add_argument("--type", dest="event_type", required=True,
                     help="opaque to core; the domain defines what it means")
    add.add_argument("--subject", required=True, help="what the fact is about")
    add.add_argument("--actor", required=True, help="who asserted it")
    add.add_argument("--body", help="JSON payload")
    add.add_argument("--effective-from",
                     help="when it became true (defaults to now, i.e. when we "
                          "learned it). Pass the real date if you know it.")
    add.set_defaults(run=cmd_append)

    show = sub.add_parser("list", help="read the log")
    show.add_argument("--since", type=int, default=0, help="exclusive")
    show.add_argument("--type", dest="event_type")
    show.add_argument("--subject")
    show.add_argument("--json", action="store_true")
    show.set_defaults(run=cmd_list)

    check = sub.add_parser("verify", help="re-walk the chain, recomputing")
    check.set_defaults(run=cmd_verify)

    tip = sub.add_parser("head", help="print the current head seq and hash")
    tip.set_defaults(run=cmd_head)

    args = parser.parse_args(argv)
    if not args.dsn:
        raise SystemExit("no connection string: pass --dsn or set CLEW_DSN")
    try:
        conn = eventlog.connect(args.dsn)
    except ImportError:
        raise SystemExit(
            "the event log needs psycopg: pip install 'psycopg[binary]'")
    except Exception as exc:
        # A stack trace here tells the operator nothing they can act on. The
        # first line of a libpq error is the actionable part; the rest is the
        # same message repeated once per resolved address.
        first_line = str(exc).strip().splitlines()[0]
        raise SystemExit(f"cannot connect: {first_line}")
    raise SystemExit(args.run(conn, args) or 0)


if __name__ == "__main__":
    main()
