"""
Clew in a browser tab, on this machine only.

    clew ui                       # prints a link and opens it
    clew ui --port 8770 --home ~/.clew/ui --no-browser

Pick a run folder, pick a run, paste a notice and press Run. The page
extracts the run's graph, then hands the notice to the Clew agent, which
triages it, plans and seals. Each step shows when its file appears on
disk, so the page reports what happened and not what the agent says
happened.

The server listens on 127.0.0.1, answers only requests that carry the
token in the printed link, and uses the standard library alone. Running a
notice needs Mainsheet in the same environment.
"""

import argparse
import hmac
import importlib.util
import json
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
from pathlib import Path

from clew.agent import mainsheet as tools
from clew.contracts import Adapter, Extractor, discover
from clew.extract.runs import Runs
from clew.ui.page import PAGE

NOTICE = "notice"
MOST_NOTICE = 20000
MOST_BODY = 1 << 20
MOST_LOG = 400
MOST_RUNS = 200
MOST_ITEMS = 15

# Tracing chatter from an agent run with no trace viewer listening.
NOISE = ("Transient error HTTPConnectionPool", "Failed to export span batch")
SPENT = re.compile(r"turns: (\d+)\s+cost_usd: ([0-9.]+|None)")

DEFINITION = Path(tools.__file__).with_name("agent.yaml")


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
              "folders": folders, "engine": None, "runs": []}
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


PROMPT = "Pick the folder your workflow was launched from"


def picker(start):
    """The command that opens this system's own folder dialog, or None when it has none."""
    if sys.platform == "darwin":
        # The start folder travels as an argument, never inside the script text.
        return ["osascript", "-e", "on run argv", "-e", "tell me to activate", "-e",
                f'POSIX path of (choose folder with prompt "{PROMPT}" '
                "default location POSIX file (item 1 of argv))", "-e", "end run", "--", start]
    if sys.platform == "win32":
        return ["powershell", "-NoProfile", "-Command",
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
                "if ($d.ShowDialog() -eq 'OK') { $d.SelectedPath }"]
    if shutil.which("zenity"):
        return ["zenity", "--file-selection", "--directory", f"--title={PROMPT}",
                f"--filename={start}/"]
    if shutil.which("kdialog"):
        return ["kdialog", "--getexistingdirectory", start, "--title", PROMPT]
    if importlib.util.find_spec("tkinter"):
        return [sys.executable, "-c",
                "import sys, tkinter, tkinter.filedialog as f; r = tkinter.Tk(); r.withdraw(); "
                "r.attributes('-topmost', True); print(f.askdirectory(initialdir=sys.argv[1]))", start]
    return None


def pick(start):
    """A folder chosen in the system's dialog on this machine. No path when it was cancelled."""
    begin = Path(start or Path.home()).expanduser()
    command = picker(str(begin if begin.is_dir() else Path.home()))
    if command is None:
        return {"available": False, "path": None}
    try:
        chosen = subprocess.run(command, capture_output=True, text=True, timeout=300).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return {"available": True, "path": None}
    return {"available": True, "path": chosen if chosen and Path(chosen).is_dir() else None}


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
    def __init__(self, name, folder):
        self.name, self.folder = name, folder
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
    out = job.folder / "out" / NOTICE
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
        "a person dismissed the notice" if decision else \
        "after your decision" if outcome == "held" else f"the notice was {outcome}"
    steps += [step("impact", "Impact", plan is not None, skipped=None if plan else why,
                   detail=(f"{plan['tasks_affected']} of {plan['tasks_total']} tasks affected"
                           if plan else "")),
              step("evidence", "Evidence", sealed, skipped=None if sealed else why,
                   detail="sealed" + ("" if job.verified is None else
                                      ", verified" if job.verified else ", NOT verified")
                   if sealed else "")]
    steps.append(step("act", "Act", False, skipped="acting on a plan is not available yet"))

    # A held notice with no decision is not a step that failed or was skipped:
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


