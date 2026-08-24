"""
Clew core — the evidence bundle.

THIS FILE KNOWS NOTHING ABOUT BIOLOGY. It packages opaque documents, hashes
them, and re-checks them.

WHAT A BUNDLE IS FOR
--------------------
Clew claims three things. A bundle is the artifact that lets someone else
check all three without trusting us, without our database, and without our
code being the thing that says so:

  1. the log is append-only and unmodified   -> the bundled entries re-chain
  2. the computation is deterministic        -> the bundled policy is the one
                                                the plan cites, by hash
  3. the result follows from the inputs      -> every verdict is RECOMPUTED
                                                from the bundled facts

The third check is the one that matters and the one that is usually missing
from things called evidence packages. A folder of documents proves only that
somebody assembled a folder. Re-deriving each verdict from the facts and the
table, offline, is what makes the plan a conclusion rather than an assertion.

NO CLOCK IN THE BUNDLE
----------------------
Building the same bundle from the same inputs produces the same bundle hash.
That is deliberate and it is testable. A timestamp inside would change the
hash on every build and quietly destroy the reproducibility claim.

Time is not lost, it is just kept where it belongs: the bundle anchors to a
log head, and the log is the thing with clocks. Sealing a bundle is itself an
event, so "when" is answered by the log, dated and hash-chained, rather than
by a field the builder could have typed anything into.

HOW THIS CLOSES THE LOG'S OPEN GAP
----------------------------------
A hash chain detects editing but not truncation: lopping entries off the end
leaves a shorter, self-consistent chain, and a rewrite by whoever holds the
owner's credentials leaves no trace at all. Nothing inside the database can
fix that — the fix has to be a witness the database's owner does not control.

A bundle is that witness. It records the log head it covered, and it goes out
of the building: to an assessor, into a build artifact, to a partner. Bundles
also chain to each other, so a sequence of them pins a sequence of heads. To
make a truncation stick, someone would now have to collect every copy of
every bundle ever issued.

WHAT "SIGNED" HONESTLY MEANS HERE
---------------------------------
This module SEALS: a SHA-256 manifest over every file, and a bundle hash over
the manifest. That needs nothing but the standard library, so anyone can
verify it, which is the whole point.

It does NOT implement signing. A signature that can only be checked by
someone holding the signing key is not a signature in the sense an assessor
means, and inventing crypto here would be indefensible. Countersigning is
detached and delegated to tooling the reader already trusts and already
manages keys for — ssh-keygen -Y, which ships with OpenSSH. The seal is
Clew's; the attestation of WHO sealed it belongs to your key infrastructure.
"""

import hashlib
import json
from pathlib import Path

from core import gate as gate_module
from core import policy as policy_module

BUNDLE_VERSION = 1

MANIFEST = "manifest.json"
SIGNATURE = "manifest.json.sig"
CRATE = "ro-crate-metadata.json"

# Never listed in the manifest: the manifest cannot hash itself, and the
# signature is made over the manifest and therefore written after it.
UNLISTED = {MANIFEST, SIGNATURE}


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
    A minimal RO-Crate 1.1 description of the bundle.

    Adopted rather than invented: labs already publish crates for journals and
    archives, and Clew already ingests them. A bundle that is also a crate is
    one fewer format for a reader to learn, and it survives being handed to
    tooling that knows nothing about Clew.
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

    python3 evidence.py verify <this directory>

That needs Python and nothing else. No database, no network, no credentials,
and no cooperation from the party that produced this. It performs four
independent checks:

  files      every file hashes to what manifest.json records, and no file is
             present that the manifest does not list.

  log        the bundled event entries re-chain: each entry's hash is
             recomputed from its own content and its predecessor's hash, and
             the last one matches the log head recorded in the manifest.

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
          coverage=None, description="Clew evidence bundle"):
    """
    Write a bundle and return its manifest and hash.

    `documents` maps a filename to a JSON-serialisable object. Core does not
    know or care what any of them mean; the caller decides what belongs.

    `log_head` is {seq, hash} for the log this bundle witnesses.
    `previous_bundle` is the hash of the bundle before this one, if any, so a
    sequence of bundles pins a sequence of log heads.
    """
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)

    written = {}
    for name, document in documents.items():
        payload = json.dumps(document, indent=2, sort_keys=True) + "\n"
        (destination / name).write_text(payload)
        written[name] = payload

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
            "previous_bundle": previous_bundle,
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

    # An unlisted file is not harmless. A bundle is meant to be exactly what
    # the manifest says it is, and a reader who opens the directory sees
    # every file in it, listed or not.
    present = {p.name for p in directory.iterdir()
               if p.is_file() and p.name not in UNLISTED}
    extra = sorted(present - set(recorded))
    if extra:
        return _check("files", False,
                      f"present but not listed in the manifest: "
                      f"{', '.join(extra)}")

    return _check("files", True, f"{len(recorded)} files, all hashes match")


def verify_log(events, manifest, eventlog):
    """
    The bundled entries must re-chain and end where the manifest says.

    `eventlog` is passed in rather than imported so this stays usable in an
    environment with no database driver installed — which is exactly the
    environment an auditor checking a bundle is in.
    """
    head = manifest["anchors"]["log_head"]
    if not events:
        if head["seq"] == 0:
            return _check("log", True, "no entries covered; log was empty")
        return _check("log", False,
                      f"manifest claims a log head at seq {head['seq']} but "
                      "the bundle carries no entries")

    start_seq = events[0]["seq"]
    result = eventlog.verify_entries(
        events, start_seq=start_seq,
        start_prev=events[0]["prev_hash"])
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
    Hold a live log up against what this bundle witnessed.

    `hash_at_seq` is a callable taking a sequence number and returning that
    entry's hash, or None if the log has no such entry. A callable rather
    than a connection so this stays driver-free and testable.

    This is the check that closes the log's open gap. A truncated chain is
    internally consistent and verify() on the log alone passes — nothing
    inside the database can notice something that is no longer in it. A
    bundle can, because it left the building carrying the head it saw.
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
    if stated != actual:
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
    mismatches = []
    for item in plan.get("plan", []):
        decision = policy_module.decide(
            item["contribution"],
            storage=item["storage"],
            exclusive=item["exclusive"],
            terminal=item["terminal"],
            policy=policy_document,
        )
        if decision["action"] != item["action"]:
            mismatches.append(
                f"{item['task']}: recorded {item['action']}, recomputes to "
                f"{decision['action']}")
        elif decision["rule"] != item["rule"]:
            mismatches.append(
                f"{item['task']}: recorded rule {item['rule']}, recomputes to "
                f"{decision['rule']}")

    if mismatches:
        return _check("replay", False,
                      f"{len(mismatches)} of {len(plan.get('plan', []))} "
                      f"verdicts do not reproduce: " + "; ".join(mismatches[:3]))
    return _check("replay", True,
                  f"all {len(plan.get('plan', []))} verdicts recompute "
                  f"identically from the bundled facts and policy")
