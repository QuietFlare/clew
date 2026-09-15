"""
The evidence bundle: opaque documents, hashed, checkable offline.

Three checks, one per claim. The bundled log entries re-chain. The bundled
policy is the one the plan cites, by hash. Every verdict is recomputed from
the bundled facts, which makes the plan a conclusion rather than a folder
somebody assembled.

No clock inside, so the same inputs give the same bundle hash. Time lives in
the log the bundle anchors to, and sealing is itself a logged event. The
bundle records the log head it saw and leaves the building, so a truncated
log is caught by a witness its owner does not control.

This module seals with SHA-256 and the standard library. It does not sign.
Countersigning is delegated to ssh-keygen -Y, which readers already trust
and manage keys for.
"""

import hashlib
import json
import shutil
from pathlib import Path

from clew.ledger import gate as gate_module
from clew.ledger import policy as policy_module
from clew.ledger.eventlog import GENESIS

# Version 2 records where the bundled chain starts (anchors.since) and the
# head of the bundle it continues (anchors.previous_log_head). A version 1
# manifest has neither and is read as starting at genesis.
BUNDLE_VERSION = 2

MANIFEST = "manifest.json"
SIGNATURE = "manifest.json.sig"
CRATE = "ro-crate-metadata.json"

# Never listed in the manifest: the manifest cannot hash itself, and the
# signature is made over the manifest and therefore written after it.
UNLISTED = {MANIFEST, SIGNATURE}


def safe_name(name):
    """
    A manifest entry must name a file in the bundle directory and nowhere
    else. A separator or a parent reference would make the verifier hash a
    file outside the bundle and report on it as if it were sealed.
    """
    return (isinstance(name, str) and bool(name) and name not in (".", "..")
            and "/" not in name and "\\" not in name and ".." not in name)


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def bundle_hash(manifest):
    """The bundle's identity: SHA-256 over the canonical manifest."""
    return sha256_bytes(canonical(manifest).encode("utf-8"))


# ------------------------------------------------------------------ writing

def _crate(documents, description):
    """
    A minimal RO-Crate 1.1 description of the bundle. Labs already publish
    crates and Clew already reads them, so a bundle that is also a crate is
    one fewer format.
    """
    parts = sorted(set(documents) | {MANIFEST})
    return {
        "@context": "https://w3id.org/ro/crate/1.1/context",
        "@graph": [
            {"@id": CRATE, "@type": "CreativeWork",
             "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"},
             "about": {"@id": "./"}},
            {"@id": "./", "@type": "Dataset",
             "name": "Clew evidence bundle",
             "description": description,
             "hasPart": [{"@id": name} for name in parts]},
        ] + [
            {"@id": name, "@type": "File",
             "encodingFormat": "application/json" if name.endswith(".json")
                               else "text/plain"}
            for name in parts
        ],
    }


HOW_TO_VERIFY = """\
How to check this bundle without trusting whoever gave it to you
===============================================================

    clew evidence verify <this directory>

That needs Python and nothing else. No database, no network, no credentials,
and no cooperation from the party that produced this. It performs four
independent checks:

  files      every file hashes to what manifest.json records, and no file is
             present that the manifest does not list.

  log        the bundled event entries re-chain: the first one chains from
             genesis, or from the previous bundle's log head as recorded in
             the manifest; each entry's hash is recomputed from its own
             content and its predecessor's hash; and the last one matches
             the log head recorded in the manifest.

  policy     policy.json hashes to the value the plan cites. The plan and the
             table it was decided under cannot have drifted apart.

  replay     EVERY verdict in the plan is recomputed from the facts and the
             policy in this bundle, and must come out identical. This is what
             makes the plan a conclusion rather than a claim.

A signature, if manifest.json.sig is present, is checked separately and is
not Clew's to make. See the README section on countersigning.

What this bundle does NOT prove
-------------------------------
That the facts fed in were true, or that the policy was the right one. Those
belong to whoever has the authority to defend them. Clew proves the
computation, not the premises.

That any artifact was physically destroyed. No cryptography reaches a
freezer. The claim is proof of non-use, not proof of destruction.

That the systems not instrumented were clean. Anything uninstrumented is
reported unknown, never clean.
"""


