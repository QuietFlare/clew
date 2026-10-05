"""
One build, from brief to verdict.

    clew build adapter --graph graph.json --name NAME --kind WORD [--sheet FILE] [--removable]
    clew build extractor --record FOLDER --name NAME

An agent writes the provider in a folder of its own, under Mainsheet. The
conformance check then runs on what it left, in a process of its own, and
the checks are printed with the command a person uses to approve the
file. Nothing is installed by a build.

The Providers tab of `clew ui` runs these same steps.
"""

import argparse
import importlib.util
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

from clew.builder import adapter as adapters
from clew.builder import extractor as extractors
from clew.contracts import Adapter, Extractor, discover
from clew.contracts.registry import LOCAL_VARIABLE, file_hash

BRIEF = "agent.yaml"
VERDICT = "verdict.json"
ADAPTER, EXTRACTOR = "adapter", "extractor"
WRITTEN = {ADAPTER: ("adapter.py", "test_adapter.py"),
           EXTRACTOR: (extractors.WRITTEN, "test_extractor.py")}
AGENTS = {ADAPTER: adapters.AGENT, EXTRACTOR: extractors.AGENT}

# Tracing chatter from an agent run with no trace viewer listening.
NOISE = ("Transient error HTTPConnectionPool", "Failed to export span batch")
SPENT = re.compile(r"turns: (\d+)\s+cost_usd: ([0-9.]+|None)")

Refused = adapters.Refused


def spent(line):
    """(turns, cost) when a line of the agent's output reports them, else None."""
    found = SPENT.search(line)
    if not found:
        return None
    return int(found.group(1)), None if found.group(2) == "None" else float(found.group(2))


def keep_sheet(folder, sheet):
    """A copy of the sheet inside the build, under its own name: an adapter may look for that name among the run's inputs."""
    (Path(folder) / "sheet").mkdir()
    return Path(shutil.copy(sheet, Path(folder) / "sheet" / Path(sheet).name))


def brief(folder, agent_home, what, asked, sheet=None, record=None):
    """
    Write the agent's definition for this build. Returns its path, and a
    function that finds the agent's work folder once Mainsheet has made it.
    """
    folder, agent_home, agent = Path(folder), Path(agent_home), AGENTS[what]
    if what == ADAPTER:
        definition = adapters.definition(folder, agent_home, sys.executable, sheet=sheet, **asked)
    else:
        definition = extractors.definition(record, agent_home, sys.executable, **asked)
    before = set((agent_home / "instances").glob(f"{agent}-*"))

    def made():
        new = sorted(set((agent_home / "instances").glob(f"{agent}-*")) - before,
                     key=lambda instance: instance.stat().st_mtime)
        return new[-1] / "work" if new else None
    (folder / BRIEF).write_text(json.dumps(definition, indent=2) + "\n")
    return folder / BRIEF, made


def collect(folder, what, left):
    """Copy what the agent left into the build's own work folder. True when the provider file is among it."""
    work = Path(folder) / "work"
    work.mkdir()
    for name in WRITTEN[what]:
        # Only a plain file the agent wrote: a link could point anywhere.
        if left and (left / name).is_file() and not (left / name).is_symlink():
            shutil.copy(left / name, work / name)
    return (work / WRITTEN[what][0]).is_file()


def check(folder, what, name, sheet=None, record=None):
    """Run the conformance check on the build's work folder and keep the verdict beside it."""
    folder = Path(folder)
    work = folder / "work"
    if what == ADAPTER:
        verdict = adapters.judged(work, folder / "graph.json", name, sheet)
    else:
        verdict = extractors.judged(work, name, record)
    verdict["sha256"] = file_hash(work / WRITTEN[what][0])
    (folder / VERDICT).write_text(json.dumps(verdict, indent=2) + "\n")
    return verdict


