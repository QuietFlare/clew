"""
Does a typed classifier, handed the policy table as input, reach the
table's own verdict?

    export TYPESAFE_API_KEY=...
    python examples/jev_trial.py --dry-run     # print the first request, send nothing
    python examples/jev_trial.py               # all 72 fact combinations
    python examples/jev_trial.py --out answers.json

Each call sends one set of facts and the whole table as state and asks one
choice question over the six actions. The answer is compared with
policy.decide() on the same facts, so every disagreement is a case where
the classifier misread a rule that has exactly one right answer.
"""

import argparse
import json
import os
import urllib.error
import urllib.request
from itertools import product

from clew.graph import contribution
from clew.ledger import policy

ENDPOINT = "https://api.typesafe.ai/v1/systemone"

# Pinned, not jev-latest: an alias moves, and a threshold tuned against one
# version says nothing about the next.
MODEL = "jev-1.13.0"

INSTRUCTIONS = (
    "Apply the rules in `policy` to `facts`. Rules are tried in order. The "
    "first rule whose `when` values all equal the facts decides, and a "
    "dimension a rule does not name matches anything. Which action does "
    "the policy give?")


def cases():
    """Every combination of fully known facts."""
    names = policy.DIMENSIONS
    for values in product(*(sorted(policy.VALID[n], key=str) for n in names)):
        yield dict(zip(names, values))


def request(facts, table, model):
    return {
        "model": model,
        "state": {"facts": facts, "policy": table},
        "questions": {"action": {
            "type": "choice",
            "instructions": INSTRUCTIONS,
            "criteria": {action: contribution.explain(action)
                         for action in sorted(policy.ACTIONS)},
        }},
    }


def ask(body, key):
    call = urllib.request.Request(
        ENDPOINT, data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(call, timeout=30) as reply:
            return json.load(reply)
    except urllib.error.HTTPError as bad:
        raise SystemExit(f"HTTP {bad.code}: {bad.read().decode('utf-8', 'replace')}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--limit", type=int, help="stop after this many cases")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the first request and send nothing")
    parser.add_argument("--out", metavar="PATH", help="write every answer as JSON")
    args = parser.parse_args(argv)

    table = policy.DEFAULT
    todo = list(cases())[:args.limit]

    if args.dry_run:
        print(json.dumps(request(todo[0], table, args.model), indent=2))
        print(f"\n{len(todo)} cases, nothing sent")
        return 0

    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        raise SystemExit("set TYPESAFE_API_KEY first")

    rows, tokens = [], 0
    for facts in todo:
        expected = policy.decide(
            facts["contribution"], storage=facts["storage"], scope=facts["scope"],
            released=facts["released"], mode=facts["mode"], policy=table)
        reply = ask(request(facts, table, args.model), key)
        answer = reply["answers"]["action"]
        tokens += reply.get("usage", {}).get("input_tokens", 0)
        agrees = answer["choice"] == expected["action"]
        rows.append({"facts": facts, "expected": expected["action"],
                     "rule": expected["rule"], "model": reply.get("model"),
                     "answer": answer, "agrees": agrees})
        shown = " ".join(str(facts[n]) for n in policy.DIMENSIONS)
        print(f"{'ok  ' if agrees else 'DIFF'} {shown:<50} "
              f"{expected['rule']:<3} {expected['action']:<12} "
              f"{answer['choice']:<12} {answer['confidence']:.2f}")

    agreed = [r for r in rows if r["agrees"]]
    missed = [r for r in rows if not r["agrees"]]
    print(f"\n{len(agreed)} of {len(rows)} agree with the table")
    if agreed:
        print("lowest confidence when right  "
              f"{min(r['answer']['confidence'] for r in agreed):.2f}")
    if missed:
        print("highest confidence when wrong "
              f"{max(r['answer']['confidence'] for r in missed):.2f}")
    print(f"{tokens} input tokens")

    if args.out:
        with open(args.out, "w") as handle:
            json.dump({"policy": policy.identify(table), "rows": rows},
                      handle, indent=2)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
