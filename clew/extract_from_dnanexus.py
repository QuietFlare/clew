"""
Clew: lineage adapter for DNAnexus analyses.

DNAnexus records what Clew needs on the platform itself. A job describe
lists its inputs and outputs as file IDs, a file describe names the job
that created it, and file IDs are immutable and survive cloning between
projects. So edges join on file ID, and a run stitches to another run
with no path matching at all.

Two ways in. `--records DIR` reads describe output saved as JSON
(jobs.json and files.json), which is how the fixtures and tests work.
`--analysis ID` fetches the same records over the API with a token, using
nothing beyond the standard library. The API path has not yet been run
against a live analysis; the record shapes come from the DNAnexus API
documentation.

The task hash is the job ID. Status is the job state, upper-cased.
The container is "<executable name>@<applet or app ID>", so a trigger
like --container gatk matches by name. Two optional keys carry what
DNAnexus knows and Nextflow does not: `price`, the job's total price
when the caller has billing access, and `duration_s`, wall time between
startedRunning and stoppedRunning.

Nextflow pipelines on DNAnexus run as a head job plus one subjob per
process. Whether those subjobs expose per-process files as platform
file IDs is unverified; if they do not, use the Nextflow lineage store
written to the project instead.
"""

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

from clew.core.graph import EXTERNAL

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

    tasks, edges, outputs = {}, [], {}
    for job_id, job in jobs.items():
        task = {
            "hash": job_id,
            "task_id": job_id,
            "name": job.get("name") or job.get("executableName") or job_id,
            "process": job.get("executableName") or job.get("name") or job_id,
            "container": executable_of(job),
            "status": (job.get("state") or "").upper(),
            "script": "",
            "workdir": "",
        }
        if isinstance(job.get("totalPrice"), (int, float)):
            task["price"] = job["totalPrice"]
        duration = duration_of(job)
        if duration is not None:
            task["duration_s"] = duration
        tasks[job_id] = task

        seen = set()
        for fid in link_ids(job.get("input")):
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

        outputs[job_id] = sorted(
            files.get(fid, {}).get("name") or fid
            for fid in dict.fromkeys(link_ids(job.get("output"))))

    return {"tasks": tasks, "edges": edges, "outputs": outputs}


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


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build a Clew graph from a DNAnexus analysis.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--records", help="directory with jobs.json and files.json")
    source.add_argument("--analysis", help="analysis-xxxx to fetch over the API")
    parser.add_argument("--token", help="API token; default DX_SECURITY_CONTEXT")
    parser.add_argument("--json-out", help="path to write the graph as JSON")
    args = parser.parse_args(argv)

    if args.records:
        records = load_records(args.records)
    else:
        token = args.token or token_from_env()
        if not token:
            print("clew: no API token; pass --token or log in with dx", file=sys.stderr)
            return 2
        records = fetch(args.analysis, token)

    graph = extract(records)
    external = [e for e in graph["edges"] if e["producer"] == EXTERNAL]
    priced = [t for t in graph["tasks"].values() if "price" in t]

    print(f"jobs               : {len(graph['tasks'])}")
    print(f"input files (edges): {len(graph['edges'])}")
    print(f"  external inputs  : {len(external)}")
    if priced:
        print(f"total price        : {sum(t['price'] for t in priced):.2f}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(graph, indent=2))
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
