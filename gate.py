"""
Clew — the pre-flight gate. Compliance as a build check, not a PDF.

    python3 gate.py --pipeline sarek --samplesheet samplesheet.csv \
        --dsn "$CLEW_DSN" --block-on Withdrawn --clear-on Reinstated \
        --out bundle/

Exit 0 to proceed, 1 to stop. Run it before the pipeline, in CI, so that
using material nobody is allowed to use fails the build the way a failing
test does — at the point where it is cheap, rather than in a remediation
exercise two years later.

FAIL CLOSED, EVERYWHERE, ON EVERYTHING
--------------------------------------
Every way this command can fail to establish that the inputs are permitted
exits non-zero:

    the log is unreachable          -> stop
    no blocking types were given    -> stop (a gate with nothing to block on
                                       is not a lenient gate, it is no gate)
    a subject is UNKNOWN to the log -> stop, unless --allow-unknown says
                                       someone decided otherwise
    a subject is BLOCKED            -> stop

A green build must mean "checked and permitted". If it can also mean "could
not check", the check is decorative, and a decorative compliance gate is
worse than none: it manufactures a record of diligence that did not happen.

THE IDENTIFIER TRAP
-------------------
The samplesheet names subjects in one vocabulary and the log in another, and
nothing makes them agree. A typo, a prefix, a different column, and every
subject comes back UNKNOWN — which is why UNKNOWN stops the build by default
and why the report always states how many subjects the log had ever heard of.
A gate that goes green having recognised none of its inputs is the exact
failure this design is arranged to make loud.
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import evidence
from core import gate as core_gate
from domains import rnaseq, sarek, viralrecon

DOMAINS = {"sarek": sarek, "viralrecon": viralrecon, "rnaseq": rnaseq}

CHECKED = "GateChecked"


def load_gate_policy(args):
    """
    Which fact types stop a build, and which release it.

    Deliberately not defaulted. Clew ships no opinion about what your event
    types mean or which of them should stop work — that is the customer's
    policy, and guessing it here would be shipping truth rather than a
    template. See gate-policy.example.json.
    """
    if args.gate_policy:
        document = json.loads(Path(args.gate_policy).read_text())
        blocking = document.get("blocking", [])
        clearing = document.get("clearing", [])
        version = document.get("version", Path(args.gate_policy).name)
    else:
        blocking, clearing = args.block_on or [], args.clear_on or []
        version = "inline"

    if not blocking:
        raise SystemExit(
            "no blocking fact types given: pass --block-on, or --gate-policy "
            "pointing at a file that names them. A gate with nothing to block "
            "on is not a lenient gate, it is no gate, and it would pass every "
            "build while appearing to check them.")

    overlap = set(blocking) & set(clearing)
    if overlap:
        raise SystemExit(
            f"these types are listed as both blocking and clearing: "
            f"{', '.join(sorted(overlap))}. One of them decides; which one "
            "cannot be inferred, so state it.")

    return {"version": version, "blocking": sorted(set(blocking)),
            "clearing": sorted(set(clearing))}


def main():
    parser = argparse.ArgumentParser(
        description="Stop a pipeline run whose inputs are not permitted.")
    parser.add_argument("--samplesheet", required=True)
    parser.add_argument("--pipeline", choices=sorted(DOMAINS), default="sarek")
    parser.add_argument("--dsn", default=os.environ.get("CLEW_DSN"),
                        help="the event log holding the facts; $CLEW_DSN")
    parser.add_argument("--gate-policy", metavar="PATH",
                        help="JSON naming the blocking and clearing types")
    parser.add_argument("--block-on", action="append", metavar="TYPE",
                        help="a fact type that stops the build; repeatable")
    parser.add_argument("--clear-on", action="append", metavar="TYPE",
                        help="a fact type that releases it; repeatable")
    parser.add_argument("--as-of", metavar="DATE",
                        help="evaluate facts effective on or before this date "
                             "(ISO-8601). Facts effective later are ignored, "
                             "so a historical gate result stays reproducible.")
    parser.add_argument("--allow-unknown", action="store_true",
                        help="do not stop on subjects the log has never heard "
                             "of. A real choice for a log covering part of an "
                             "estate — but it must be a choice.")
    parser.add_argument("--out", metavar="DIR",
                        help="seal the result into an evidence bundle")
    parser.add_argument("--actor", default="ci",
                        help="who ran the check, for --out --seal-into-log")
    parser.add_argument("--seal-into-log", action="store_true",
                        help="record the gate result back into the log")
    args = parser.parse_args()

    policy = load_gate_policy(args)
    domain = DOMAINS[args.pipeline]
    subjects = sorted(domain.load_subjects(args.samplesheet))

    if not args.dsn:
        raise SystemExit(
            "no connection string: pass --dsn or set CLEW_DSN. Without the "
            "log there are no facts to check against, and a gate that cannot "
            "check must not pass.")
    try:
        from core import eventlog
        conn = eventlog.connect(args.dsn)
        entries = eventlog.read(conn)
        log_head = eventlog.head(conn)
    except ImportError:
        raise SystemExit("the gate needs psycopg: pip install 'psycopg[binary]'")
    except Exception as exc:
        # Fail closed. An unreachable log is not an absence of prohibitions.
        raise SystemExit(
            f"cannot reach the event log: {str(exc).strip().splitlines()[0]}\n"
            "Stopping. An unreachable log is not the same as a clean one.")

    result = core_gate.decide(
        subjects, entries, policy["blocking"], policy["clearing"],
        as_of=args.as_of, unknown_blocks=not args.allow_unknown)
    result["gate_policy"] = policy
    result["samplesheet"] = str(args.samplesheet)
    result["log_head"] = log_head

    report(result, policy, log_head)

    if args.out:
        seal(args, result, policy, entries, log_head)

    return 0 if result["passed"] else 1


def report(result, policy, log_head):
    counts = result["counts"]
    total = len(result["subjects"])
    print(f"CLEW GATE  {result['samplesheet']}")
    print(f"  subjects        {total}")
    print(f"  blocking on     {', '.join(policy['blocking'])}")
    if policy["clearing"]:
        print(f"  cleared by      {', '.join(policy['clearing'])}")
    print(f"  as of           {result['as_of'] or 'now (all facts in effect)'}")
    print(f"  log head        seq {log_head['seq']}  {log_head['hash'][:16]}")
    print()

    for status in (core_gate.BLOCKED, core_gate.UNKNOWN, core_gate.CLEARED):
        named = [s for s, d in result["subjects"].items()
                 if d["status"] == status]
        if not named:
            continue
        print(f"  {status:<8} {len(named)}")
        for subject in named[:20]:
            detail = result["subjects"][subject]
            print(f"      {subject:<28} {detail['reason']}")
            if detail["fact"]:
                print(f"          log seq {detail['fact']['seq']}, "
                      f"entry {detail['fact']['hash'][:16]}")
        if len(named) > 20:
            print(f"      ... {len(named) - 20} more")
        print()

    known = counts[core_gate.BLOCKED] + counts[core_gate.CLEARED]
    if known == 0:
        # The identifier trap, said out loud rather than left to be noticed.
        print("  NOTE: the log had never heard of ANY subject in this "
              "samplesheet.")
        print("  That usually means the identifiers do not match between the "
              "two, not")
        print("  that everything is permitted. Check the subject column and "
              "the log's")
        print("  subject vocabulary before reading anything into this result.")
        print()

    if result["passed"]:
        print(f"PASS  {counts[core_gate.CLEARED]} cleared, "
              f"{counts[core_gate.UNKNOWN]} unknown "
              f"(allowed by --allow-unknown)"
              if counts[core_gate.UNKNOWN] else
              f"PASS  {counts[core_gate.CLEARED]} cleared")
    else:
        print(f"STOP  {counts[core_gate.BLOCKED]} blocked, "
              f"{counts[core_gate.UNKNOWN]} unknown, "
              f"{counts[core_gate.CLEARED]} cleared")


def seal(args, result, policy, entries, log_head):
    documents = {
        "gate.json": result,
        "gate-policy.json": policy,
        "events.json": [
            {k: e[k] for k in ("seq", "effective_from", "recorded_at", "actor",
                               "event_type", "subject", "prev_hash", "hash")}
            | {"body": json.dumps(e["body"], sort_keys=True,
                                  separators=(",", ":"), ensure_ascii=True)}
            for e in entries],
        "inputs.json": {
            Path(args.samplesheet).name: {
                "path": str(args.samplesheet),
                "sha256": evidence.sha256_file(args.samplesheet),
                "bytes": Path(args.samplesheet).stat().st_size,
            }
        },
    }
    coverage = [
        "a gate result is a statement about the INPUTS to a run, not about "
        "anything the run produced",
        f"{result['counts'][core_gate.UNKNOWN]} subjects were unknown to the "
        "log; unknown is not clean",
        "the log's coverage bounds this result: facts never recorded cannot "
        "block anything",
    ]
    manifest, digest = evidence.build(
        args.out, documents, log_head=log_head, coverage=coverage,
        description=f"Clew gate result for {Path(args.samplesheet).name}")
    print(f"\nsealed {args.out}")
    print(f"  bundle hash  {digest}")

    if args.seal_into_log:
        from core import eventlog
        conn = eventlog.connect(args.dsn)
        entry = eventlog.append(
            conn, event_type=CHECKED, subject=Path(args.samplesheet).name,
            actor=args.actor,
            body={"bundle_hash": digest, "passed": result["passed"],
                  "counts": result["counts"], "as_of": result["as_of"],
                  "gate_policy_version": policy["version"]})
        print(f"  logged as seq {entry['seq']}")


if __name__ == "__main__":
    raise SystemExit(main())
