"""
Clew: lineage adapter for Snakemake's metadata store.

One record per output file, in .snakemake/metadata/ or metadata.db.
Details and limits: docs/sources.md, "Snakemake".
"""

import argparse
import base64
import json
import sqlite3
import sys
from pathlib import Path

EXTERNAL = "EXTERNAL"
METADATA_DIR = "metadata"
METADATA_DB = "metadata.db"
KNOWN_FORMAT = 6
JSON_COLUMNS = ("input", "log", "params", "input_checksums")


def store_dir(path):
    """The .snakemake directory, given it or the working directory."""
    path = Path(path)
    if (path / METADATA_DB).exists() or (path / METADATA_DIR).is_dir():
        return path
    if (path / ".snakemake").is_dir():
        return path / ".snakemake"
    raise SystemExit(f"clew: no .snakemake directory at {path}")


def decode_key(relative_parts):
    """The output path from a record's file name, long names rejoined."""
    joined = "".join(p[1:] if p.startswith("@") else p for p in relative_parts)
    return base64.urlsafe_b64decode(joined.encode()).decode()


def read_files(metadata_dir):
    """{output path: record} from the file backend."""
    records = {}
    for path in sorted(metadata_dir.rglob("*")):
        if not path.is_file():
            continue
        try:
            record = json.loads(path.read_text())
        except ValueError:
            continue          # mid-write, as Snakemake itself skips it
        key = decode_key(path.relative_to(metadata_dir).parts)
        records[key] = record
    return records


def read_db(db_path, namespace=None):
    """{output path: record} from the SQLite backend, one namespace."""
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows = connection.execute("select * from snakemake_metadata").fetchall()
    connection.close()

    namespaces = sorted({row["namespace"] for row in rows})
    if namespace is None and len(namespaces) > 1:
        raise SystemExit(
            "clew: the store holds several workflows, pass --namespace:\n  "
            + "\n  ".join(namespaces))
    records = {}
    for row in rows:
        if namespace is not None and row["namespace"] != namespace:
            continue
        record = dict(row)
        for column in JSON_COLUMNS:
            if isinstance(record.get(column), str):
                record[column] = json.loads(record[column])
        records[record.pop("target")] = record
    return records


def load_records(store, namespace=None):
    store = store_dir(store)
    db = store / METADATA_DB
    files = store / METADATA_DIR
    if db.exists():
        return read_db(db, namespace)
    if files.is_dir():
        return read_files(files)
    raise SystemExit(f"clew: no metadata in {store}")


def check_format(records):
    """Refuse a newer record format rather than guess at its fields."""
    versions = {r.get("record_format_version") or 0 for r in records.values()}
    newer = {v for v in versions if v > KNOWN_FORMAT}
    if newer:
        raise SystemExit(
            f"clew: record_format_version {sorted(newer)} is newer than "
            f"{KNOWN_FORMAT}, which is the latest this adapter reads")


def group_jobs(records):
    """[(node id, rule, [output paths], record)], one entry per job."""
    jobs = {}
    for output, record in sorted(records.items()):
        if record.get("job_hash") is not None:
            key = (record.get("rule"), record["job_hash"])
        else:
            key = (record.get("rule"), tuple(record.get("input") or []),
                   record.get("shellcmd"))
        jobs.setdefault(key, []).append(output)
    grouped = []
    for key, paths in jobs.items():
        node = f"{key[0]}/{paths[0]}"
        grouped.append((node, key[0], paths, records[paths[0]]))
    return grouped


def environment_of(record):
    """The image the job ran in, else the conda environment by hash."""
    image = record.get("container_img_url")
    if image:
        return image
    if record.get("conda_env"):
        return f"conda@{(record.get('software_stack_hash') or '')[:12]}"
    return ""


def checksum_of(record, path):
    value = (record.get("input_checksums") or {}).get(path)
    if isinstance(value, str) and value.startswith("sha256:"):
        return value[len("sha256:"):]
    return None


def extract(records, workdir=""):
    """Build the common graph schema from the store's records."""
    check_format(records)
    jobs = group_jobs(records)
    producers = {path: node for node, _, paths, _ in jobs for path in paths}

    tasks, edges, outputs = {}, [], {}
    for node, rule, paths, record in jobs:
        tasks[node] = {
            "hash": node,
            "task_id": node,
            "name": f"{rule} ({paths[0]})",
            "process": rule or "",
            "container": environment_of(record),
            "status": "INCOMPLETE" if record.get("incomplete") else "COMPLETED",
            "script": record.get("shellcmd") or record.get("code") or "",
            "workdir": workdir,
        }
        start, end = record.get("starttime"), record.get("endtime")
        if isinstance(start, (int, float)) and isinstance(end, (int, float)):
            tasks[node]["duration_s"] = end - start

        for path in dict.fromkeys(record.get("input") or []):
            producer = producers.get(path)
            if producer == node:
                producer = None
            edge = {
                "consumer": node,
                "producer": producer or EXTERNAL,
                "filename": Path(path).name,
                "target": path,
            }
            digest = checksum_of(record, path)
            if digest:
                edge["sha256"] = digest
            edges.append(edge)

        outputs[node] = sorted(Path(p).name for p in paths)

    return {"tasks": tasks, "edges": edges, "outputs": outputs}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build a Clew graph from Snakemake's metadata store.")
    parser.add_argument("--workdir", required=True,
                        help="the workflow's working directory, or its .snakemake")
    parser.add_argument("--namespace",
                        help="which workflow to read from a shared metadata.db")
    parser.add_argument("--json-out", help="path to write the graph as JSON")
    args = parser.parse_args(argv)

    records = load_records(args.workdir, args.namespace)
    workdir = str(store_dir(args.workdir).parent.resolve())
    graph = extract(records, workdir)
    external = [e for e in graph["edges"] if e["producer"] == EXTERNAL]
    hashed = [e for e in graph["edges"] if e.get("sha256")]
    incomplete = [t for t in graph["tasks"].values() if t["status"] == "INCOMPLETE"]

    print(f"jobs               : {len(graph['tasks'])}")
    print(f"  incomplete       : {len(incomplete)}")
    print(f"input files (edges): {len(graph['edges'])}")
    print(f"  external inputs  : {len(external)}")
    print(f"  with sha256      : {len(hashed)}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(graph, indent=2))
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