def build(destination, documents, log_head, previous_bundle=None,
          previous_log_head=None, since=0, coverage=None,
          description="Clew evidence bundle", force=False):
    """
    Write a bundle and return its manifest and hash. `documents` maps a
    filename to a JSON-serialisable object; core does not interpret them.
    `log_head` is {seq, hash}. `since` is the seq the entries start after,
    chaining to genesis at 0 and otherwise to `previous_log_head` and
    `previous_bundle`, so each window verifies against the one before. A
    non-empty destination is refused unless `force`.
    """
    for name in documents:
        if not safe_name(name):
            raise ValueError(f"refusing to write a document named {name!r}")
    if since:
        if not previous_log_head or previous_log_head["seq"] != since:
            raise ValueError(
                f"a bundle starting after seq {since} must continue a "
                "previous bundle whose log head is that seq")
        if not previous_bundle:
            raise ValueError("a windowed bundle must name the bundle it "
                             "continues")
    if since > log_head["seq"]:
        raise ValueError(f"since={since} is past the log head "
                         f"(seq {log_head['seq']})")
    anchor = previous_log_head["hash"] if since else GENESIS

    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        if not force:
            raise FileExistsError(
                f"{destination} is not empty; pass force to replace its "
                "contents")
        for leftover in destination.iterdir():
            if leftover.is_dir() and not leftover.is_symlink():
                shutil.rmtree(leftover)
            else:
                leftover.unlink()
    destination.mkdir(parents=True, exist_ok=True)

    for name, document in documents.items():
        payload = json.dumps(document, indent=2, sort_keys=True) + "\n"
        (destination / name).write_text(payload)

    (destination / CRATE).write_text(
        json.dumps(_crate(documents, description), indent=2, sort_keys=True)
        + "\n")
    (destination / "HOW-TO-VERIFY.txt").write_text(HOW_TO_VERIFY)

    files = {}
    for path in sorted(destination.iterdir()):
        if path.is_file() and path.name not in UNLISTED:
            files[path.name] = sha256_file(path)

    manifest = {
        "clew_bundle_version": BUNDLE_VERSION,
        "description": description,
        "files": files,
        "anchors": {
            "log_head": {"seq": log_head["seq"], "hash": log_head["hash"]},
            "since": {"seq": since, "hash": anchor},
            "previous_bundle": previous_bundle,
            "previous_log_head": (
                {"seq": previous_log_head["seq"],
                 "hash": previous_log_head["hash"]}
                if previous_log_head else None),
        },
        "coverage": coverage or [],
    }
    (destination / MANIFEST).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    return manifest, bundle_hash(manifest)


# ---------------------------------------------------------------- verifying

def _check(name, ok, detail):
    return {"check": name, "ok": ok, "detail": detail}


def verify_files(directory, manifest):
    directory = Path(directory)
    recorded = manifest["files"]

    unsafe = [repr(n) for n in recorded if not safe_name(n)]
    if unsafe:
        return _check("files", False,
                      "the manifest names something outside the bundle: "
                      + ", ".join(sorted(unsafe)))

    missing = [n for n in recorded if not (directory / n).is_file()]
    if missing:
        return _check("files", False, f"missing from the bundle: "
                                      f"{', '.join(sorted(missing))}")

    altered = [n for n in sorted(recorded)
               if sha256_file(directory / n) != recorded[n]]
    if altered:
        return _check("files", False,
                      f"content does not match the manifest: "
                      f"{', '.join(altered)}")

    # An unlisted entry is not harmless. A bundle is meant to be exactly what
    # the manifest says it is, and a reader who opens the directory sees
    # everything in it, listed or not. A subdirectory is the easiest place
    # to put something a reader will find and the manifest never covered.
    present = {p.name for p in directory.iterdir() if p.name not in UNLISTED}
    extra = sorted(present - set(recorded))
    if extra:
        return _check("files", False,
                      f"present but not listed in the manifest: "
                      f"{', '.join(extra)}")

    return _check("files", True, f"{len(recorded)} files, all hashes match")