class App:
    """The state behind the page: where jobs live, the one job that may run, the token."""

    def __init__(self, home, token=None, launch=None):
        self.home = Path(home).expanduser().resolve()
        self.token = token or secrets.token_urlsafe(24)
        self.launch = launch or run_agent
        self.jobs = {}
        self.lock = threading.Lock()

    def state(self):
        return {"mainsheet": importlib.util.find_spec("mainsheet") is not None,
                "classifier": "jev" if os.environ.get("TYPESAFE_API_KEY") else "name",
                "api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
                # An engine can be picked by folder only when its extractor lists runs.
                "engines": [{"name": name, "folder": type(e).records is not Extractor.records}
                            for name, e in sorted(discover(Extractor).items())],
                "adapters": [{"name": name, "kinds": sorted(a.triggers), "flags": adapter_flags(a)}
                             for name, a in sorted(adapters().items())],
                "home": str(self.home), "start": str(Path.home())}

    def start(self, body):
        notice = (body.get("notice") or "").strip()
        if not notice:
            raise Refused("write the notice first")
        if len(notice) > MOST_NOTICE:
            raise Refused(f"the notice is longer than {MOST_NOTICE} characters")
        found = recognise(body.get("path") or "")
        if not found:
            raise Refused("that folder holds no run record Clew can read")
        run = body.get("run") or ""
        if run not in {r["name"] for r in found.records()}:
            raise Refused(f"no run named {run!r} in that folder")
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
        with self.lock:
            if any(job.state == "running" for job in self.jobs.values()):
                raise Refused("a run is still going; wait for it to finish", 409)
            name = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
            folder = self.home / "jobs" / name
            (folder / "inbox").mkdir(parents=True)
            job = self.jobs[name] = Job(name, folder)
            job.roots = roots
        threading.Thread(target=self.work, args=(job, found, run, notice, roots),
                         daemon=True).start()
        return {"job": name}

    def work(self, job, found, run, notice, roots):
        try:
            graph = found.load(run)
            (job.folder / "graph.json").write_text(json.dumps(graph))
            (job.folder / "inbox" / f"{NOTICE}.txt").write_text(notice + "\n")
            job.extract = {"engine": found.kind, "run": run, "tasks": len(graph["tasks"]),
                           "edges": len(graph["edges"]), "coverage": graph.get("coverage") or []}
            self.launch(job, self.home, roots)
            bundle = job.folder / "out" / NOTICE / "bundle"
            if (bundle / "manifest.json").is_file():
                job.verified = tools.run_clew("evidence", "verify", bundle)[0] == 0
            if job.state == "running":
                job.state = "finished"
        except (SystemExit, Exception) as bad:      # a job must end in a state the page can show
            job.state, job.error = "failed", str(bad) or repr(bad)

    def decide(self, body):
        """A person's decision on a held notice. Asking a trigger carries on to a sealed plan."""
        job = self.jobs.get(body.get("job") or "")
        if not job:
            raise Refused("no such run", 404)
        if job.state == "running":
            raise Refused("the run is still going; decide when it has finished", 409)
        action = body.get("action")
        if action not in ("ask", "dismiss"):
            raise Refused("the decision is ask or dismiss")
        try:
            tools.decide(job.folder, NOTICE, (body.get("actor") or "").strip(),
                         ask=(body.get("trigger") or "") if action == "ask" else None,
                         reason=body.get("reason") or "")
        except tools.ToolError as bad:
            raise Refused(str(bad))
        if action == "ask":
            job.state, job.error = "running", None
            threading.Thread(target=self.carry_on, args=(job,), daemon=True).start()
        return status(job)

    def carry_on(self, job):
        """After a person asks a trigger: the plan and its seal. Two commands, no model."""
        try:
            env = dict(os.environ, **job.roots)
            tools.impact(job.folder, NOTICE, env=env)
            job.verified = tools.seal(job.folder, NOTICE)["verified"]
            job.state = "finished"
        except (tools.ToolError, SystemExit, Exception) as bad:
            job.state, job.error = "failed", str(bad) or repr(bad)

    def job(self, body):
        job = self.jobs.get(body.get("job") or "")
        if not job:
            raise Refused("no such run", 404)
        return status(job)


def run_agent(job, home, roots):
    """One agent run over the job folder, its output kept line by line."""
    if importlib.util.find_spec("mainsheet") is None:
        raise Refused("Mainsheet is not installed in this environment, so the agent cannot run")
    agent_home = home / "mainsheet"
    agent_home.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, CLEW_AGENT_DIR=str(job.folder), MAINSHEET_HOME=str(agent_home), **roots)
    ran = subprocess.Popen([sys.executable, "-m", "mainsheet.agent.main", str(DEFINITION)],
                           cwd=agent_home, env=env, text=True, bufsize=1,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for line in ran.stdout:
        job.say(line)
        spent = SPENT.search(line)
        if spent:
            job.turns = int(spent.group(1))
            job.cost = None if spent.group(2) == "None" else float(spent.group(2))
    if ran.wait() != 0:
        job.state = "failed"
        job.error = next((line for line in reversed(job.log) if "failed" in line or "rror" in line),
                         f"the agent exited with status {ran.returncode}")
        if "authenticate" in job.error and not os.environ.get("ANTHROPIC_API_KEY"):
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
        """Only a request addressed to this machine by name: a rebound hostname is refused."""
        port = self.server.server_address[1]
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

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
        routes = {"/api/state": lambda body: app.state(),
                  "/api/browse": lambda body: browse(body.get("path")),
                  "/api/pick": lambda body: pick(body.get("path")),
                  "/api/run": app.start, "/api/job": app.job, "/api/decide": app.decide}
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


def serve(home, port=0, token=None):
    """A server bound to this machine. Returns it unstarted, with its link."""
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.app = App(home, token)
    return server, f"http://127.0.0.1:{server.server_address[1]}/?t={server.app.token}"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="clew ui",
                                     description="Clew in a browser tab, on this machine only.")
    parser.add_argument("--port", type=int, default=8770, help="default 8770; 0 picks a free one")
    parser.add_argument("--home", default="~/.clew/ui",
                        help="where runs and the agent's records are kept (default ~/.clew/ui)")
    parser.add_argument("--no-browser", action="store_true", help="print the link and do not open it")
    args = parser.parse_args(argv)

    try:
        server, link = serve(args.home, args.port)
    except OSError as bad:
        raise SystemExit(f"cannot listen on port {args.port}: {bad.strerror or bad}")
    print(f"Clew UI: {link}\nruns are kept in {server.app.home}\nCtrl-C stops it", flush=True)
    if not args.no_browser:
        webbrowser.open(link)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
