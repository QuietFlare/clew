"""
Workflow Run RO-Crates, as nf-prov writes them, read into the graph.

In @graph each task is a CreateAction with @id "#task/<32-hex>", a name in
the "PROCESS (tag)" convention, object[] inputs as "#task/<producer>/<file>"
or an external id (file://, https://, results/ paths, #tmp), result[]
outputs, and an instrument naming the module, not a container image.
Version-pinned container triggers therefore cannot match; module names can.
Hashes abbreviate like the lineage store.

A crate records what ran, not how to run it again: no script, no workdir.
Tasks without a container classify IRREDUCIBLE, and storage reads DESTROYED
unless published copies are mapped. Prefer the store when both exist.
"""

import json
import sys
from pathlib import Path
from clew.contracts import Extractor

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
            "target": "",   # a crate records no execution host
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
            elif oid and not oid.startswith("#param"):
                # Anything that is not another task's output came from outside
                # the run. Real nf-prov crates reference such inputs by
                # whatever id the file arrived under: file:// paths, https://
                # URLs for remote test data or references, relative results/
                # paths, or #tmp entries. Dropping the unrecognised ones is
                # how the first real crate lost all 117 external inputs, a
                # reference-update trigger then finds nothing and reads as
                # clean, which is the false negative this project exists to
                # avoid. Unknown ids fail open to EXTERNAL instead.
                path = oid[len(FILE_SCHEME):] if oid.startswith(FILE_SCHEME) else oid
                edges.append({
                    "consumer": abbrev,
                    "producer": "EXTERNAL",
                    "filename": Path(path.split("?", 1)[0]).name,
                    "target": path,
                })

        produced = []
        for res in entity.get("result", []):
            rid = res.get("@id", "") if isinstance(res, dict) else str(res)
            if rid.startswith(TASK_PREFIX):
                produced.append(rid[len(TASK_PREFIX):].partition("/")[2])
        outputs[abbrev] = sorted(p for p in produced if p)

    return {"tasks": tasks, "edges": edges, "outputs": outputs}


class RoCrate(Extractor):
    name = "ro-crate"
    description = "a Workflow Run RO-Crate, as nf-prov writes"

    def add_arguments(self, parser):
        parser.add_argument("--crate", required=True, help="ro-crate-metadata.json path")

    def extract(self, args):
        return extract(args.crate)


main = RoCrate.main


if __name__ == "__main__":
    sys.exit(main())
