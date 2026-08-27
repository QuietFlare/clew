"""
Clew — the remediation policy, from the command line.

    clew rulebook show
    clew rulebook export --out policy_v1.json
    clew rulebook check policy_v1.json
    clew rulebook register --dsn "$CLEW_DSN" --actor qa.lead@example.org

WHY REGISTER A POLICY IN THE EVENT LOG
--------------------------------------
A plan cites a policy version and hash. That is only worth something if the
claim "v1 hashed to dbb59de6... and we adopted it on this date" is itself a
recorded fact rather than something recomputed later from whatever the code
says today. So adoption is an event, with an actor and an effective date, in
the same append-only log as everything else.

The full policy goes into the event body, not a pointer to it. A pointer to
code is worthless six months and four releases later; the log has to hold the
actual table so an old plan can be replayed even if this build no longer
ships that version.
"""

import argparse
import json
import os
import sys
from pathlib import Path


from clew.core import policy

ADOPTED = "PolicyAdopted"


def selected(args):
    """The policy named on the command line, or the built-in default."""
    if args.file:
        return policy.load(args.file)
    if args.version:
        return policy.resolve(args.version)
    return policy.DEFAULT


def cmd_show(args):
    active = selected(args)
    stamp = policy.identify(active)
    print(f"policy {stamp['policy_version']}")
    print(f"sha256 {stamp['policy_hash']}")
    if active.get("description"):
        print(active["description"])
    print()
    print("First match wins. An omitted dimension is a wildcard.\n")
    for item in active["rules"]:
        when = ", ".join(f"{k}={v}" for k, v in sorted(item["when"].items()))
        print(f"  {item['id']:<4} {when or '(any)':<52} -> {item['action']}")
        print(f"       {item['because']}")
    print()
    trailing = 52 - (len(policy.FALLTHROUGH_RULE) - 4)
    print(f"  {policy.FALLTHROUGH_RULE} "
          f"{'(nothing matched)':<{trailing}} -> {policy.FALLTHROUGH_ACTION}")
    print("       Outside the rule list, where no policy can remove it.")
    print()
    print("The class is normalised before matching: anything unrecognised is")
    print("IRREDUCIBLE first. That fixes the facts, not the verdict — a policy")
    print("that decides badly will be honoured, and will be identifiable by")
    print("version, hash and rule id when someone asks why.")


