"""
Clew in a browser tab, on this machine only.

    clew ui                       # prints a link and opens it
    clew ui --port 8770 --home ~/.clew/ui --no-browser

Pick a run folder, pick a run, describe an incident and press Run. The page
extracts the run's graph, then hands the incident to the Clew agent, which
triages it, plans and seals. Each step shows when its file appears on
disk, so the page reports what happened and not what the agent says
happened.

The Providers tab has an agent write an adapter for a run and its launch
sheet, or an extractor for a folder Clew cannot read yet. A judge checks
what it wrote, the page shows the code, and nothing is installed until a
person approves it under their own name.

The server listens on 127.0.0.1, answers only requests that carry the
token in the printed link, and uses the standard library alone. Running a
incident needs Mainsheet in the same environment.
"""

import argparse
import email.parser
import email.policy
import hashlib
import hmac
import importlib.util
import io
import json
import tarfile
import tempfile
import zipfile
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from clew.agent import summary, tools
from clew.builder import adapter as builder
from clew.builder import build
from clew.builder import extractor as extractor_builder
from clew.builder.build import ADAPTER, BRIEF, EXTRACTOR, NOISE, VERDICT, WRITTEN
from clew.contracts import Adapter, Extractor, discover
from clew.contracts.registry import approval_of, file_hash, local_folder
from clew.extract.runs import Runs
from clew.ui.page import PAGE
from clew.views.drift_report import label as drift_label

INCIDENT = "incident"
MOST_INCIDENT = 20000
MOST_BODY = 1 << 20
MOST_UPLOAD = 512 << 20  # a record uploaded through the page
MOST_LOG = 400
MOST_RUNS = 200
MOST_ITEMS = 15
MOST_ROOTS = 60
MOST_PLANS = 50        # plans kept for the explain button, newest win
MOST_SHEET = 5 << 20
MOST_NOTES = 2000
MOST_CODE = 100000


DEFINITION = Path(tools.__file__).with_name("agent.yaml")

BUILD = "build"


class Refused(Exception):
    """A request the page should show as a message, with its HTTP status."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ folders

def recognise(path):
    """The engine record at this folder, or None. Only an extractor's word counts."""
    try:
        for extractor in discover(Extractor).values():
            if extractor.records(Path(path)) is not None:
                return Runs(path)
    except (SystemExit, OSError, ValueError):
        return None
    return None


def browse(path):
    """One folder: its subfolders by name, and the runs in it when an engine recorded there."""
    here = Path(path or Path.home()).expanduser()
    try:
        here = here.resolve()
        folders = sorted((c.name for c in here.iterdir()
                          if c.is_dir() and not c.name.startswith(".")), key=str.lower)
    except OSError as bad:
        raise Refused(f"cannot read {here}: {bad.strerror or bad}")
    answer = {"path": str(here), "parent": str(here.parent) if here.parent != here else None,
              "folders": folders, "engine": None, "runs": [],
              "launchlike": any((here / sign).exists() for sign in LAUNCH_SIGNS)}
    found = recognise(here)
    if found:
        newest_first = list(reversed(found.records()))[:MOST_RUNS]
        answer["engine"] = found.kind
        answer["runs"] = [{"name": r["name"], "id": r["id"], "timestamp": r["timestamp"]}
                          for r in newest_first]
        work = here / "work"
        answer["work_root"] = str(work) if work.is_dir() else ""
    else:
        # Someone inside work/ or results/ is one step from the record: point up to it.
        for above in list(here.parents)[:3]:
            near = recognise(above)
            if near:
                answer["nearby"] = {"path": str(above), "name": above.name, "engine": near.kind}
                break
    return answer


# What a launch folder tends to hold, when no extractor recognises it. Only
# such a folder gets the offer to have an extractor written for it.
LAUNCH_SIGNS = ("work", ".nextflow", "nextflow.config", "main.nf", ".snakemake", "Snakefile",
                "cromwell-executions", "cromwell-workflow-logs", "results", "logs")

NOISE_FILE = re.compile(r"^(\._.*|\.DS_Store|Thumbs\.db)$")  # what a laptop leaves in a folder
ARCHIVE_NAME = re.compile(r"\.(zip|tgz|tar|tar\.gz)$", re.IGNORECASE)
MOST_UNPACKED = 2 << 30  # bytes an archive may unpack to


def unpack_archive(data, filename):
    """The files inside an uploaded .tar, .tar.gz, .tgz or .zip, as [(path, bytes)].
    Only regular files, only relative paths; a path that climbs out is refused."""
    members, total = [], 0

    def take(path, size, read):
        nonlocal total
        path = path.replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        path = path.lstrip("/")
        parts = PurePosixPath(path).parts
        if not parts or PurePosixPath(path).is_absolute() or ".." in parts:
            raise Refused(f"refusing the path {path} inside the archive")
        total += size
        if total > MOST_UNPACKED:
            raise Refused(f"the archive unpacks to over {MOST_UNPACKED >> 30} GB")
        members.append((path, read()))

    name = filename.lower()
    try:
        if name.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    if not info.is_dir():
                        take(info.filename, info.file_size, lambda: z.read(info))
        elif name.endswith((".tar", ".tar.gz", ".tgz")):
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as t:
                for info in t:
                    if info.isfile():
                        take(info.name, info.size, lambda: t.extractfile(info).read())
        else:
            raise Refused("the archive must be a .zip, .tar, .tar.gz or .tgz")
    except (zipfile.BadZipFile, tarfile.TarError, EOFError) as bad:
        raise Refused(f"cannot read the archive: {bad}")
    return members


def record_layout(names):
    """
    Where the files of an uploaded folder land: (record name, uploaded path -> path under it).
    A launch folder keeps its name and loses one level; a bare `.lineage` picked on its own
    gets a dated name and stays `.lineage` inside it, where the extractor looks.
    """
    paths = [PurePosixPath(n) for n in names]
    for p in paths:
        if p.is_absolute() or not p.parts or any(part in ("", ".", "..") for part in p.parts):
            raise Refused(f"refusing the path {p}")
    tops = {p.parts[0] for p in paths}
    if len(tops) != 1:
        raise Refused("pick one folder")
    top = tops.pop()
    if top.startswith("."):
        return time.strftime("record-%Y%m%d-%H%M%S"), {str(p): str(p) for p in paths}
    return top, {str(p): str(PurePosixPath(*p.parts[1:])) for p in paths if len(p.parts) > 1}


