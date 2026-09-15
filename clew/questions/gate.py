"""
The pre-flight gate: compliance as a build check.

    clew gate --pipeline sarek --samplesheet sheet.csv --dsn "$CLEW_DSN" \
        --block-on Withdrawn --clear-on Reinstated --out bundle/

Exit 0 to proceed, 1 to stop. Every way of failing to establish that the
inputs are permitted exits non-zero: an unreachable log, no blocking types
given, a subject the log has never heard of (unless --allow-unknown), a
blocked subject. A green build must mean checked and permitted.

The samplesheet and the log name subjects in different vocabularies and
nothing makes them agree, so the report always states how many subjects the
log recognised. A gate that goes green having recognised none of its inputs
is the failure this command is arranged to make loud.
"""

import argparse
import json
import os
import sys
from pathlib import Path


from clew.ledger import bundle
from clew.ledger import gate as core_gate
from clew.contracts import REMOVE, Adapter, discover

CHECKED = "GateChecked"


def load_gate_policy(args):
    """
    Which fact types stop a build and which release it. No default: what
    your event types mean is your policy, and a default here would be
    shipping truth. See gate-policy.example.json.
    """
    if args.gate_policy:
        try:
            document = json.loads(Path(args.gate_policy).read_text())
        except OSError as exc:
            raise SystemExit(
                f"cannot read --gate-policy {args.gate_policy}: "
                f"{exc.strerror}. Stopping: a gate without its policy "
                "cannot check anything.")
        except ValueError as exc:
            raise SystemExit(
                f"--gate-policy {args.gate_policy} is not valid JSON: {exc}")
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


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    adapters = discover(Adapter)
    first = argparse.ArgumentParser(add_help=False)
    first.add_argument("--pipeline", choices=sorted(adapters), default="sarek")
    adapter = adapters[first.parse_known_args(argv)[0].pipeline]
    removable = [k for k, t in adapter.triggers.items() if t.mode is REMOVE]
    parser = argparse.ArgumentParser(
        description="Stop a pipeline run whose inputs are not permitted.",
        conflict_handler="resolve")
    parser.add_argument("--pipeline", choices=sorted(adapters), default="sarek")
    parser.add_argument("--kind", choices=sorted(adapter.triggers),
                        default=removable[0] if removable else None,
                        help="which of this pipeline's trigger kinds lists the inputs to check")
    for kind in adapter.triggers.values():
        kind.add_arguments(parser)
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
                             "estate, but it must be a choice.")
    parser.add_argument("--out", metavar="DIR",
                        help="seal the result into an evidence bundle")
    parser.add_argument("--force", action="store_true",
                        help="replace the contents of a non-empty --out")
    parser.add_argument("--actor", default="ci",
                        help="who ran the check, for --out --seal-into-log")
    parser.add_argument("--seal-into-log", action="store_true",
                        help="record the gate result back into the log")
    args = parser.parse_args(argv)

    policy = load_gate_policy(args)
    if not args.kind:
        raise SystemExit(
            f"pipeline {args.pipeline!r} declares no trigger kind that owns inputs, "
            "so there is nothing to list and check. Stopping.")
    try:
        subjects = sorted(adapter.triggers[args.kind].values(args))
    except OSError as exc:
        raise SystemExit(
            f"cannot read the {args.kind} inputs: {exc.strerror} ({exc.filename}). "
            "Stopping: no inputs were checked.")

    if args.as_of:
        from clew.ledger import eventlog
        try:
            eventlog.instant(args.as_of)
        except ValueError as exc:
            raise SystemExit(f"--as-of: {exc}")

    if not args.dsn:
        raise SystemExit(
            "no connection string: pass --dsn or set CLEW_DSN. Without the "
            "log there are no facts to check against, and a gate that cannot "
            "check must not pass.")
    try:
        from clew.ledger import eventlog
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

    # as_of is always a concrete instant in the result, defaulted to now by
    # the core when none was given. That is what lets a sealed gate result
    # be re-derived after the log has grown.
    result = core_gate.decide(
        subjects, entries, policy["blocking"], policy["clearing"],
        as_of=args.as_of, unknown_blocks=not args.allow_unknown)
    result["as_of_given"] = args.as_of is not None
    result["gate_policy"] = policy
    result["inputs"] = {"kind": args.kind, "samplesheet": getattr(args, "samplesheet", None)}
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
    when = result["as_of"]
    if not result.get("as_of_given", True):
        when += "  (now; no --as-of given)"
    print(f"  as of           {when}")
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
        # Keyed by content hash, basename only: the same sheet from any
        # directory seals to the same bundle.
        "inputs.json": {
            bundle.sha256_file(args.samplesheet): {
                "name": Path(args.samplesheet).name,
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
    try:
        manifest, digest = bundle.build(
            args.out, documents, log_head=log_head, coverage=coverage,
            force=args.force,
            description=f"Clew gate result for {Path(args.samplesheet).name}")
    except FileExistsError:
        raise SystemExit(
            f"{args.out} is not empty; a bundle needs a directory of its "
            "own. Pass --force to replace what is there.")
    print(f"\nsealed {args.out}")
    print(f"  bundle hash  {digest}")

    if args.seal_into_log:
        from clew.ledger import eventlog
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