def cmd_export(args):
    active = selected(args)
    payload = json.dumps(active, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(payload + "\n")
        print(f"wrote {args.out}  ({policy.fingerprint(active)})")
    else:
        print(payload)


def cmd_check(args):
    try:
        loaded = policy.load(args.path)
    except policy.InvalidPolicy as bad:
        print(f"REJECTED  {args.path}")
        print(f"  {bad}")
        return 1
    except (OSError, json.JSONDecodeError) as bad:
        print(f"REJECTED  {args.path}")
        print(f"  not readable as JSON: {bad}")
        return 1
    stamp = policy.identify(loaded)
    print(f"OK  {args.path}")
    print(f"  version {stamp['policy_version']}")
    print(f"  sha256  {stamp['policy_hash']}")
    print(f"  {len(loaded['rules'])} rules")
    print()
    print("Valid means well-formed and decidable — every rule can fire, names")
    print("a known action, and carries a rationale. It does not mean correct.")
    print("Whether the table says the right thing is the author's to defend.")
    return 0


def cmd_diff(args):
    """
    What changed between two policies, by rule id.

    Ids identify rules rather than positions, so a rule that moved is
    reported as moved rather than as one deletion and one addition. Order is
    semantics here — first match wins — so a pure reorder is a real change
    and has to read like one.
    """
    before, after = policy.resolve_or_load(args.before), policy.resolve_or_load(args.after)
    a, b = policy.identify(before), policy.identify(after)
    print(f"  {a['policy_version']:<16} {a['policy_hash']}")
    print(f"  {b['policy_version']:<16} {b['policy_hash']}")
    if a["policy_hash"] == b["policy_hash"]:
        print("\nidentical")
        return 0

    order_before = [r["id"] for r in before["rules"]]
    order_after = [r["id"] for r in after["rules"]]
    if order_before != order_after:
        print(f"\norder  {a['policy_version']}: {' '.join(order_before)}")
        print(f"       {b['policy_version']}: {' '.join(order_after)}")

    by_before = {r["id"]: r for r in before["rules"]}
    by_after = {r["id"]: r for r in after["rules"]}

    for rule_id in [r for r in order_before if r not in by_after]:
        print(f"\n- {rule_id}  removed  ({by_before[rule_id]['action']})")
    for rule_id in [r for r in order_after if r not in by_before]:
        print(f"\n+ {rule_id}  added    ({by_after[rule_id]['action']})")

    for rule_id in order_after:
        if rule_id not in by_before:
            continue
        old_rule, new_rule = by_before[rule_id], by_after[rule_id]
        changes = []
        if order_before.index(rule_id) != order_after.index(rule_id):
            changes.append(f"position {order_before.index(rule_id) + 1} -> "
                           f"{order_after.index(rule_id) + 1}")
        if old_rule["action"] != new_rule["action"]:
            changes.append(f"action {old_rule['action']} -> {new_rule['action']}")
        if old_rule["when"] != new_rule["when"]:
            changes.append(f"when {old_rule['when']} -> {new_rule['when']}")
        rationale = old_rule["because"] != new_rule["because"]
        if not changes and not rationale:
            continue
        heading = f"  {rule_id}"
        if changes:
            heading += "  " + "; ".join(changes)
        print(f"\n{heading}")
        if rationale:
            # The prose is hashed too: it is what an assessor reads, so a
            # changed rationale is a changed policy even when the logic holds.
            print(f"      - {old_rule['because']}")
            print(f"      + {new_rule['because']}")
    return 0


def cmd_register(args):
    from clew.core import eventlog

    active = selected(args)
    stamp = policy.identify(active)
    conn = eventlog.connect(args.dsn)
    entry = eventlog.append(
        conn, event_type=ADOPTED, subject=stamp["policy_version"],
        actor=args.actor, effective_from=args.effective_from,
        # The whole table, not a reference to it. See the module docstring.
        body={"policy_hash": stamp["policy_hash"], "policy": active})
    print(f"seq {entry['seq']}  {ADOPTED}  {entry['subject']}")
    print(f"  sha256          {stamp['policy_hash']}")
    print(f"  effective from  {entry['effective_from']}")
    print(f"  asserted by     {entry['actor']}")
    print(f"  log hash        {entry['hash']}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Inspect, validate and record Clew's remediation policy.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--file", metavar="PATH", help="a policy JSON file")
    source.add_argument("--version", help="a shipped policy version")
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="print the policy and its hash")
    show.set_defaults(run=cmd_show)

    export = sub.add_parser("export", help="write the policy out as JSON")
    export.add_argument("--out", metavar="PATH")
    export.set_defaults(run=cmd_export)

    check = sub.add_parser("check", help="validate a policy file")
    check.add_argument("path")
    check.set_defaults(run=cmd_check)

    diff = sub.add_parser("diff", help="what changed between two policies")
    diff.add_argument("before", help="a shipped version or a file path")
    diff.add_argument("after", help="a shipped version or a file path")
    diff.set_defaults(run=cmd_diff)

    register = sub.add_parser(
        "register", help="record adoption in the append-only event log")
    register.add_argument("--dsn", default=os.environ.get("CLEW_DSN"),
                          help="defaults to $CLEW_DSN")
    register.add_argument("--actor", required=True, help="who adopted it")
    register.add_argument("--effective-from",
                          help="when it takes effect (defaults to now)")
    register.set_defaults(run=cmd_register)

    args = parser.parse_args(argv)
    if args.command == "register" and not args.dsn:
        raise SystemExit("no connection string: pass --dsn or set CLEW_DSN")
    try:
        raise SystemExit(args.run(args) or 0)
    except policy.InvalidPolicy as bad:
        raise SystemExit(f"policy rejected: {bad}")
    except ImportError:
        raise SystemExit(
            "recording a policy needs psycopg: pip install 'psycopg[binary]'")


if __name__ == "__main__":
    main()
