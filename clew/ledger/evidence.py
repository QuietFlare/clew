"""
Clew — build and check evidence bundles.

    # seal a plan, the policy it used, and the log entries behind it
    clew evidence build --out bundle/ --plan plan.json \
        --dsn "$CLEW_DSN" --input graph.json --input donors.csv

    # anyone, anywhere, with Python and nothing else
    clew evidence verify bundle/

    # countersigning is delegated, not invented
    clew evidence sign bundle/ --key ~/.ssh/id_ed25519
    clew evidence verify bundle/ --allowed-signers allowed_signers

WHY THE VERIFIER TAKES NO CREDENTIALS
-------------------------------------
`verify` reads a directory. It does not connect to the database, does not
call us, and does not need the driver installed. An assessor who does not
trust the party that produced a bundle must be able to check it anyway, and
any step that routes through the producer's infrastructure defeats that.

WHY BUILDING IS SEPARATE FROM COMPUTING
---------------------------------------
clew impact answers a question. This packages an answer that was already given.
Keeping them apart means a bundle can only ever contain a plan that was
produced independently — sealing cannot quietly recompute something on more
convenient terms on its way into the folder.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


from clew.ledger import bundle
from clew.ledger import policy as policy_module

SEALED = "BundleSealed"


def load_json(path):
    return json.loads(Path(path).read_text())


def open_log(dsn):
    """Connect, or fail with something an operator can act on."""
    from clew.ledger import eventlog
    try:
        return eventlog.connect(dsn)
    except ImportError:
        raise SystemExit(
            "attaching a log needs psycopg: pip install 'psycopg[binary]'")
    except Exception as exc:
        raise SystemExit(f"cannot connect: {str(exc).strip().splitlines()[0]}")


def resolve_policy_for(plan, override):
    """
    The exact table the plan was decided under — never a substitute.

    A plan citing a version this build does not ship cannot be sealed here.
    Quietly bundling today's table instead would produce a bundle whose replay
    check passes against the wrong policy, which is worse than no bundle.
    """
    if override:
        document = policy_module.resolve_or_load(override)
    else:
        version = plan.get("policy_version")
        if version not in policy_module.REGISTRY:
            raise SystemExit(
                f"the plan cites policy {version!r}, which this build does not "
                f"ship ({', '.join(sorted(policy_module.REGISTRY))}). Pass "
                "--policy pointing at the table it was computed under.")
        document = policy_module.resolve(version)

    stated = plan.get("policy_hash")
    actual = policy_module.fingerprint(document)
    if stated != actual:
        raise SystemExit(
            f"policy mismatch: the plan cites {stated}, the table given "
            f"hashes to {actual}. Seal the table the plan actually used.")
    return document


def record_inputs(paths):
    """
    Source files by content hash, with the basename only.

    Keyed by hash rather than by path so the same inputs give the same
    bundle whatever directory they were read from: a bundle hash that
    changed because someone typed ./graph.json instead of graph.json
    would be a reproducibility claim with a hole in it.
    """
    inputs = {}
    for path in paths or []:
        digest = bundle.sha256_file(path)
        name = Path(path).name
        seen = inputs.get(digest)
        if seen and seen["name"] != name:
            raise SystemExit(
                f"--input {path} has the same content as {seen['name']}; "
                "record it once")
        inputs[digest] = {"name": name, "bytes": Path(path).stat().st_size}
    return inputs


def continue_from(previous_dir, since):
    """The previous bundle's hash and log head, checked against --since."""
    if not previous_dir:
        if since:
            raise SystemExit(
                f"--since {since} needs --previous: the entries after that "
                "seq can only be anchored to the bundle that ended there")
        return None, None
    manifest_path = Path(previous_dir) / bundle.MANIFEST
    if not manifest_path.is_file():
        raise SystemExit(
            f"--previous {previous_dir} holds no {bundle.MANIFEST}, so "
            "it is not a sealed bundle and nothing can chain to it.")
    manifest = load_json(manifest_path)
    previous_head = manifest["anchors"]["log_head"]
    if since and previous_head["seq"] != since:
        raise SystemExit(
            f"--since {since} does not match the previous bundle, whose log "
            f"head is seq {previous_head['seq']}")
    return bundle.bundle_hash(manifest), previous_head