def chain_start(manifest):
    """Where the bundled entries begin: {seq, hash} they must chain from."""
    since = manifest["anchors"].get("since")
    if since is None:
        return {"seq": 0, "hash": GENESIS}
    return since


def verify_log(events, manifest, eventlog):
    """
    The bundled entries must re-chain from the recorded start to the
    recorded head. The start comes from the manifest, genesis or the
    previous bundle's head, never from the entries themselves, which would
    verify whatever they were forged to say. `eventlog` is passed in so this
    runs without a database driver.
    """
    anchors = manifest["anchors"]
    head = anchors["log_head"]
    start = chain_start(manifest)

    if start["seq"] == 0:
        if start["hash"] != GENESIS:
            return _check("log", False,
                          "a bundle starting at the beginning of the log "
                          "must chain from genesis, but the manifest records "
                          "a different start hash")
    else:
        previous = anchors.get("previous_log_head")
        if not previous or not anchors.get("previous_bundle"):
            return _check("log", False,
                          f"the entries start after seq {start['seq']} but "
                          "the manifest does not name the bundle they "
                          "continue; the chain cannot be anchored")
        if (previous["seq"], previous["hash"]) != (start["seq"],
                                                   start["hash"]):
            return _check("log", False,
                          "the recorded start does not match the previous "
                          "bundle's log head")

    if not events:
        if head["seq"] == start["seq"] and head["hash"] == start["hash"]:
            return _check("log", True,
                          "no entries covered; the log had not grown"
                          if start["seq"] else
                          "no entries covered; log was empty")
        return _check("log", False,
                      f"manifest claims a log head at seq {head['seq']} but "
                      "the bundle carries no entries")

    result = eventlog.verify_entries(
        events, start_seq=start["seq"] + 1, start_prev=start["hash"])
    if not result["ok"]:
        return _check("log", False,
                      f"chain broken at seq {result['broken_at']}: "
                      f"{result['reason']}")

    if result["head"] != head["hash"]:
        return _check("log", False,
                      "the entries do not end at the log head recorded in the "
                      "manifest; entries were added or removed after sealing")
    if events[-1]["seq"] != head["seq"]:
        return _check("log", False,
                      f"last entry is seq {events[-1]['seq']} but the manifest "
                      f"records head seq {head['seq']}")

    return _check("log", True,
                  f"{len(events)} entries re-chain to the recorded head "
                  f"(seq {head['seq']})")


def verify_against_log(manifest, hash_at_seq):
    """
    Hold a live log against what this bundle witnessed. `hash_at_seq`
    returns an entry's hash by sequence number, or None. A truncated chain
    passes verify() on its own; a bundle that left the building carrying the
    head it saw catches it.
    """
    anchor = manifest["anchors"]["log_head"]
    if anchor["seq"] == 0:
        return _check("witness", None,
                      "this bundle anchors to no log head; it witnesses a "
                      "computation but cannot detect a truncation")

    live = hash_at_seq(anchor["seq"])
    if live is None:
        return _check("witness", False,
                      f"the log has no entry at seq {anchor['seq']}, but this "
                      f"bundle recorded one. Entries have been removed from "
                      f"the end since this bundle was issued.")
    if live != anchor["hash"]:
        return _check("witness", False,
                      f"seq {anchor['seq']} in the log hashes to "
                      f"{live[:16]}..., but this bundle recorded "
                      f"{anchor['hash'][:16]}.... The log was rewritten after "
                      f"this bundle was issued.")
    return _check("witness", True,
                  f"the log still contains the head this bundle witnessed "
                  f"(seq {anchor['seq']})")


