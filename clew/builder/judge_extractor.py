"""
Judge an extractor an agent wrote, on the record it was written for.

    python -m clew.builder.judge_extractor --work DIR --name NAME --record FOLDER

Run as its own process, with the extractor loaded on trial. It prints one
JSON object: the checks, what the graph holds, and whether all passed.

These checks show the graph is well formed and that Clew can pick the
folder. They cannot show the graph is true to the run: nobody gave the
judge the answer. The counts it reports are for a person to compare.
"""

import argparse
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from clew.builder.adapter import judged_file
from clew.builder.extractor import FLAG, WRITTEN
from clew.contracts import Adapter, Extractor, discover
from clew.contracts.registry import LOCAL_VARIABLE, TRIAL_VARIABLE, load_local
from clew.graph.graph import EXTERNAL, contract_violations

MOST_RUNS = 5
MOST_LISTED = 12


def judge(work, name, record):
    checks, found = [], {}

    def check(label, passed, detail=""):
        checks.append({"name": label, "passed": bool(passed),
                       "detail": "" if passed else str(detail)[:300]})
        return bool(passed)

    work, record = Path(work), Path(record)

    def done():
        return {"passed": all(c["passed"] for c in checks), "checks": checks, "graph": found,
                **judged_file(work, WRITTEN, name)}

    if not check(f"{WRITTEN} is there", (work / WRITTEN).is_file()):
        return done()
    # Only the extractor goes on trial: its tests and anything else beside it stay out.
    trial = Path(tempfile.mkdtemp(prefix="clew-trial-"))
    atexit.register(shutil.rmtree, trial, ignore_errors=True)
    shutil.copy(work / WRITTEN, trial / WRITTEN)
    before = {contract: dict(discover(contract)) for contract in (Adapter, Extractor)}
    if not check(f"the name {name} is free", name not in before[Extractor],
                 "an installed extractor already has it"):
        return done()
    os.environ[LOCAL_VARIABLE], os.environ[TRIAL_VARIABLE] = str(trial), "1"
    try:
        failed = [problem for _, problem in load_local() if problem]
    except (SystemExit, Exception) as bad:
        failed = [bad]
    if not check("the extractor imports", not failed, failed[0] if failed else ""):
        return done()
    extractors = discover(Extractor)
    if not check(f"registers an extractor named {name}", name in extractors,
                 "found: " + ", ".join(sorted(extractors))):
        return done()
    # One file, one extractor: it may not add or replace any other provider.
    others = sorted(seen for contract, had in before.items()
                    for seen, now in discover(contract).items()
                    if seen != name and had.get(seen) is not now)
    if not check("registers nothing else", not others, "also registers: " + ", ".join(others)):
        return done()
    extractor = extractors[name]

    parser = argparse.ArgumentParser(add_help=False)
    extractor.add_arguments(parser)
    flags = [action.option_strings[0] for action in parser._actions if action.option_strings]
    # --run to pick one of several runs is Clew's own convention, so it is allowed beside --record.
    if not check(f"takes the record through {FLAG}", FLAG in flags and set(flags) <= {FLAG, "--run"},
                 f"flags: {flags}"):
        return done()
    try:
        graph = extractor.extract(parser.parse_args([FLAG, str(record)]))
    except (SystemExit, Exception) as bad:
        check("extracts the record", False, repr(bad))
        return done()
    if not check("extracts the record", isinstance(graph, dict), type(graph).__name__):
        return done()
    problems = contract_violations(graph)
    if not check("the graph meets Clew's contract", not problems, "; ".join(problems[:3])):
        return done()
    tasks, edges = graph["tasks"], graph["edges"]
    found.update({
        "tasks": len(tasks), "edges": len(edges),
        "external": sorted({Path(e["filename"]).name for e in edges
                            if e["producer"] == EXTERNAL})[:MOST_LISTED],
        "processes": dict(Counter(task.get("process") or "?" for task in tasks.values())
                          .most_common(MOST_LISTED)),
        "statuses": dict(Counter(task.get("status") or "?" for task in tasks.values())),
        "coverage": (graph.get("coverage") or [])[:MOST_LISTED], "runs": []})
    check("the run has at least one task", bool(tasks))
    check("says what the graph does not cover", bool(graph.get("coverage")))

    # The page picks a run by folder, so the folder must be recognised, and no other may be.
    try:
        listed = extractor.records(record)
    except (SystemExit, Exception) as bad:
        listed = None
        check("recognises the record's folder", False, repr(bad))
    else:
        check("recognises the record's folder", bool(listed and listed.get("runs")),
              "records() listed no run")
    for run in (listed or {}).get("runs", [])[:MOST_RUNS]:
        try:
            loaded = extractor.load(listed["root"], run["id"])
            problems = contract_violations(loaded)
        except (SystemExit, Exception) as bad:
            check(f"loads run {run['name']}", False, repr(bad))
            continue
        found["runs"].append({"name": run["name"], "tasks": len(loaded.get("tasks") or {})})
        check(f"loads run {run['name']}", not problems and loaded.get("tasks"),
              "; ".join(problems[:3]) or "no tasks")
    with tempfile.TemporaryDirectory() as empty:
        (Path(empty) / "notes.txt").write_text("not a run record\n")
        try:
            claimed = [str(folder) for folder in (Path(empty), Path(__file__).parent)
                       if extractor.records(folder) is not None]
        except (SystemExit, Exception) as bad:
            claimed = [repr(bad)]
        check("leaves other folders alone", not claimed, "it claimed " + ", ".join(claimed))

    with tempfile.TemporaryDirectory() as scratch:
        out = Path(scratch) / "graph.json"
        ran = subprocess.run([sys.executable, "-m", "clew", "extract", name, FLAG, str(record),
                              "--json-out", str(out)], capture_output=True, text=True, timeout=300)
        check(f"clew extract {name} writes the graph", ran.returncode == 0 and out.is_file(),
              (ran.stderr or ran.stdout).strip().splitlines()[-1:] or "no graph")
    check("the agent wrote its own tests", (work / "test_extractor.py").is_file())
    return done()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Judge an extractor an agent wrote.")
    parser.add_argument("--work", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--record", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(judge(args.work, args.name, args.record), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