def cmd_build(args):
    plan = load_json(args.plan)
    policy_document = resolve_policy_for(plan, args.policy)
    previous, previous_head = continue_from(args.previous, args.since)

    events, log_head = [], {"seq": 0, "hash": "0" * 64}
    if args.dsn:
        from clew.ledger import eventlog
        conn = open_log(args.dsn)
        log_head = eventlog.head(conn)
        events = eventlog.raw(conn, since=args.since)
        # Check the window against the live log before sealing it. A
        # previous bundle from a different log, or a log rewritten since,
        # would otherwise be sealed into a bundle that can never verify.
        try:
            anchor = eventlog.anchor(conn, args.since)
        except LookupError as exc:
            raise SystemExit(f"cannot continue from --since {args.since}: "
                             f"{exc}")
        if previous_head and args.since and anchor != previous_head["hash"]:
            raise SystemExit(
                f"the previous bundle's log head (seq {args.since}) does not "
                "match this log's entry at that seq; it was sealed from a "
                "different log, or the log was rewritten since")
        chain = eventlog.verify_entries(events, start_seq=args.since + 1,
                                        start_prev=anchor)
        if not chain["ok"]:
            raise SystemExit(f"refusing to seal a broken log: seq "
                             f"{chain['broken_at']}, {chain['reason']}")
    elif args.since:
        raise SystemExit("--since needs --dsn; there is no log to window")

    inputs = record_inputs(args.input)

    # Coverage, stated rather than implied. Everything here is a limit of
    # what the bundle witnesses, and a reader should not have to infer any
    # of it from what is absent.
    undetermined = sum(1 for i in plan.get("plan", []) if not i["action"])
    coverage = list(plan.get("caveats", []))
    if undetermined:
        coverage.append(
            f"{undetermined} of {len(plan.get('plan', []))} items are "
            "UNDETERMINED: storage was not verified. They are unanswered, "
            "not clean.")
    if not args.dsn:
        coverage.append(
            "no event log was attached: this bundle witnesses a computation "
            "but anchors to no log head, so it cannot detect a later "
            "truncation of anything.")
    elif args.since:
        coverage.append(
            f"log entries 1-{args.since} are not in this bundle; the chain "
            "here starts from the log head the previous bundle recorded, "
            "and that earlier bundle is where those entries are witnessed.")

    documents = {
        "plan.json": plan,
        "policy.json": policy_document,
        "events.json": events,
        "inputs.json": inputs,
    }
    try:
        manifest, digest = bundle.build(
            args.out, documents, log_head=log_head, previous_bundle=previous,
            previous_log_head=previous_head, since=args.since,
            coverage=coverage, force=args.force,
            description=f"Clew evidence bundle for trigger: "
                        f"{plan.get('trigger')}")
    except FileExistsError:
        raise SystemExit(
            f"{args.out} is not empty. A bundle must be exactly what its "
            "manifest lists, so it gets a directory of its own; pass --force "
            "to replace what is there.")

    print(f"wrote {args.out}")
    print(f"  bundle hash     {digest}")
    print(f"  files           {len(manifest['files'])}")
    print(f"  log head        seq {log_head['seq']}  {log_head['hash'][:16]}")
    print(f"  events covered  {len(events)}")
    print(f"  policy          {plan.get('policy_version')} "
          f"{plan.get('policy_hash', '')[:16]}")
    if previous:
        print(f"  chains to       {previous[:16]}")
    for note in coverage:
        print(f"  coverage: {note}")

    if args.seal_into_log:
        if not args.dsn:
            raise SystemExit("--seal-into-log needs --dsn")
        from clew.ledger import eventlog
        conn = open_log(args.dsn)
        entry = eventlog.append(
            conn, event_type=SEALED, subject=digest, actor=args.actor,
            # The bundle records the log head; the log records the bundle.
            # Neither can be quietly rolled back without contradicting the
            # other, which is the point of writing it in both directions.
            body={"bundle_hash": digest,
                  "covers_log_head": log_head,
                  "previous_bundle": previous,
                  "trigger": plan.get("trigger"),
                  "policy_version": plan.get("policy_version")})
        print(f"\nsealed into the log as seq {entry['seq']}")
        print("A later truncation past this point now contradicts a bundle")
        print("that has already left the building.")
    return 0