def verify_policy(plan, policy_document):
    stated = plan.get("policy_hash")
    actual = policy_module.fingerprint(policy_document)
    if not policy_module.cites(policy_document, stated):
        return _check("policy", False,
                      f"the plan cites policy hash {stated}, but the bundled "
                      f"policy hashes to {actual}")
    return _check("policy", True,
                  f"{policy_document['version']} matches the hash the plan "
                  f"cites")


def verify_gate(result, gate_policy, events):
    """
    Recompute a gate decision from the bundled facts and the bundled policy.

    The same discipline as verify_replay, for the other kind of answer Clew
    gives. A sealed "PASS" that cannot be re-derived from the facts it claims
    to rest on is not evidence that anything was checked.
    """
    recomputed = gate_module.decide(
        list(result["subjects"]), events,
        gate_policy["blocking"], gate_policy["clearing"],
        as_of=result.get("as_of"),
        unknown_blocks=result.get("unknown_blocks", True))

    if recomputed["passed"] != result["passed"]:
        return _check("gate", False,
                      f"recorded passed={result['passed']}, but recomputes to "
                      f"passed={recomputed['passed']}")

    disagreements = [
        f"{subject}: recorded {detail['status']}, recomputes to "
        f"{recomputed['subjects'][subject]['status']}"
        for subject, detail in result["subjects"].items()
        if recomputed["subjects"][subject]["status"] != detail["status"]
    ]
    if disagreements:
        return _check("gate", False,
                      f"{len(disagreements)} subjects disagree: "
                      + "; ".join(disagreements[:3]))

    return _check("gate", True,
                  f"passed={result['passed']}; all "
                  f"{len(result['subjects'])} subject outcomes recompute "
                  f"identically from the bundled facts and gate policy")


def verify_replay(plan, policy_document):
    """
    Recompute every verdict from the bundled facts and the bundled table.

    Not a spot check. If one line of a remediation plan cannot be re-derived,
    the plan is not evidence of anything, so every line is re-derived.
    """
    items = plan.get("plan", [])
    mismatches = []
    counts = {}
    for item in items:
        try:
            decision = policy_module.decide(
                item["contribution"], policy=policy_document,
                **policy_module.plan_item_facts(item))
        except ValueError as exc:
            mismatches.append(f"{item['task']}: {exc}")
            continue
        counts[decision["action"] or policy_module.UNDETERMINED] = counts.get(
            decision["action"] or policy_module.UNDETERMINED, 0) + 1
        if decision["action"] != item.get("action"):
            mismatches.append(
                f"{item['task']}: recorded {item.get('action')}, recomputes "
                f"to {decision['action']}")
        elif decision["rule"] != item.get("rule"):
            mismatches.append(
                f"{item['task']}: recorded rule {item.get('rule')}, "
                f"recomputes to {decision['rule']}")
        elif decision.get("possible") != item.get("possible"):
            # An undetermined item's candidates are its whole content. Left
            # unchecked, a plan could narrow "one of three" to "one of one"
            # and read as settled without ever stating a verdict.
            mismatches.append(
                f"{item['task']}: recorded possible {item.get('possible')}, "
                f"recomputes to {decision.get('possible')}")

    # The totals are read before any item is, so they are checked too. A
    # plan whose header says 16 affected while listing one is not a plan
    # with a rounding error; it is two documents pretending to be one.
    if plan.get("tasks_affected") != len(items):
        mismatches.append(
            f"header records tasks_affected={plan.get('tasks_affected')}, "
            f"but the plan lists {len(items)} items")
    if not mismatches and plan.get("actions") != counts:
        mismatches.append(
            f"header records actions={plan.get('actions')}, but the items "
            f"recompute to {counts}")

    if mismatches:
        return _check("replay", False,
                      f"{len(mismatches)} discrepancies across {len(items)} "
                      f"items; the plan does not reproduce: "
                      + "; ".join(mismatches[:3]))
    return _check("replay", True,
                  f"all {len(items)} verdicts and their counts recompute "
                  f"identically from the bundled facts and policy")
