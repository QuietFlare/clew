"""
Reading a directory of sealed bundles back into answerable facts.

Two surfaces read evidence: the offline dashboard and the MCP server. If
each loaded bundles its own way they would eventually disagree, and on the
day they did nobody could say which was wrong. So loading, bundle lookup
and the integrity check live here, once, and both surfaces import them.

Read-only on purpose. Nothing in this module writes anything, and nothing
in it opens a database connection.
"""

import json
from pathlib import Path

from core import evidence
from core import eventlog
from core import query


def load_store(bundle_root):
    """
    Every readable bundle under a directory, plus the union of their events.

    A bundle is a directory containing a manifest. Anything else is skipped
    rather than treated as an error: an auditor's folder holds whatever they
    were sent, and one unreadable item should not deny them the rest.
    """
    root = Path(bundle_root)
    bundles, entries_by_hash = [], {}

    candidates = [root] if (root / evidence.MANIFEST).is_file() else sorted(
        p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []

    for path in candidates:
        manifest_path = path / evidence.MANIFEST
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
            documents = {}
            for name in manifest.get("files", {}):
                if name.endswith(".json"):
                    documents[name] = json.loads((path / name).read_text())
        except (OSError, json.JSONDecodeError):
            continue

        bundles.append({
            "path": str(path),
            "name": path.name,
            "hash": evidence.bundle_hash(manifest),
            "manifest": manifest,
            "documents": documents,
        })
        # Entries are content-addressed, so the same entry appearing in
        # several bundles collapses to one rather than being counted twice.
        for entry in documents.get("events.json", []):
            entries_by_hash[entry["hash"]] = dict(entry, _bundle=path.name)

    # A LOG HAS NO IDENTITY, which is a real gap and is handled here rather
    # than hidden. Two bundles sealed from two different logs both number
    # their entries from 1, and merging them would silently interleave two
    # unrelated histories into one plausible-looking timeline. There is no
    # field to compare, so compare the thing that must agree if the logs are
    # the same: an entry at a given seq has one hash.
    by_seq = {}
    conflicts = []
    for entry in entries_by_hash.values():
        clash = by_seq.get(entry["seq"])
        if clash is not None and clash["hash"] != entry["hash"]:
            conflicts.append({
                "seq": entry["seq"],
                "bundles": sorted({clash["_bundle"], entry["_bundle"]}),
                "hashes": sorted({clash["hash"], entry["hash"]}),
            })
        else:
            by_seq[entry["seq"]] = entry

    entries = sorted(by_seq.values(), key=lambda e: e["seq"])
    return bundles, entries, conflicts


def conflict_coverage(conflicts):
    """Said on every answer drawn from the merged entries, when it applies."""
    if not conflicts:
        return []
    seqs = ", ".join(str(c["seq"]) for c in conflicts[:5])
    return [
        f"THESE BUNDLES DISAGREE: {len(conflicts)} sequence numbers "
        f"(including {seqs}) carry different entries in different bundles, "
        "which means they were sealed from DIFFERENT LOGS. Answers drawn "
        "from the combined history are not trustworthy. Query one bundle's "
        "log at a time until this is resolved.",
    ]


def with_conflicts(result, conflicts):
    """Attach the cross-log warning to an answer drawn from merged entries."""
    result["coverage"] = list(result.get("coverage", [])) + conflict_coverage(
        conflicts)
    return result


def find_bundle(bundles, name):
    for bundle in bundles:
        if name in (bundle["name"], bundle["hash"], bundle["path"]):
            return bundle
    return None


def plan_of(bundle):
    return bundle["documents"].get("plan.json")


def policy_of(bundle):
    return bundle["documents"].get("policy.json")


def check_integrity(bundle):
    """Run the deterministic verifier and report exactly what it said."""
    manifest = bundle["manifest"]
    path = Path(bundle["path"])
    checks = [evidence.verify_files(path, manifest)]
    events = bundle["documents"].get("events.json", [])
    if "events.json" in manifest["files"]:
        checks.append(evidence.verify_log(events, manifest, eventlog))
    if plan_of(bundle):
        checks.append(evidence.verify_policy(plan_of(bundle), policy_of(bundle)))
        checks.append(evidence.verify_replay(plan_of(bundle), policy_of(bundle)))
    elif "gate.json" in manifest["files"]:
        checks.append(evidence.verify_gate(
            bundle["documents"]["gate.json"],
            bundle["documents"]["gate-policy.json"], events))

    return query.answer(
        f"is the evidence in {bundle['name']!r} intact?",
        {"bundle": bundle["name"], "bundle_hash": bundle["hash"],
         "checks": checks,
         "all_passed": all(c["ok"] for c in checks if c["ok"] is not None)},
        [{"kind": "bundle", "name": bundle["name"],
          "bundle_hash": bundle["hash"]}],
        coverage=[
            "this checks that the sealed record is internally consistent and "
            "that every verdict re-derives. It says nothing about whether the "
            "facts sealed into it were true.",
            "a signature, if present, is not checked here: that needs an "
            "allowed_signers file the reader trusts. Use `evidence.py verify "
            "--allowed-signers`.",
        ])