def cmd_verify(args):
    directory = Path(args.bundle)
    try:
        manifest = load_json(directory / bundle.MANIFEST)
    except OSError:
        raise SystemExit(f"{directory}/{bundle.MANIFEST} not readable; "
                         "this does not look like a bundle")

    checks = [bundle.verify_files(directory, manifest)]
    sealed = set(manifest["files"])

    # Only read the bundled documents once the files are known to be intact;
    # parsing a tampered file and reporting on its contents would be reporting
    # on something we have just been told not to believe.
    if not checks[0]["ok"]:
        checks.append(bundle._check(
            "contents", None,
            "not checked: the files themselves do not verify"))
    else:
        from clew.ledger import eventlog

        # The log check always runs. A bundle that names a log head and
        # carries no entries is not a bundle with one check fewer; it is a
        # bundle whose claim about the log cannot be examined.
        events = load_json(directory / "events.json") \
            if "events.json" in sealed else []
        checks.append(bundle.verify_log(events, manifest, eventlog))

        # A bundle seals one kind of answer or the other. Checking for the
        # documents rather than assuming a shape means a gate result gets the
        # gate's own re-derivation instead of silently skipping every check
        # that mattered.
        if "plan.json" in sealed:
            plan = load_json(directory / "plan.json")
            policy_document = load_json(directory / "policy.json")
            checks.append(bundle.verify_policy(plan, policy_document))
            checks.append(bundle.verify_replay(plan, policy_document))
        elif "gate.json" in sealed:
            result = load_json(directory / "gate.json")
            gate_policy = load_json(directory / "gate-policy.json")
            checks.append(bundle.verify_gate(result, gate_policy, events))
        else:
            checks.append(bundle._check(
                "contents", False,
                "this bundle seals neither a plan nor a gate result, so there "
                "is nothing in it to re-derive"))

    checks.append(check_signature(directory, args.allowed_signers))

    digest = bundle.bundle_hash(manifest)
    print(f"bundle {digest}")
    print(f"  covers log head seq {manifest['anchors']['log_head']['seq']}")
    start = bundle.chain_start(manifest)
    if start["seq"]:
        print(f"  entries after   seq {start['seq']}")
    if manifest["anchors"]["previous_bundle"]:
        print(f"  chains to       "
              f"{manifest['anchors']['previous_bundle'][:16]}")
    print()
    for check in checks:
        mark = "ok  " if check["ok"] else ("FAIL" if check["ok"] is False
                                           else "-   ")
        print(f"  {mark} {check['check']:<10} {check['detail']}")

    if manifest.get("coverage"):
        print("\ncoverage, as recorded when the bundle was sealed:")
        for note in manifest["coverage"]:
            print(f"  - {note}")

    failed = [c for c in checks if c["ok"] is False]
    print()
    if failed:
        print(f"FAILED: {len(failed)} check(s) did not pass.")
        return 1
    print("All checks passed. This proves the computation, not the premises:")
    print("Clew says nothing about whether the facts fed in were true or the")
    print("policy was the right one. Those belong to whoever can defend them.")
    return 0


def cmd_witness(args):
    """
    Hold a live log up against a bundle that has already left the building.

    Deliberately a separate command from `verify`. Verifying a bundle needs
    no credentials and must stay that way; this one needs the log, and
    folding it into `verify` would make the credential-free property look
    optional when it is the whole design.
    """
    from clew.ledger import eventlog

    manifest = load_json(Path(args.bundle) / bundle.MANIFEST)
    conn = open_log(args.dsn)

    def hash_at_seq(seq):
        try:
            return eventlog.anchor(conn, seq)
        except LookupError:
            return None

    check = bundle.verify_against_log(manifest, hash_at_seq)
    digest = bundle.bundle_hash(manifest)
    mark = "ok  " if check["ok"] else ("FAIL" if check["ok"] is False else "-   ")
    print(f"bundle {digest}")
    print(f"  {mark} {check['check']:<10} {check['detail']}")
    return 1 if check["ok"] is False else 0