PROMPT = "Pick the folder your workflow was launched from"
PROMPT_FILE = "Pick the sheet the workflow was launched from"


def picker(start, file=False):
    """The command that opens this system's own folder or file dialog, or None when it has none."""
    prompt = PROMPT_FILE if file else PROMPT
    if sys.platform == "darwin":
        # The start folder travels as an argument, never inside the script text.
        return ["osascript", "-e", "on run argv", "-e", "tell me to activate", "-e",
                f'POSIX path of (choose {"file" if file else "folder"} with prompt "{prompt}" '
                "default location POSIX file (item 1 of argv))", "-e", "end run", "--", start]
    if sys.platform == "win32":
        dialog, chosen = ("OpenFileDialog", "FileName") if file else \
            ("FolderBrowserDialog", "SelectedPath")
        return ["powershell", "-NoProfile", "-Command",
                "Add-Type -AssemblyName System.Windows.Forms; "
                f"$d = New-Object System.Windows.Forms.{dialog}; "
                f"if ($d.ShowDialog() -eq 'OK') {{ $d.{chosen} }}"]
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        # Headless Linux, a Codespace among others: no dialog can open, and
        # tkinter would only fail after a pause. The page then says to type
        # the path or click through the list.
        return None
    if shutil.which("zenity"):
        return ["zenity", "--file-selection", *([] if file else ["--directory"]),
                f"--title={prompt}", f"--filename={start}/"]
    if shutil.which("kdialog"):
        return ["kdialog", "--getopenfilename" if file else "--getexistingdirectory",
                start, "--title", prompt]
    if importlib.util.find_spec("tkinter"):
        ask = "askopenfilename" if file else "askdirectory"
        return [sys.executable, "-c",
                "import sys, tkinter, tkinter.filedialog as f; r = tkinter.Tk(); r.withdraw(); "
                f"r.attributes('-topmost', True); print(f.{ask}(initialdir=sys.argv[1]))", start]
    return None


def pick(start, file=False):
    """A folder, or a file, chosen in the system's dialog on this machine. No path when it was cancelled."""
    begin = Path(start or Path.home()).expanduser()
    command = picker(str(begin if begin.is_dir() else Path.home()), file)
    if command is None:
        return {"available": False, "path": None}
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return {"available": True, "path": None}
    if done.returncode != 0:
        return {"available": False, "path": None}
    chosen = done.stdout.strip()
    there = bool(chosen) and (Path(chosen).is_file() if file else Path(chosen).is_dir())
    return {"available": True, "path": chosen if there else None}


def adapter_flags(adapter):
    """The files an adapter's own kinds need, as the flags its kinds declare."""
    parser = argparse.ArgumentParser(add_help=False, conflict_handler="resolve")
    for kind in adapter.triggers.values():
        kind.add_arguments(parser)
    return [{"flag": action.option_strings[0], "help": action.help or ""}
            for action in parser._actions if action.option_strings]


def adapters():
    try:
        return discover(Adapter)
    except SystemExit:
        return {}


# --------------------------------------------------------------------- jobs

class Job:
    def __init__(self, name, folder, kind=INCIDENT):
        self.name, self.folder, self.kind = name, folder, kind
        self.brief = {}
        self.what, self.sheet, self.record = ADAPTER, None, None
        self.made = lambda: None
        self.state = "running"
        self.error = None
        self.extract = None
        self.log = []
        self.turns = self.cost = None
        self.verified = None
        self.roots = {}

    def say(self, line):
        line = line.rstrip()
        if line and not line.startswith(NOISE) and len(self.log) < MOST_LOG:
            self.log.append(line[:2000])


