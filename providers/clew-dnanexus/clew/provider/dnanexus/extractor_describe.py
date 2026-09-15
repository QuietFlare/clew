"""
DNAnexus analyses, read into the graph.

A job describe lists inputs and outputs as file IDs, a file describe names
the job that created it, and file IDs survive cloning between projects, so
edges join on file ID. --records DIR reads saved describe output (jobs.json,
files.json). --analysis ID fetches the same over the API with a token,
standard library only, and has not yet met a live analysis.

The task hash is the job ID, status the job state, container
"<executable>@<applet or app ID>", with optional price and duration_s. An
input naming a job outside the analysis counts in coverage rather than being
dropped. Whether Nextflow subjobs on DNAnexus expose per-process files as
platform IDs is unverified; if not, use the lineage store written to the
project.
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

from clew.graph.graph import EXTERNAL, task_status
from clew.contracts import Extractor

API = "https://api.dnanexus.com"


def load_records(records_dir):
    """Saved describe output: jobs.json and files.json, each a list."""
    records_dir = Path(records_dir)
    return {
        "jobs": json.loads((records_dir / "jobs.json").read_text()),
        "files": json.loads((records_dir / "files.json").read_text()),
    }


def link_ids(value):
    """Every file ID referenced by a job input or output, in order."""
    if isinstance(value, dict):
        link = value.get("$dnanexus_link")
        if isinstance(link, str):
            return [link] if link.startswith("file-") else []
        if isinstance(link, dict):
            target = link.get("id", "")
            return [target] if target.startswith("file-") else []
        return [fid for v in value.values() for fid in link_ids(v)]
    if isinstance(value, list):
        return [fid for v in value for fid in link_ids(v)]
    return []


def job_refs(value):
    """
    Every job or stage reference in a job input, as (kind, id, field). An
    input given as another job's output field is an edge to that job, not to
    a file.
    """
    if isinstance(value, dict):
        link = value.get("$dnanexus_link")
        if isinstance(link, dict):
            if "job" in link:
                return [("job", link["job"], link.get("field") or "")]
            if "stage" in link:
                field = link.get("outputField") or link.get("inputField") or ""
                return [("stage", link["stage"], field)]
        if link is not None:
            return []
        return [r for v in value.values() for r in job_refs(v)]
    if isinstance(value, list):
        return [r for v in value for r in job_refs(v)]
    return []


def input_views(job):
    """
    The job's input as recorded, best resolved view first. `input` is what
    the job ran with; `runInput` and `originalInput` are read as fallbacks
    when they resolve more references to files.
    """
    views = [job.get(key) for key in ("input", "runInput", "originalInput")]
    return [v for v in views if v]


def executable_of(job):
    name = job.get("executableName") or job.get("name") or ""
    ident = job.get("applet") or job.get("app") or job.get("executable") or ""
    return f"{name}@{ident}" if ident else name


def duration_of(job):
    started, stopped = job.get("startedRunning"), job.get("stoppedRunning")
    if isinstance(started, (int, float)) and isinstance(stopped, (int, float)):
        return (stopped - started) / 1000
    return None


def extract(records):
    """Build the common graph schema from job and file describes."""
    jobs = {j["id"]: j for j in records["jobs"]}
    files = {f["id"]: f for f in records["files"]}
    by_stage = {j["stage"]: jid for jid, j in jobs.items() if j.get("stage")}

    tasks, edges, outputs = {}, [], {}
    unresolved = {}
    for job_id, job in jobs.items():
        task = {
            "hash": job_id,
            "task_id": job_id,
            "name": job.get("name") or job.get("executableName") or job_id,
            "process": job.get("executableName") or job.get("name") or job_id,
            "container": executable_of(job),
            "status": task_status(job.get("state")),
            "engine_status": job.get("state") or "",
            "script": "",
            "workdir": "",
        }
        if isinstance(job.get("totalPrice"), (int, float)):
            task.setdefault("metrics", {})["price"] = job["totalPrice"]
        duration = duration_of(job)
        if duration is not None:
            task.setdefault("metrics", {})["duration_s"] = duration
        tasks[job_id] = task

        views = input_views(job)
        seen = set()
        for fid in [fid for view in views for fid in link_ids(view)]:
            if fid in seen:
                continue
            seen.add(fid)
            record = files.get(fid, {})
            producer = (record.get("createdBy") or {}).get("job")
            if producer not in jobs or producer == job_id:
                producer = EXTERNAL
            edges.append({
                "consumer": job_id,
                "producer": producer,
                "filename": record.get("name") or fid,
                "target": fid,
            })

        # References to a job or stage rather than a file. The view that
        # resolved the most of them to files is the one whose leftovers
        # count; a reference that names a job in this analysis is an edge
        # to it, and one that names nothing here is reported, not dropped.
        refs = min((job_refs(view) for view in views), key=len, default=[])
        for kind, ref_id, field in dict.fromkeys(refs):
            producer = ref_id if kind == "job" else by_stage.get(ref_id)
            if producer in jobs and producer != job_id:
                edges.append({
                    "consumer": job_id,
                    "producer": producer,
                    "filename": field or ref_id,
                    "target": f"{ref_id}:{field}" if field else ref_id,
                })
            else:
                unresolved[job_id] = unresolved.get(job_id, 0) + 1

        outputs[job_id] = sorted(
            files.get(fid, {}).get("name") or fid
            for fid in dict.fromkeys(link_ids(job.get("output"))))

    graph = {"tasks": tasks, "edges": edges, "outputs": outputs}
    if unresolved:
        listing = ", ".join(f"{jid} ({n})" for jid, n in sorted(unresolved.items()))
        graph["coverage"] = [
            f"{sum(unresolved.values())} job or stage references in the "
            f"inputs of {len(unresolved)} jobs name no job in this analysis, "
            f"so those inputs carry no edge and what fed them is unknown: "
            f"{listing}."]
    return graph


def api_call(path, body, token, api=API):
    request = urllib.request.Request(
        f"{api}{path}", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())


def fetch(analysis_id, token, api=API):
    """Every job under one analysis, plus every file they touch."""
    jobs, starting = [], None
    while True:
        body = {"rootExecution": analysis_id, "describe": True,
                "classname": "job"}
        if starting:
            body["starting"] = starting
        page = api_call("/system/findExecutions", body, token, api)
        jobs += [r["describe"] for r in page.get("results", [])]
        starting = page.get("next")
        if not starting:
            break

    file_ids = sorted({fid for job in jobs
                       for fid in link_ids(job.get("input"))
                       + link_ids(job.get("output"))})
    files = []
    for start in range(0, len(file_ids), 1000):
        page = api_call("/system/describeDataObjects",
                        {"objects": file_ids[start:start + 1000]}, token, api)
        files += [r["describe"] for r in page.get("results", [])
                  if "describe" in r]
    return {"jobs": jobs, "files": files}


def token_from_env():
    """dx-toolkit keeps the token in DX_SECURITY_CONTEXT as JSON."""
    raw = os.environ.get("DX_SECURITY_CONTEXT")
    if raw:
        try:
            return json.loads(raw).get("auth_token")
        except ValueError:
            return None
    return os.environ.get("DX_API_TOKEN")


class DNAnexus(Extractor):
    name = "dnanexus"
    description = "a DNAnexus analysis, from saved records or the API"

    def add_arguments(self, parser):
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument("--records", help="directory with jobs.json and files.json")
        source.add_argument("--analysis", help="analysis-xxxx to fetch over the API")
        parser.add_argument("--token", help="API token; default DX_SECURITY_CONTEXT")

    def extract(self, args):
        if args.records:
            return extract(load_records(args.records))
        token = args.token or token_from_env()
        if not token:
            print("clew: no API token; pass --token or log in with dx", file=sys.stderr)
            raise SystemExit(2)
        return extract(fetch(args.analysis, token))

    def summarize(self, graph, args):
        external = [e for e in graph["edges"] if e["producer"] == EXTERNAL]
        priced = [t for t in graph["tasks"].values() if "price" in t.get("metrics", {})]
        print(f"jobs               : {len(graph['tasks'])}")
        print(f"input files (edges): {len(graph['edges'])}")
        print(f"  external inputs  : {len(external)}")
        if priced:
            print(f"total price        : {sum(t['metrics']['price'] for t in priced):.2f}")
        self.coverage(graph)


main = DNAnexus.main


if __name__ == "__main__":
    sys.exit(main())