def mainsheet(definition, agent_home, say, env=None):
    """Run one Mainsheet agent on a definition, each line of its output handed to `say`. Returns its exit status."""
    if importlib.util.find_spec("mainsheet") is None:
        raise Refused("Mainsheet is not installed in this environment, so the agent cannot run")
    agent_home = Path(agent_home)
    agent_home.mkdir(parents=True, exist_ok=True)
    ran = subprocess.Popen([sys.executable, "-m", "mainsheet.agent.main", str(definition)],
                           cwd=agent_home, text=True, bufsize=1,
                           env=dict(os.environ, MAINSHEET_HOME=str(agent_home), **(env or {})),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for line in ran.stdout:
        say(line)
    return ran.wait()


def taken(name, contract, providers):
    """Whether a provider of this name is installed. The file is looked for first, so nothing is imported to say yes."""
    return adapters.installed_as(providers, name).exists() or name in discover(contract)


def main(argv=None, launch=None):
    parser = argparse.ArgumentParser(
        prog="clew build",
        description="Have an agent write a provider, then run the conformance check on it.")
    kinds = parser.add_subparsers(dest="what", required=True)
    for what, about in ((ADAPTER, "what an incident names, in a run Clew reads"),
                        (EXTRACTOR, "a run record Clew cannot read yet")):
        one = kinds.add_parser(what, help=about, description=f"An {what}: {about}.")
        one.add_argument("--name", required=True, help="lowercase letters, digits, - or _")
        one.add_argument("--notes", default="", help="anything the agent should know")
        one.add_argument("--home", default="~/.clew/ui",
                         help="where builds and the agent's records are kept (default ~/.clew/ui, as clew ui)")
        if what == ADAPTER:
            one.add_argument("--graph", required=True, help="the run's graph, from clew extract")
            one.add_argument("--kind", required=True, help="what one id is called, as one lowercase word")
            one.add_argument("--sheet", help="the launch sheet. The agent reads it, so its contents go to the model")
            one.add_argument("--removable", action="store_true",
                             help="one can be withdrawn, and what only it fed is then removed")
        else:
            one.add_argument("--record", required=True,
                             help="the folder the engine wrote its record in. The agent reads it")
    args = parser.parse_args(argv)

    home = Path(args.home).expanduser().resolve()
    providers = Path(os.environ.get(LOCAL_VARIABLE) or home / "providers").expanduser()
    # The conformance check must see what is already installed, to refuse a taken name.
    os.environ[LOCAL_VARIABLE] = str(providers)
    sheet = record = None
    try:
        if args.what == ADAPTER:
            adapters.check_brief(args.name, args.kind)
            if taken(args.name, Adapter, providers):
                raise Refused(f"an adapter named {args.name} is already installed; choose another name")
            if not Path(args.graph).is_file():
                raise Refused(f"{args.graph} is not a file")
            if args.sheet and not Path(args.sheet).is_file():
                raise Refused(f"{args.sheet} is not a file")
            asked = {"name": args.name, "kind": args.kind, "removable": args.removable,
                     "notes": args.notes.strip()}
        else:
            extractors.check_brief(args.name)
            if taken(args.name, Extractor, providers):
                raise Refused(f"an extractor named {args.name} is already installed; choose another name")
            record = Path(args.record).expanduser().resolve()
            if not record.is_dir():
                raise Refused(f"{args.record} is not a folder")
            if record == Path.home() or record in Path.home().parents:
                raise Refused("pick the run's own folder, not one that holds everything else")
            reader = next((name for name, one in sorted(discover(Extractor).items())
                           if one.records(record) is not None), None)
            if reader:
                raise Refused(f"Clew already reads that folder as a {reader} record")
            asked = {"name": args.name, "notes": args.notes.strip()}
    except Refused as bad:
        raise SystemExit(f"clew build: {bad}")

    folder = home / "jobs" / (time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2))
    folder.mkdir(parents=True)
    if args.what == ADAPTER:
        shutil.copy(args.graph, folder / "graph.json")
        sheet = keep_sheet(folder, args.sheet) if args.sheet else None
    definition, made = brief(folder, home / "mainsheet", args.what, asked, sheet=sheet, record=record)
    print(f"build {folder}\nagent {AGENTS[args.what]} on {adapters.MODEL}, limits in {definition}",
          file=sys.stderr)

    used = {}

    def say(line):
        if line.strip() and not line.startswith(NOISE):
            print("  " + line.rstrip()[:300], file=sys.stderr)
        if spent(line):
            used["turns"], used["cost"] = spent(line)
    try:
        status = (launch or mainsheet)(definition, home / "mainsheet", say)
    except Refused as bad:
        raise SystemExit(f"clew build: {bad}")
    if used:
        print(f"{used['turns']} agent turns" + ("" if used["cost"] is None else f", ${used['cost']:.3f}"),
              file=sys.stderr)
    if not collect(folder, args.what, made()):
        print(f"clew build: the agent ended with status {status} and wrote no {args.what}", file=sys.stderr)
        return 2

    verdict = check(folder, args.what, args.name, sheet=sheet, record=record)
    for one in verdict["checks"]:
        print(f"{'ok  ' if one['passed'] else 'FAIL'}  {one['name']}"
              + (f"  ({one['detail']})" if one["detail"] else ""))
    passed = sum(1 for one in verdict["checks"] if one["passed"])
    print(f"\n{passed} of {len(verdict['checks'])} conformance checks passed")
    print(f"the code: {folder / 'work' / WRITTEN[args.what][0]}")
    if not verdict["passed"]:
        print("nothing can be installed from this build. Build again, with a note on what failed.")
        return 1
    print("read it, then approve it under your own name:\n"
          f"  {LOCAL_VARIABLE}={providers} clew providers --approve {folder / VERDICT} --actor \"your name\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