def read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def status(job):
    """What the job folder shows, step by step. Files decide, not the agent's own account."""
    out = job.folder / "out" / INCIDENT
    record, plan = read(out / "triage.json"), read(out / "plan.json")
    review, decision = read(out / "review.json"), read(out / "decision.json")
    sealed = (out / "bundle" / "manifest.json").is_file()
    outcome = record["outcome"] if record else None
    asked = outcome == "ask" or bool(decision and decision["decision"] == "ask")

    def step(key, label, done, skipped=None, detail=""):
        return {"key": key, "label": label, "detail": skipped or detail,
                "state": "done" if done else "skipped" if skipped else "waiting"}

    steps = [step("extract", "Extract", job.extract is not None,
                  detail=(f"{job.extract['engine']} run {job.extract['run']}: "
                          f"{job.extract['tasks']} tasks" if job.extract else "")),
             step("triage", "Triage", record is not None,
                  detail=f"{outcome}: {record['reason']}" if record else "")]
    if outcome == "held":
        steps.append(step("review", "Recommendation", review is not None,
                          detail=review["verdict"] if review else ""))
        said = (f"{decision['decision']}" + (f" {decision['trigger']}" if decision["trigger"] else "")
                + f", by {decision['actor']}") if decision else ""
        steps.append(step("decision", "Your decision", decision is not None, detail=said))
    why = None if asked or outcome is None else \
        "a person dismissed the incident" if decision else \
        "after your decision" if outcome == "held" else f"the incident was {outcome}"
    steps += [step("impact", "Impact", plan is not None, skipped=None if plan else why,
                   detail=(f"{plan['tasks_affected']} of {plan['tasks_total']} tasks affected"
                           if plan else "")),
              step("evidence", "Evidence", sealed,
                   skipped=None if sealed or (decision and decision["decision"] == "dismiss") else why,
                   detail="sealed" + ("" if job.verified is None else
                                      ", verified" if job.verified else ", NOT verified")
                   if sealed else "")]
    steps.append(step("act", "Act", False, skipped="acting on a plan is not available yet"))

    # A held incident with no decision is not a step that failed or was skipped:
    # it is open, and waits for a person.
    open_decision = outcome == "held" and decision is None and job.state != "running"
    for one in steps:
        if one["key"] == "decision" and one["state"] == "waiting" and open_decision:
            one["state"], one["detail"] = "open", "waiting for you"
    waiting = next((s for s in steps if s["state"] == "waiting"), None)
    if waiting and job.state == "running":
        waiting["state"] = "running"
    elif waiting and job.state == "failed":
        waiting["state"] = "failed"
    for later in steps:
        if later["state"] == "waiting":
            later["state"] = "skipped"
            later["detail"] = later["detail"] or (
                "after your decision" if open_decision else
                "not yet" if job.state == "running" else "not reached")

    answer = {"job": job.name, "state": job.state, "error": job.error, "steps": steps,
              "extract": job.extract, "turns": job.turns, "cost": job.cost,
              "log": job.log, "folder": str(job.folder), "triage": None, "plan": None,
              "bundle": str(out / "bundle") if sealed else None,
              "verified": job.verified, "review": review, "decision": decision,
              "choices": None}
    if record:
        answer["triage"] = {key: record[key] for key in
                            ("outcome", "choice", "confidence", "trigger", "reason",
                             "backend", "model")}
        answer["triage"]["options"] = len(record["options"])
        answer["triage"]["meaning"] = record.get("meaning")
        answer["triage"]["notes"] = record.get("notes") or []
        answer["triage"]["pipeline"] = record.get("pipeline")
        others = sorted(((p, option) for option, p in (record.get("probabilities") or {}).items()
                         if option != record["choice"] and p >= 0.01), reverse=True)
        answer["triage"]["others"] = [{"option": option, "probability": p}
                                      for p, option in others[:4]]
    if open_decision:
        # What a person may ask: only what triage offered. The agent's suggestion
        # comes first, then the classifier's own pick.
        suggested = (review or {}).get("trigger") or record["options"].get(record["choice"])
        answer["choices"] = {"triggers": sorted(record["options"].values()), "suggested": suggested}
    if plan:
        answer["plan"] = {"trigger": plan["trigger"], "policy": plan["policy_version"],
                          "tasks_affected": plan["tasks_affected"],
                          "tasks_total": plan["tasks_total"], "actions": plan["actions"],
                          "items": [{"process": item["process"],
                                     "action": item["action"] or "UNDETERMINED",
                                     "rule": item["rule"] or ""}
                                    for item in plan["plan"][:MOST_ITEMS]],
                          "more": max(0, len(plan["plan"]) - MOST_ITEMS)}
    return answer


def text_of(path):
    try:
        return Path(path).read_text(errors="replace")[:MOST_CODE]
    except OSError:
        return None


DRIFTED = "DRIFTED"
# The order the command prints verdicts in: findings first, the reassuring last.
DRIFT_ORDER = ("UNSETTLED", "DOWNSTREAM", "UNVERIFIED", "REPRODUCED", "ADDED", "REMOVED")
RECLAIM_ORDER = ("REDUNDANT", "SUPERSEDED", "FAILED", "INTERMEDIATE", "KEEP", "GONE")
PROPOSED = ("REDUNDANT", "SUPERSEDED", "FAILED", "INTERMEDIATE")
KEEP = "KEEP"


def reclaim_answer(plan):
    """
    A reclaim plan, as `clew reclaim --json` wrote it, the way the page shows
    it: what can go and what it weighs, each verdict by process with its
    bytes, the causes that withheld the rest, and what the answer rests on.
    """
    items = plan["plan"]
    tally = {}
    for item in items:
        by_process = tally.setdefault(item["verdict"], {})
        cell = by_process.setdefault(item["process"].split(":")[-1], [0, 0])
        cell[0] += 1
        cell[1] += item.get("bytes", 0)
    meanings = plan.get("meanings") or {}
    groups = [{"verdict": verdict, "meaning": meanings.get(verdict, ""),
               "processes": [{"process": p, "count": n, "bytes": b} for p, (n, b) in
                             sorted(tally[verdict].items(), key=lambda kv: (-kv[1][1], kv[0]))]}
              for verdict in RECLAIM_ORDER if verdict in tally]
    kept = [i for i in items if i["verdict"] == KEEP]
    causes = {}
    for item in kept:
        cause = item.get("cause") or item["reason"]
        causes.setdefault(cause, {})
        name = item["process"].split(":")[-1]
        causes[cause][name] = causes[cause].get(name, 0) + 1
    withheld = [{"cause": cause, "count": sum(by.values()),
                 "processes": ", ".join(f"{n} {p}" for p, n in
                                        sorted(by.items(), key=lambda kv: (-kv[1], kv[0])))}
                for cause, by in sorted(causes.items(), key=lambda kv: -sum(kv[1].values()))]
    size, verdicts = plan.get("bytes", {}), plan["verdicts"]
    return {"run": plan.get("run"), "work_root": plan["work_root"], "results": plan.get("results"),
            "intermediates": plan.get("intermediates", False), "tasks_total": plan["tasks_total"],
            "verdicts": verdicts, "bytes": size,
            "reclaimable_bytes": sum(size.get(v, 0) for v in PROPOSED),
            "reclaimable_dirs": sum(verdicts.get(v, 0) for v in PROPOSED),
            "groups": groups, "withheld": withheld, "caveats": plan["caveats"]}


def drift_answer(plan):
    """
    A drift plan, as `clew drift --json` wrote it, the way the page shows it:
    the roots as rows, since they are the finding, every other verdict
    grouped by cause and counted by process, and what the answer rests on.
    """
    items = plan["plan"]
    roots = [i for i in items if i["verdict"] == DRIFTED]
    tally = {}
    for item in items:
        if item["verdict"] != DRIFTED:
            by_process = tally.setdefault(item["verdict"], {})
            name = item["process"].split(":")[-1]
            by_process[name] = by_process.get(name, 0) + 1
    meanings = plan.get("meanings") or {}
    groups = [{"verdict": verdict, "meaning": meanings.get(verdict, ""),
               "processes": [{"process": p, "count": n} for p, n in
                             sorted(tally[verdict].items(), key=lambda kv: (-kv[1], kv[0]))]}
              for verdict in DRIFT_ORDER if verdict in tally]
    return {"before": plan["before"], "after": plan["after"], "tasks_total": plan["tasks_total"],
            "verdicts": plan["verdicts"],
            "roots": [{"task": drift_label(i), "process": i["process"].split(":")[-1],
                       "cause": i["cause"], "files": i.get("files", [])}
                      for i in roots[:MOST_ROOTS]],
            "more_roots": max(0, len(roots) - MOST_ROOTS),
            "groups": groups, "ignored": plan["ignored_outputs"],
            "caveats": plan["caveats"], "coverage": plan.get("coverage") or []}