def check_signature(directory, allowed_signers):
    signature = Path(directory) / bundle.SIGNATURE
    if not signature.exists():
        return bundle._check(
            "signature", None,
            "no signature present; the seal is intact but nothing attests to "
            "who produced it")
    if not allowed_signers:
        return bundle._check(
            "signature", None,
            "a signature is present but no --allowed-signers file was given, "
            "so it was not checked")
    if not shutil.which("ssh-keygen"):
        return bundle._check("signature", None,
                               "ssh-keygen not available to check it")

    # Ask the signature who signed it, then check that claim against the
    # allowed_signers file. Verifying requires naming a principal, and an
    # auditor holding a bundle has no reason to know one in advance — the
    # useful output here is WHO attested to it, which this recovers.
    found = subprocess.run(
        ["ssh-keygen", "-Y", "find-principals", "-s", str(signature),
         "-f", str(allowed_signers)],
        capture_output=True, text=True)
    if found.returncode != 0 or not found.stdout.strip():
        return bundle._check(
            "signature", False,
            "the signing key is not listed in the allowed_signers file, so "
            "the signature is by someone this reader has no reason to trust")

    principals = found.stdout.split()
    failures = []
    for principal in principals:
        result = subprocess.run(
            ["ssh-keygen", "-Y", "verify", "-f", str(allowed_signers),
             "-I", principal, "-n", "clew-evidence", "-s", str(signature)],
            stdin=open(Path(directory) / bundle.MANIFEST, "rb"),
            capture_output=True, text=True)
        if result.returncode == 0:
            return bundle._check("signature", True,
                                   f"sealed by {principal}")
        failures.append((result.stderr or result.stdout).strip())
    return bundle._check("signature", False,
                           f"{', '.join(principals)}: {failures[0]}")


def cmd_sign(args):
    """
    Countersign the manifest with ssh-keygen. Clew implements no crypto.

    The manifest is the right thing to sign: it already covers every file by
    hash, so one signature over it attests to the whole bundle, and the
    signature can be added, replaced or made by several parties without any
    of the sealed content changing.
    """
    if not shutil.which("ssh-keygen"):
        raise SystemExit("ssh-keygen not found; it ships with OpenSSH")
    manifest = Path(args.bundle) / bundle.MANIFEST
    if not manifest.exists():
        raise SystemExit(f"{manifest} not found")
    result = subprocess.run(
        ["ssh-keygen", "-Y", "sign", "-f", args.key, "-n", "clew-evidence",
         str(manifest)],
        capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit((result.stderr or result.stdout).strip())
    print(f"wrote {manifest}.sig")
    print("Give the verifier an allowed_signers file naming the public key:")
    print("  <identity> namespaces=\"clew-evidence\" ssh-ed25519 AAAA...")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build and check Clew evidence bundles.")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="seal a plan into a bundle")
    build.add_argument("--out", required=True, metavar="DIR")
    build.add_argument("--plan", required=True,
                       help="plan JSON from clew impact --json")
    build.add_argument("--policy", metavar="VERSION|PATH",
                       help="the table the plan was computed under; inferred "
                            "from the plan when it names a shipped version")
    build.add_argument("--dsn", default=os.environ.get("CLEW_DSN"),
                       help="event log to witness; defaults to $CLEW_DSN")
    build.add_argument("--since", type=int, default=0,
                       help="cover log entries after this seq (exclusive), "
                            "for bundles that continue an earlier one")
    build.add_argument("--input", action="append", metavar="PATH",
                       help="a source file to record by hash; repeatable")
    build.add_argument("--previous", metavar="DIR",
                       help="the previous bundle, to chain to it; required "
                            "with --since")
    build.add_argument("--force", action="store_true",
                       help="replace the contents of a non-empty --out")
    build.add_argument("--seal-into-log", action="store_true",
                       help="record the bundle hash back into the event log")
    build.add_argument("--actor", default="unknown",
                       help="who sealed it, for --seal-into-log")
    build.set_defaults(run=cmd_build)

    check = sub.add_parser("verify", help="check a bundle; needs no credentials")
    check.add_argument("bundle", metavar="DIR")
    check.add_argument("--allowed-signers", metavar="FILE",
                       help="ssh allowed_signers file, to check a signature")
    check.set_defaults(run=cmd_verify)

    witness = sub.add_parser(
        "witness", help="check a live log against what a bundle witnessed")
    witness.add_argument("bundle", metavar="DIR")
    witness.add_argument("--dsn", default=os.environ.get("CLEW_DSN"),
                         help="the log to hold up against it")
    witness.set_defaults(run=cmd_witness)

    sign = sub.add_parser("sign", help="countersign a bundle with ssh-keygen")
    sign.add_argument("bundle", metavar="DIR")
    sign.add_argument("--key", required=True, help="private key to sign with")
    sign.set_defaults(run=cmd_sign)

    args = parser.parse_args(argv)
    raise SystemExit(args.run(args) or 0)


if __name__ == "__main__":
    main()
