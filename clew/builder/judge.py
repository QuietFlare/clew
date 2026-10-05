"""
Judge an adapter an agent wrote, on the run and the sheet it was written for.

    python -m clew.builder.judge --work DIR --graph graph.json --name NAME [--sheet sheet.csv]

Run as its own process, with the adapter loaded on trial, so unreviewed
code never runs inside anything that stays up. It prints one JSON object:
the checks, what each kind found, and whether all of it passed.

The agent's own tests show the adapter does what the agent meant. These
checks show Clew can use it: the ids are listed, each reaches tasks that
exist in the run, triage offers them, and impact answers for one.
"""

import argparse
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from clew.builder.adapter import judged_file
from clew.contracts import Adapter, Extractor, discover
from clew.contracts.registry import LOCAL_VARIABLE, TRIAL_VARIABLE, load_local
from clew.graph import blast_radius as core
from clew.intake import triage

MOST_LISTED = 12


def flags_of(adapter):
    parser = argparse.ArgumentParser(add_help=False, conflict_handler="resolve")
    for kind in adapter.triggers.values():
        kind.add_arguments(parser)
    return parser, [action.option_strings[0] for action in parser._actions if action.option_strings]


def judge(work, graph_path, name, sheet=None):
    checks, kinds = [], []

    def check(label, passed, detail=""):
        checks.append({"name": label, "passed": bool(passed),
                       "detail": "" if passed else str(detail)[:300]})
        return bool(passed)

    work = Path(work)

    def done():
        return {"passed": all(c["passed"] for c in checks), "checks": checks, "kinds": kinds,
                **judged_file(work, "adapter.py", name)}

    if not check("adapter.py is there", (work / "adapter.py").is_file()):
        return done()
    # Only the adapter goes on trial: its tests and anything else beside it stay out.
    trial = Path(tempfile.mkdtemp(prefix="clew-trial-"))
    atexit.register(shutil.rmtree, trial, ignore_errors=True)
    shutil.copy(work / "adapter.py", trial / "adapter.py")
    before = {contract: dict(discover(contract)) for contract in (Adapter, Extractor)}
    if not check(f"the name {name} is free", name not in before[Adapter],
                 "an installed adapter already has it"):
        return done()
    os.environ[LOCAL_VARIABLE], os.environ[TRIAL_VARIABLE] = str(trial), "1"
    try:
        failed = [problem for _, problem in load_local() if problem]
    except (SystemExit, Exception) as bad:
        failed = [bad]
    if not check("the adapter imports", not failed, failed[0] if failed else ""):
        return done()
    adapters = discover(Adapter)
    if not check(f"registers an adapter named {name}", name in adapters,
                 "found: " + ", ".join(sorted(adapters))):
        return done()
    # One file, one adapter: it may not add or replace any other provider.
    others = sorted(found for contract, had in before.items()
                    for found, now in discover(contract).items()
                    if found != name and had.get(found) is not now)
    if not check("registers nothing else", not others, "also registers: " + ", ".join(others)):
        return done()
    adapter = adapters[name]
    if not check("declares at least one kind", bool(adapter.triggers)):
        return done()

    parser, flags = flags_of(adapter)
    given = []
    if sheet:
        if not check("takes the sheet through one flag", len(flags) == 1, f"flags: {flags}"):
            return done()
        given = [flags[0], str(sheet)]
    args = parser.parse_args(given)
    graph = core.load_graph(str(graph_path))

    first = None
    for kind, declared in adapter.triggers.items():
        try:
            entries = declared.resolve(graph, None, args)
            listed = declared.values(args, graph)
        except (SystemExit, Exception) as bad:
            check(f"{kind}: resolves every id", False, bad)
            continue
        reached = {value: nodes for value, nodes in entries.items() if nodes}
        unreached = sorted(set(entries) - set(reached))
        kinds.append({"kind": kind, "about": declared.about, "mode": declared.mode.value,
                      "ids": len(entries), "reached": len(reached),
                      "unreached": unreached[:MOST_LISTED],
                      "tasks_per_id": sorted(len(nodes) for nodes in reached.values())[:MOST_LISTED]})
        check(f"{kind}: resolves every id", isinstance(entries, dict) and entries)
        check(f"{kind}: lists the same ids it resolves", sorted(listed) == sorted(entries),
              f"{len(listed)} listed, {len(entries)} resolved")
        check(f"{kind}: at least one id reaches a task", bool(reached))
        strangers = sorted({node for nodes in entries.values() for node in nodes} - set(graph["tasks"]))
        check(f"{kind}: every task it names is in the run", not strangers, strangers[:5])
        check(f"{kind}: says what it names", bool((declared.about or "").strip()))
        try:
            declared.resolve(graph, "no-such-id-0000", args)
            refused = False
        except SystemExit:
            refused = True
        except Exception as bad:
            refused = False
            check(f"{kind}: an unknown id is refused", False, f"it raised {bad!r}")
            continue
        check(f"{kind}: an unknown id is refused", refused, "it answered for an id nobody has")
        if reached and first is None:
            first = (kind, sorted(reached)[0])

    if first:
        kind, value = first
        offered = {trigger for _, trigger, _ in triage.options(graph, adapter=adapter, args=args,
                                                               incident=value)}
        check("triage offers its ids", f"{kind}:{value}" in offered)
        with tempfile.TemporaryDirectory() as scratch:
            plan = Path(scratch) / "plan.json"
            ran = subprocess.run(
                [sys.executable, "-m", "clew", "impact", "--graph", str(graph_path),
                 "--pipeline", name, *given, "--trigger", f"{kind}:{value}", "--json", str(plan)],
                capture_output=True, text=True, timeout=300)
            answered = ran.returncode == 0 and plan.is_file() and \
                json.loads(plan.read_text())["tasks_affected"] > 0
            check(f"impact answers for {kind}:{value}", answered,
                  (ran.stderr or ran.stdout).strip().splitlines()[-1:] or "no plan")
    check("the agent wrote its own tests", (work / "test_adapter.py").is_file())
    return done()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Judge an adapter an agent wrote.")
    parser.add_argument("--work", required=True)
    parser.add_argument("--graph", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--sheet")
    args = parser.parse_args(argv)
    print(json.dumps(judge(args.work, args.graph, args.name, args.sheet), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
