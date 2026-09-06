"""
Clew: hash a run's files once, for graphs recorded without content digests.

    clew digest --graph graph.json --work-root work/ --results results/ --out graph.json

The engine hashing at write time is the cheap moment. This is the fallback
for runs that missed it: every output under --work-root and every file under
--results is read once, and the SHA-256 goes into the graph, where reclaim
and stitch compare strings instead of reading files again.
"""

import argparse
import hashlib
import json
from pathlib import Path

from clew.graph.graph import local_workdir

ALGORITHM = "sha256"


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return f"{ALGORITHM}:{digest.hexdigest()}"


def digest_outputs(graph, work_root):
    """Fill `digest` on every output found under work_root. Returns counts."""
    counts = {"hashed": 0, "kept": 0, "missing": 0, "bytes": 0}
    details = graph.setdefault("output_details", {})
    for task_hash, task in graph["tasks"].items():
        local = local_workdir(task.get("workdir"), work_root)
        entries = details.setdefault(task_hash, [])
        known = {d["file"]: d for d in entries}
        for name in graph.get("outputs", {}).get(task_hash, []):
            if name not in known:
                known[name] = {"file": name}
                entries.append(known[name])
        for detail in entries:
            if (detail.get("digest") or "").startswith(f"{ALGORITHM}:"):
                counts["kept"] += 1
                continue
            path = local / detail["file"] if local else None
            if not path or path.is_symlink() or not path.is_file():
                counts["missing"] += 1
                continue
            detail["digest"] = sha256_file(path)
            detail["size"] = path.stat().st_size
            counts["hashed"] += 1
            counts["bytes"] += detail["size"]
    return counts


def digest_results(graph, results):
    """Record size and digest of every regular file under results. Returns counts."""
    counts = {"hashed": 0, "links": 0, "bytes": 0}
    root = Path(results)
    published = graph.setdefault("published", {})
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            counts["links"] += 1
            continue
        if not path.is_file():
            continue
        size = path.stat().st_size
        published[str(path.relative_to(root))] = {"size": size, "digest": sha256_file(path)}
        counts["hashed"] += 1
        counts["bytes"] += size
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Hash a run's files once and record the digests in its graph.")
    parser.add_argument("--graph", required=True, help="graph JSON from an extractor")
    parser.add_argument("--work-root", metavar="DIR", help="the run's work directory")
    parser.add_argument("--results", metavar="DIR", help="the published results tree")
    parser.add_argument("--out", metavar="PATH", help="where to write; default: --graph")
    args = parser.parse_args(argv)
    if not args.work_root and not args.results:
        raise SystemExit("give --work-root, --results, or both")

    graph = json.loads(Path(args.graph).read_text())
    if args.work_root:
        c = digest_outputs(graph, args.work_root)
        print(f"outputs: {c['hashed']} hashed ({c['bytes']:,} bytes), "
              f"{c['kept']} already sha256, {c['missing']} not on disk")
    if args.results:
        c = digest_results(graph, args.results)
        print(f"published: {c['hashed']} files hashed ({c['bytes']:,} bytes), "
              f"{c['links']} symlinks skipped")
    out = args.out or args.graph
    Path(out).write_text(json.dumps(graph, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