def build_status(job, providers):
    """What a build's folder shows, step by step: the brief, what the agent wrote, the verdict, the approval."""
    work, live = job.folder / "work", job.made()
    code, tests = WRITTEN[job.what]
    wrote, tested = ((work / name).is_file() or bool(live and (live / name).is_file())
                     for name in (code, tests))
    verdict = read(job.folder / VERDICT)
    passed = bool(verdict and verdict["passed"])
    target = builder.installed_as(providers, job.brief["name"])
    approval = approval_of(target)[0] if target.is_file() else None
    # An approval counts for this build only when it names the file the judge saw.
    approval = approval if approval and verdict and approval.get("sha256") == verdict["sha256"] else None

    def step(key, label, done, detail="", skipped=None):
        return {"key": key, "label": label, "detail": skipped or detail,
                "state": "done" if done else "skipped" if skipped else "waiting"}

    checks = verdict["checks"] if verdict else []
    missed = sum(1 for check in checks if not check["passed"])
    # An extractor is built from the record itself, so there is no graph to extract first.
    first = [] if job.what == EXTRACTOR else [
        step("extract", "Extract", job.extract is not None,
             detail=(f"{job.extract['engine']} run {job.extract['run']}: "
                     f"{job.extract['tasks']} tasks" if job.extract else ""))]
    asked = f"the record in {job.record.name}" if job.what == EXTRACTOR else \
        f"one {job.brief['kind']} per id, " + (f"sheet {job.sheet.name}" if job.sheet else "no sheet")
    steps = first + [
             step("brief", "Brief", (job.folder / BRIEF).is_file(), detail=asked),
             step(job.what, job.what.capitalize(), wrote,
                  detail="written by the agent" if wrote else ""),
             step("tests", "Its tests", tested, detail="written by the agent" if tested else ""),
             step("judge", "Judge", passed,
                  detail=f"passed, {len(checks)} checks" if passed else ""),
             step("approval", "Approval", approval is not None,
                  detail=(f"by {approval['actor']}, installed as {target.name}" if approval else ""),
                  skipped=None if passed or not verdict else "needs a verdict that passed")]
    for one in steps:
        if one["key"] == "judge" and verdict and not passed:
            one["state"], one["detail"] = "failed", f"{missed} of {len(checks)} checks failed"
        if one["key"] == "approval" and one["state"] == "waiting" and passed and job.state != "running":
            one["state"], one["detail"] = "open", "waiting for you"
    waiting = next((s for s in steps if s["state"] == "waiting"), None)
    if waiting and job.state in ("running", "failed"):
        waiting["state"] = job.state
    for later in steps:
        if later["state"] == "waiting":
            later["state"] = "skipped"
            later["detail"] = "not yet" if job.state == "running" else "not reached"

    return {"job": job.name, "kind": BUILD, "state": job.state, "error": job.error, "steps": steps,
            "extract": job.extract, "turns": job.turns, "cost": job.cost, "log": job.log,
            "folder": str(job.folder), "what": job.what, "brief": job.brief, "verdict": verdict,
            "code": text_of(work / code), "tests": text_of(work / tests),
            "approval": approval, "can_install": passed and approval is None and job.state != "running",
            "can_judge": wrote and approval is None and job.state != "running"}


