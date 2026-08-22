"""
Clew — lineage adapter for Workflow Run RO-Crate (nf-prov output).

WHY THIS EXISTS
---------------
nf-prov publishes a run's provenance as a Workflow Run RO-Crate: a
standards-shaped JSON-LD file many archives, journals and registries
already ask for. Labs that produce crates for compliance reasons have
lineage on disk without knowing it — this adapter turns a crate into the
same graph JSON the other two extractors emit, so everything downstream
works unchanged.

THE CRATE, AS WRITTEN BY nf-prov (wrroc format)
-----------------------------------------------
    @graph contains, among much else:
      CreateAction  @id "#task/<32-hex>"      one per task
                    name "ALIGN (sample_beta)"  the nf-core tag convention
                    object[]  inputs:  "#task/<producer-hash>/<file>"
                                       or "file:///..." (external)
                    result[]  outputs: "#task/<own-hash>/<file>"
                    instrument -> SoftwareApplication (container), when run
                    with one
      CreateAction  "Nextflow workflow run <uuid>"   run-level; skipped

Task hashes are the same 32-hex values the lineage store uses, abbreviated
identically, so graphs from either source are comparable node for node.

HONEST LIMITS
-------------
A crate records what ran, not how to run it again: no script, and no
workdir. Tasks whose container is not in the crate therefore classify as
IRREDUCIBLE (fail closed — the crate carries no re-execution evidence),
and storage checks report DESTROYED unless published copies are mapped.
Prefer the lineage store when both exist; read the crate when it is what
a lab already has.
"""

import argparse
import json
from pathlib import Path

TASK_PREFIX = "#task/"
FILE_SCHEME = "file://"


def abbreviate(full_hash):
    return f"{full_hash[:2]}/{full_hash[2:8]}"


def load_crate(path):
    return {e["@id"]: e for e in json.loads(Path(path).read_text()).get("@graph", [])
            if isinstance(e, dict) and "@id" in e}


def is_task(entity):
    types = entity.get("@type")
    types = types if isinstance(types, list) else [types]
    return ("CreateAction" in types
            and entity.get("@id", "").startswith(TASK_PREFIX))


def task_hash_of(entity_id):
    """'#task/<hash>' or '#task/<hash>/<file>' -> the 32-hex hash."""
    rest = entity_id[len(TASK_PREFIX):]
    return rest.split("/", 1)[0]


def container_of(entity, by_id):
    """Resolve the action's instrument to a container name, if recorded."""
    instrument = entity.get("instrument")
    if not isinstance(instrument, dict):
        return ""
    app = by_id.get(instrument.get("@id"), {})
    return app.get("containerImage") or app.get("name") or ""


def extract(crate_path):
    """Build the common graph schema from one run's crate."""
    by_id = load_crate(crate_path)
    tasks, edges, outputs = {}, [], {}

    for entity in by_id.values():
        if not is_task(entity):
            continue
        full_hash = task_hash_of(entity["@id"])
        abbrev = abbreviate(full_hash)

        name = entity.get("name", "")
        process = name.rsplit(" (", 1)[0] if " (" in name else name

        tasks[abbrev] = {
            "hash": abbrev,
            "task_id": None,
            "name": name,
            "process": process,
            "container": container_of(entity, by_id),
            "status": "",
            "workdir": "",   # not recorded in a crate; storage fails closed
            "script": "",    # likewise: no re-execution evidence
        }

        for obj in entity.get("object", []):
            oid = obj.get("@id", "") if isinstance(obj, dict) else str(obj)
            if oid.startswith(TASK_PREFIX):
                producer_hash, _, filename = oid[len(TASK_PREFIX):].partition("/")
                edges.append({
                    "consumer": abbrev,
                    "producer": abbreviate(producer_hash),
                    "filename": filename,
                    "target": oid,
                })
            elif oid.startswith(FILE_SCHEME):
                path = oid[len(FILE_SCHEME):]
                edges.append({
                    "consumer": abbrev,
                    "producer": "EXTERNAL",
                    "filename": Path(path).name,
                    "target": path,
                })
            # anything else (parameters, PropertyValues) is not an artifact

        produced = []
        for res in entity.get("result", []):
            rid = res.get("@id", "") if isinstance(res, dict) else str(res)
            if rid.startswith(TASK_PREFIX):
                produced.append(rid[len(TASK_PREFIX):].partition("/")[2])
        outputs[abbrev] = sorted(p for p in produced if p)

    return {"tasks": tasks, "edges": edges, "outputs": outputs}


def main():
    parser = argparse.ArgumentParser(
        description="Build a Clew graph from a Workflow Run RO-Crate (nf-prov).")
    parser.add_argument("--crate", required=True, help="ro-crate-metadata.json path")
    parser.add_argument("--json-out", help="path to write the graph as JSON")
    args = parser.parse_args()

    graph = extract(args.crate)
    known = set(graph["tasks"])
    external = [e for e in graph["edges"] if e["producer"] == "EXTERNAL"]
    dangling = [e for e in graph["edges"]
                if e["producer"] not in known and e["producer"] != "EXTERNAL"]

    print(f"tasks in crate     : {len(graph['tasks'])}")
    print(f"input files (edges): {len(graph['edges'])}")
    print(f"  external inputs  : {len(external)}")
    print(f"  DANGLING         : {len(dangling)}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(graph, indent=2))
        print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()