class App:
    """The state behind the page: where jobs live, the one job that may run, the token."""

    def __init__(self, home, token=None, launch=None):
        self.home = Path(home).expanduser().resolve()
        self.token = token or secrets.token_urlsafe(24)
        self.launch = launch or run_agent
        # Providers a person approved live here, and every command loads from here.
        self.providers = local_folder()
        self.jobs = {}
        self.lock = threading.Lock()
        # Plans the page laid out, by hash, so the explain button can name
        # one; and the model's reading of each, so a second click is free.
        self.plans = {}
        self.readings = {}

    def add_record(self, files):
        """Files uploaded from a person's own machine, [(path as uploaded, bytes)], written under
        records/ in Clew's home so the page can open them like any folder here."""
        files = [(name, data) for name, data in files if not NOISE_FILE.match(PurePosixPath(name).name)
                 and not name.startswith("__MACOSX/")]
        if not files:
            raise Refused("nothing in the upload looks like a record")
        name, placed = record_layout([name for name, _ in files])
        root = self.home / "records" / re.sub(r"[^A-Za-z0-9._-]", "_", name)
        if root.exists():
            raise Refused(f"{root} is already here; open it from the list, or remove it first")
        written = 0
        for uploaded, data in files:
            under = placed.get(uploaded)
            if not under:
                continue
            dest = root / under
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            written += 1
        # Validate now, not later: a folder no extractor recognises is not kept,
        # and a record whose checksums do not hash content is said so at once.
        found = recognise(root)
        if not found:
            shutil.rmtree(root, ignore_errors=True)
            raise Refused("No run record in that folder. Clew looks for the engine's own record: "
                          "for Nextflow a .lineage folder, written with lineage.enabled = true.")
        notes = []
        try:
            graph = found.load()
            if any("hecksum" in note for note in graph.get("coverage", [])):
                notes = ["Recorded without content checksums. Impact and triage work; drift and "
                         "reclaim need clew digest first, or a run recorded with NXF_CACHE_MODE=DEEP."]
        except (SystemExit, OSError, ValueError, KeyError):
            pass
        return {"path": str(root), "files": written, "engine": found.kind,
                "runs": len(found.records()), "notes": notes}

    def browse(self, path):
        """A folder, as browse() reads it; or the path of an archive on this machine, which is
        unpacked into records/ and opened, for someone who typed the zip's path into the box."""
        here = Path(path or "").expanduser()
        if here.is_file() and ARCHIVE_NAME.search(here.name):
            if here.stat().st_size > MOST_UPLOAD:
                raise Refused(f"{here.name} is over {MOST_UPLOAD >> 20} MB")
            added = self.add_record(unpack_archive(here.read_bytes(), here.name))
            answer = browse(added["path"])
            answer["notes"] = added["notes"]
            return answer
        return browse(path)

    def state(self):
        return {"mainsheet": importlib.util.find_spec("mainsheet") is not None,
                "classifier": "jev" if os.environ.get("TYPESAFE_API_KEY") else "name",
                "api_key": bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
                # Through a gateway: the host the agent's calls go to, and whether Jev's do.
                "proxy": gateway_host(), "jev_proxy": bool(os.environ.get("CLEW_JEV_ENDPOINT")),
                # An engine can be picked by folder only when its extractor lists runs.
                "engines": [{"name": name, "folder": type(e).records is not Extractor.records}
                            for name, e in sorted(discover(Extractor).items())],
                "adapters": [{"name": name, "kinds": sorted(a.triggers), "flags": adapter_flags(a)}
                             for name, a in sorted(adapters().items())],
                "local": self.local(), "providers": str(self.providers),
                "builder": {"agent": builder.AGENT, "model": builder.MODEL},
                "home": str(self.home), "start": str(Path.cwd()),
                "dialog": picker(str(Path.cwd())) is not None}

    def local(self):
        """Each provider file in the local folder and whether it may load. Nothing is imported to say so."""
        listed = []
        for source in sorted(self.providers.glob("*.py")):
            approval, problem = approval_of(source)
            approval = approval or {}
            listed.append({"file": source.name, "name": approval.get("name"),
                           "actor": approval.get("actor"), "approved_at": approval.get("approved_at"),
                           "written_by": approval.get("written_by"), "problem": problem})
        return listed

    def chosen(self, body):
        """The run a request names, checked against the folder's own record."""
        found = recognise(body.get("path") or "")
        if not found:
            raise Refused("that folder holds no run record Clew can read")
        run = body.get("run") or ""
        if run not in {r["name"] for r in found.records()}:
            raise Refused(f"no run named {run!r} in that folder")
        return found, run

    def drift(self, body):
        """
        Where two runs of the record part ways. The command answers, as it
        does on a terminal, with no agent and no job folder; the page only
        lays its plan out.
        """
        found, after = self.chosen(body)
        before = body.get("before") or ""
        if before not in {r["name"] for r in found.records()}:
            raise Refused(f"no run named {before!r} in that folder")
        if before == after:
            raise Refused("pick two different runs")
        ignore = [g.strip() for g in (body.get("ignore") or "").split(",") if g.strip()]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "drift.json"
            code, _, err = tools.run_clew("drift", "--runs", found.path, "--before", before,
                                          "--after", after, "--json", out,
                                          *[flag for glob in ignore for flag in ("--ignore", glob)])
            if code != 0:
                lines = err.strip().splitlines()
                raise Refused(lines[-1] if lines else f"clew drift exited {code}")
            return self.keep("drift", drift_answer(json.loads(out.read_text())))

    def reclaim(self, body):
        """
        Which work directories of the run can go, with the proof. Needs the
        run's work tree on this machine, and the results tree to prove a
        published copy; the command answers and nothing is deleted.
        """
        found, run = self.chosen(body)
        folders = self.folders(body)
        flags = ["--work-root", folders["work_root"]]
        if folders["results"]:
            flags += ["--results", folders["results"]]
        if body.get("intermediates"):
            flags.append("--intermediates")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "reclaim.json"
            code, _, err = tools.run_clew("reclaim", "--runs", found.path, "--run", run,
                                          *flags, "--json", out)
            if code != 0:
                lines = err.strip().splitlines()
                raise Refused(lines[-1] if lines else f"clew reclaim exited {code}")
            answer = reclaim_answer(json.loads(out.read_text()))
            # The command's warnings are about placing directories; they belong beside the limits.
            answer["warnings"] = [line.removeprefix("clew: ") for line in err.strip().splitlines()
                                  if line.startswith("clew: ")]
            return self.keep("reclaim", answer)

    def folders(self, body):
        """The work and results folders a request names, checked to exist here; work is required."""
        folders = {}
        for key in ("work_root", "results"):
            value = (body.get(key) or "").strip()
            if value and not value.startswith("s3://") and not Path(value).expanduser().is_dir():
                raise Refused(f"{value} is not a folder on this machine")
            folders[key] = value if value.startswith("s3://") else str(Path(value).expanduser()) if value else ""
        if not folders["work_root"]:
            raise Refused("name the run's work folder: the files to read are there")
        return folders

    def digest(self, body):
        """
        Content digests for the run's files, read once and kept in a sidecar
        beside the record, for a record the engine wrote without them. The
        command does the reading; the record itself is not touched.
        """
        found, run = self.chosen(body)
        folders = self.folders(body)
        flags = ["--work-root", folders["work_root"]]
        if folders["results"]:
            flags += ["--results", folders["results"]]
        code, out, err = tools.run_clew("digest", "--runs", found.path, "--run", run, *flags)
        if code != 0:
            lines = err.strip().splitlines()
            raise Refused(lines[-1] if lines else f"clew digest exited {code}")
        return {"lines": [line for line in out.strip().splitlines() if line.strip()]}

    def keep(self, kind, answer):
        """The answer with a `plan` id the explain button can send back."""
        key = hashlib.sha256(json.dumps(answer, sort_keys=True).encode()).hexdigest()[:16]
        with self.lock:
            self.plans[key] = (kind, answer)
            for old in list(self.plans)[:-MOST_PLANS]:
                del self.plans[old]
        return dict(answer, plan=key)

    def explain(self, body):
        """A model's reading of a plan the page showed, in words. Cached per plan."""
        key = body.get("plan") or ""
        if key not in self.plans:
            raise Refused("that plan is no longer here; run it again", 404)
        if key not in self.readings:
            kind, answer = self.plans[key]
            try:
                self.readings[key] = summary.explain(kind, answer)
            except summary.Refused as bad:
                raise Refused(str(bad))
        return self.readings[key]

    def new_job(self, kind=INCIDENT):
        """A job and its folder. One at a time, of either kind."""
        with self.lock:
            if any(job.state == "running" for job in self.jobs.values()):
                raise Refused("a run is still going; wait for it to finish", 409)
            name = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
            folder = self.home / "jobs" / name
            folder.mkdir(parents=True)
            job = self.jobs[name] = Job(name, folder, kind)
        return job

    def start(self, body):
        incident = (body.get("incident") or "").strip()
        if not incident:
            raise Refused("write the incident first")
        if len(incident) > MOST_INCIDENT:
            raise Refused(f"the incident is longer than {MOST_INCIDENT} characters")
        found, run = self.chosen(body)
        roots = {}
        for key, variable in (("work_root", "CLEW_WORK_ROOT"), ("results", "CLEW_RESULTS")):
            value = (body.get(key) or "").strip()
            if value and not value.startswith("s3://") and not Path(value).expanduser().is_dir():
                raise Refused(f"{value} is not a folder")
            if value:
                roots[variable] = value if value.startswith("s3://") else str(Path(value).expanduser())
        pipeline = body.get("pipeline") or ""
        if pipeline:
            known = adapters()
            if pipeline not in known:
                raise Refused(f"no adapter named {pipeline!r} is installed")
            allowed = {flag["flag"] for flag in adapter_flags(known[pipeline])}
            given = []
            for flag, value in (body.get("adapter_args") or {}).items():
                value = str(value or "").strip()
                if not value:
                    continue
                if flag not in allowed:
                    raise Refused(f"{flag} is not a setting of the {pipeline} adapter")
                # A file that exists cannot be read as another flag by the command.
                if not Path(value).expanduser().is_file():
                    raise Refused(f"{value} is not a file")
                given += [flag, str(Path(value).expanduser())]
            roots["CLEW_PIPELINE"] = pipeline
            roots["CLEW_ADAPTER_ARGS"] = shlex.join(given)
        job = self.new_job()
        (job.folder / "inbox").mkdir()
        job.roots = roots
        threading.Thread(target=self.work, args=(job, found, run, incident, roots),
                         daemon=True).start()
        return {"job": job.name}

    def work(self, job, found, run, incident, roots):
        try:
            graph = found.load(run)
            (job.folder / "graph.json").write_text(json.dumps(graph))
            (job.folder / "inbox" / f"{INCIDENT}.txt").write_text(incident + "\n")
            job.extract = {"engine": found.kind, "run": run, "tasks": len(graph["tasks"]),
                           "edges": len(graph["edges"]), "coverage": graph.get("coverage") or []}
            self.launch(job, self.home, roots)
            bundle = job.folder / "out" / INCIDENT / "bundle"
            if (bundle / "manifest.json").is_file():
                job.verified = tools.run_clew("evidence", "verify", bundle)[0] == 0
            if job.state == "running":
                job.state = "finished"
        except (SystemExit, Exception) as bad:      # a job must end in a state the page can show
            job.state, job.error = "failed", str(bad) or repr(bad)

    def decide(self, body):
        """A person's decision on a held incident. Asking a trigger carries on to a sealed plan."""
        job = self.jobs.get(body.get("job") or "")
        if not job:
            raise Refused("no such run", 404)
        if job.state == "running":
            raise Refused("the run is still going; decide when it has finished", 409)
        action = body.get("action")
        if action not in ("ask", "dismiss"):
            raise Refused("the decision is ask or dismiss")
        try:
            tools.decide(job.folder, INCIDENT, (body.get("actor") or "").strip(),
                         ask=(body.get("trigger") or "") if action == "ask" else None,
                         reason=body.get("reason") or "")
        except tools.ToolError as bad:
            raise Refused(str(bad))
        job.state, job.error = "running", None
        threading.Thread(target=self.carry_on if action == "ask" else self.seal_dismissal,
                         args=(job,), daemon=True).start()
        return status(job)

    def seal_dismissal(self, job):
        """After a person dismisses: the dismissal sealed as evidence, no model and no plan."""
        try:
            job.verified = tools.seal(job.folder, INCIDENT)["verified"]
            job.state = "finished"
        except (tools.ToolError, SystemExit, Exception) as bad:
            job.state, job.error = "failed", str(bad) or repr(bad)

    def carry_on(self, job):
        """After a person asks a trigger: the plan and its seal. Two commands, no model."""
        try:
            env = dict(os.environ, **job.roots)
            tools.impact(job.folder, INCIDENT, env=env)
            job.verified = tools.seal(job.folder, INCIDENT)["verified"]
            job.state = "finished"
        except (tools.ToolError, SystemExit, Exception) as bad:
            job.state, job.error = "failed", str(bad) or repr(bad)

    def job(self, body):
        job = self.jobs.get(body.get("job") or "")
        if not job:
            raise Refused("no such run", 404)
        return build_status(job, self.providers) if job.kind == BUILD else status(job)

    def build(self, body):
        """Start an agent on an adapter for one run. What it writes loads nowhere until a person approves it."""
        if body.get("what") == EXTRACTOR:
            return self.build_extractor(body)
        name, kind = (body.get("name") or "").strip(), (body.get("kind") or "").strip()
        try:
            kind = ", ".join(builder.check_brief(name, kind))
        except builder.Refused as bad:
            raise Refused(str(bad))
        if name in adapters() or builder.installed_as(self.providers, name).exists():
            raise Refused(f"an adapter named {name} is already installed; choose another name")
        found, run = self.chosen(body)
        sheet = (body.get("sheet") or "").strip()
        if sheet:
            sheet = Path(sheet).expanduser()
            if not sheet.is_file():
                raise Refused(f"{sheet} is not a file")
            if sheet.stat().st_size > MOST_SHEET:
                raise Refused(f"the sheet is larger than {MOST_SHEET >> 20} MB")
        notes = (body.get("notes") or "").strip()
        if len(notes) > MOST_NOTES:
            raise Refused(f"the notes are longer than {MOST_NOTES} characters")
        job = self.new_job(BUILD)
        job.brief = {"name": name, "kind": kind, "removable": bool(body.get("removable")),
                     "separable": bool(body.get("separable")), "notes": notes}
        threading.Thread(target=self.construct, args=(job, found, run, sheet or None),
                         daemon=True).start()
        return {"job": job.name}

    def build_extractor(self, body):
        """Start an agent on an extractor for a folder no installed extractor reads."""
        name = (body.get("name") or "").strip()
        try:
            extractor_builder.check_brief(name)
        except builder.Refused as bad:
            raise Refused(str(bad))
        if name in discover(Extractor) or builder.installed_as(self.providers, name).exists():
            raise Refused(f"an extractor named {name} is already installed; choose another name")
        given = (body.get("record") or "").strip()
        if not given or not Path(given).expanduser().is_dir():
            raise Refused("pick the folder the engine wrote its record in")
        record = Path(given).expanduser().resolve()
        # The agent may read everything under it, so not a folder that holds the rest of the machine.
        if record == Path.home() or record in Path.home().parents:
            raise Refused("pick the run's own folder, not one that holds everything else")
        found = recognise(record)
        if found:
            raise Refused(f"Clew already reads that folder as a {found.kind} record")
        notes = (body.get("notes") or "").strip()
        if len(notes) > MOST_NOTES:
            raise Refused(f"the notes are longer than {MOST_NOTES} characters")
        job = self.new_job(BUILD)
        job.what, job.record = EXTRACTOR, record
        job.brief = {"name": name, "notes": notes}
        threading.Thread(target=self.construct, args=(job, None, None, None), daemon=True).start()
        return {"job": job.name}

    def construct(self, job, found, run, sheet):
        """Brief the agent, run it, then check what it left. The same steps as `clew build`."""
        try:
            if job.what == ADAPTER:
                graph = found.load(run)
                (job.folder / "graph.json").write_text(json.dumps(graph))
                job.extract = {"engine": found.kind, "run": run, "tasks": len(graph["tasks"]),
                               "edges": len(graph["edges"]), "coverage": graph.get("coverage") or []}
                job.sheet = build.keep_sheet(job.folder, sheet) if sheet else None
            definition, job.made = build.brief(job.folder, self.home / "mainsheet", job.what, job.brief,
                                               sheet=job.sheet, record=job.record)
            self.launch(job, self.home, {}, definition)
            if not build.collect(job.folder, job.what, job.made()):
                if job.state == "running":
                    job.state, job.error = "failed", f"the agent finished and wrote no {job.what}"
                return
            build.check(job.folder, job.what, job.brief["name"], sheet=job.sheet, record=job.record)
            if job.state == "running":
                job.state = "finished"
        except (SystemExit, Exception) as bad:      # a job must end in a state the page can show
            job.state, job.error = "failed", str(bad) or repr(bad)

    def judge(self, body):
        """The conformance check again, on what the agent left. No agent runs."""
        job = self.jobs.get(body.get("job") or "")
        if not job or job.kind != BUILD:
            raise Refused("no such build", 404)
        if job.state == "running":
            raise Refused("the build is still going", 409)
        with self.lock:
            if any(other.state == "running" for other in self.jobs.values()):
                raise Refused("a run is still going; wait for it to finish", 409)
            job.state, job.error = "running", None

        def again():
            try:
                build.rejudge(job.folder)
                job.state = "finished"
            except (build.Refused, SystemExit, Exception) as bad:
                job.state, job.error = "failed", str(bad) or repr(bad)
        threading.Thread(target=again, daemon=True).start()
        return build_status(job, self.providers)

    def install(self, body):
        """A person's approval of a built provider: their name, and the hash of the file the judge saw."""
        job = self.jobs.get(body.get("job") or "")
        if not job or job.kind != BUILD:
            raise Refused("no such build", 404)
        if job.state == "running":
            raise Refused("the build is still going; approve when it has finished", 409)
        work, verdict = job.folder / "work", read(job.folder / VERDICT)
        if not verdict:
            raise Refused("this build has no verdict, so there is nothing to approve")
        code = WRITTEN[job.what][0]
        if not (work / code).is_file() or file_hash(work / code) != verdict["sha256"]:
            raise Refused(f"the {job.what} changed after the judge saw it; build it again")
        try:
            builder.install(work, self.providers, job.brief["name"], body.get("actor") or "", verdict,
                            written=code, agent=build.AGENTS[job.what])
        except builder.Refused as bad:
            raise Refused(str(bad))
        return build_status(job, self.providers)


def run_agent(job, home, roots, definition=DEFINITION):
    """One agent run over the job folder, its output kept line by line."""
    def say(line):
        job.say(line)
        if build.spent(line):
            job.turns, job.cost = build.spent(line)
    try:
        status = build.mainsheet(definition, home / "mainsheet", say,
                                 env=dict(roots, CLEW_AGENT_DIR=str(job.folder)))
    except build.Refused as bad:
        raise Refused(str(bad))
    if status != 0:
        job.state = "failed"
        job.error = next((line for line in reversed(job.log) if "failed" in line or "rror" in line),
                         f"the agent exited with status {status}")
        if "authenticate" in job.error and not (os.environ.get("ANTHROPIC_API_KEY")
                                               or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            job.error += ". Set ANTHROPIC_API_KEY in the terminal that starts clew ui."


# ------------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    server_version = "clew-ui"

    def log_message(self, *args):
        pass

    def send(self, status, body, kind="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", kind + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def local(self):
        """
        Only a request addressed to this machine by name, or through the one
        forwarded host the server was started for: a rebound hostname is refused.
        """
        host = self.headers.get("Host") or ""
        # The port is not checked for the loopback names: a container publishes
        # the server's port under whatever port the person chose.
        name = host.rsplit(":", 1)[0]
        return name in ("127.0.0.1", "localhost") or host.removesuffix(":443") in self.server.hosts

    def do_GET(self):
        if not self.local():
            return self.send(403, {"error": "not a local request"})
        if self.path.split("?", 1)[0] != "/":
            return self.send(404, {"error": "not found"})
        self.send(200, PAGE.encode("utf-8"), "text/html")

    def do_POST(self):
        app = self.server.app
        given = self.headers.get("X-Clew-Token") or ""
        if not self.local() or not hmac.compare_digest(given, app.token):
            return self.send(403, {"error": "open the link the terminal printed"})
        if self.path == "/api/upload":
            return self.upload(app)
        routes = {"/api/state": lambda body: app.state(),
                  "/api/browse": lambda body: app.browse(body.get("path")),
                  "/api/pick": lambda body: pick(body.get("path"), body.get("what") == "file"),
                  "/api/run": app.start, "/api/job": app.job, "/api/decide": app.decide,
                  "/api/build": app.build, "/api/install": app.install,
                  "/api/judge": app.judge, "/api/drift": app.drift,
                  "/api/reclaim": app.reclaim, "/api/digest": app.digest,
                  "/api/explain": app.explain}
        route = routes.get(self.path)
        if not route:
            return self.send(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MOST_BODY:
                raise Refused("request too large", 413)
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise Refused("the request must be a JSON object")
            self.send(200, route(body))
        except Refused as bad:
            self.send(bad.status, {"error": str(bad)})
        except ValueError:
            self.send(400, {"error": "the request is not JSON"})


    def upload(self, app):
        """A folder sent by the browser as multipart/form-data, one part per file, its relative
        path as the filename. Parsed with the email package: it is the same wire format."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MOST_UPLOAD:
                raise Refused(f"the upload is over {MOST_UPLOAD >> 20} MB; pick the .lineage folder itself", 413)
            kind = self.headers.get("Content-Type") or ""
            if not kind.startswith("multipart/form-data"):
                raise Refused("the upload must be multipart/form-data")
            head = f"Content-Type: {kind}\r\nMIME-Version: 1.0\r\n\r\n".encode()
            message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(head + self.rfile.read(length))
            # One "archive" part, a folder packed by the page or by hand, is unpacked here.
            # Otherwise each file is preceded by a "path" field carrying its relative path,
            # since a browser may rewrite the file name it sends; the file name serves without.
            files, path = [], None
            for part in message.iter_parts():
                field = part.get_param("name", header="content-disposition")
                if field == "archive" and part.get_filename():
                    files = unpack_archive(part.get_payload(decode=True) or b"", part.get_filename())
                    break
                if part.get_filename():
                    files.append((path or part.get_filename(), part.get_payload(decode=True) or b""))
                    path = None
                elif field == "path":
                    path = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
            if not files:
                raise Refused("nothing was uploaded")
            self.send(200, app.add_record(files))
        except Refused as bad:
            self.send(bad.status, {"error": str(bad)})


def gateway_host(env=os.environ):
    """The host ANTHROPIC_BASE_URL names when the agent is sent through a gateway, else None."""
    base = env.get("ANTHROPIC_BASE_URL") or ""
    return urlparse(base).hostname if base and env.get("ANTHROPIC_AUTH_TOKEN") else None


def forwarded_host(port, env=os.environ):
    """The host a Codespace forwards this port through, from the variables GitHub sets, or None."""
    name, domain = env.get("CODESPACE_NAME"), env.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN")
    return f"{name}-{port}.{domain}" if name and domain else None


def serve(home, port=0, token=None, hosts=(), bind="127.0.0.1"):
    """
    A server bound to this machine. Returns it unstarted, with its link.
    `hosts` are the forwarded names it also answers to, none by default.
    `bind` is the address to listen on: the loopback, or every interface
    inside a container, where the loopback is not reachable from outside.
    """
    server = ThreadingHTTPServer((bind, port), Handler)
    server.app = App(home, token)
    server.hosts = frozenset(hosts)
    shown = "localhost" if bind == "0.0.0.0" else bind
    return server, f"http://{shown}:{server.server_address[1]}/?t={server.app.token}"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="clew ui",
                                     description="Clew in a browser tab, on this machine only.")
    parser.add_argument("--port", type=int, default=8770, help="default 8770; 0 picks a free one")
    parser.add_argument("--home", default="~/.clew/ui",
                        help="where runs and the agent's records are kept (default ~/.clew/ui)")
    parser.add_argument("--no-browser", action="store_true", help="print the link and do not open it")
    parser.add_argument("--forwarded", action="store_true",
                        help="also answer through the host a GitHub Codespace forwards the port to; "
                             "the token still guards every request")
    parser.add_argument("--bind", default="127.0.0.1",
                        help="address to listen on; 0.0.0.0 inside a container, where the loopback "
                             "cannot be reached from outside (default 127.0.0.1)")
    parser.add_argument("--host", action="append", default=[], metavar="NAME[:PORT]",
                        help="a further host name to answer to, when the page is reached through one; "
                             "may repeat; the token still guards every request")
    args = parser.parse_args(argv)

    if forwarded_host(args.port) and not args.forwarded:
        # A Codespace: the only browser is on the other side of the port
        # forward, and nothing here can open it.
        args.forwarded, args.no_browser = True, True
    hosts = list(args.host)
    if args.forwarded:
        if not args.port:
            raise SystemExit("--forwarded needs a fixed --port: the forwarded host carries it")
        host = forwarded_host(args.port)
        if not host:
            raise SystemExit("--forwarded needs CODESPACE_NAME and "
                             "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN, which a Codespace sets")
        hosts.append(host)
    try:
        server, link = serve(args.home, args.port, hosts=hosts, bind=args.bind)
    except OSError as bad:
        raise SystemExit(f"cannot listen on port {args.port}: {bad.strerror or bad}")
    if args.forwarded:
        link = f"https://{hosts[-1]}/?t={server.app.token}"
    print(f"Clew UI: {link}\nruns are kept in {server.app.home}\n"
          f"approved providers are kept in {server.app.providers}\nCtrl-C stops it", flush=True)
    if not args.no_browser:
        webbrowser.open(link)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
