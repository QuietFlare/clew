"""
    clew providers
    clew providers --approve verdict.json --actor NAME

Every adapter and extractor Clew can see, and the package each came from.
The check to run when your own package does not show up.

With --approve, a person installs a provider file a judge passed into the
folder CLEW_PROVIDER_DIR names, under their own name. The record beside
the file carries the file's hash, so a later change stops it loading.
"""

import argparse
import os

from clew.contracts import Adapter, Extractor
from clew.contracts.registry import (LOCAL_VARIABLE, approval_of, entry_points, load_local,
                                     local_module)
from clew.contracts.trigger import ENGINE_KINDS


def approve(verdict, actor):
    from clew.builder import adapter as builder
    folder = os.environ.get(LOCAL_VARIABLE)
    if not folder:
        raise SystemExit(f"set {LOCAL_VARIABLE} to the folder approved providers are kept in")
    try:
        record = builder.approve(verdict, folder, actor)
    except builder.Refused as bad:
        raise SystemExit(f"clew providers: {bad}")
    print(f"installed {record['name']} as {builder.installed_as(folder, record['name'])}, "
          f"approved by {record['actor']}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="clew providers",
                                     description="Every provider Clew can see, or approve a judged one.")
    parser.add_argument("--approve", metavar="VERDICT", help="the verdict a judge printed")
    parser.add_argument("--actor", help="who approves, with --approve")
    args = parser.parse_args(argv)
    if args.approve:
        if not (args.actor or "").strip():
            parser.error("--approve needs --actor, the name of the person approving")
        return approve(args.approve, args.actor.strip())
    for contract in (Adapter, Extractor):
        print(f"{contract.group}")
        rows = sorted(entry_points(contract.group))
        if not rows:
            print("  none installed")
        width = max((len(name) for name, _, _ in rows), default=0)
        for name, dist, entry in rows:
            try:
                entry.load()
                state = "" if name in contract.registered else "  module imported but registered nothing"
            except Exception as exc:  # a broken provider is what this command is for
                state = f"  FAILED to import: {exc}"
            print(f"  {name.ljust(width)}  {dist}{state}")
            if contract is Adapter and name in contract.registered:
                for kind, trig in contract.registered[name].triggers.items():
                    shadow = "  shadows the engine's" if kind in ENGINE_KINDS else ""
                    print(f"  {' ' * width}    {kind}: {trig.mode.value}{shadow}")
        print()
    local = load_local()
    if local:
        print(f"local provider files ({LOCAL_VARIABLE})")
        for source, problem in local:
            approval = approval_of(source)[0] or {}
            state = f"REFUSED: {problem}" if problem else \
                f"approved by {approval.get('actor', 'nobody yet, on trial')}"
            print(f"  {source.name}  {state}")
            if problem:
                continue
            for contract in (Adapter, Extractor):
                for name, provider in sorted(contract.registered.items()):
                    if type(provider).__module__ == local_module(source):
                        print(f"    {contract.group[5:-1]} {name}")
        print()
    from clew.ledger import policy
    print(policy.POLICY_GROUP)
    registered = {n for n, _, _ in entry_points(policy.POLICY_GROUP)}
    for name, dist, _ in sorted(entry_points(policy.POLICY_GROUP)):
        try:
            stamp = policy.identify(policy.available()[name])
            state = f"sha256 {stamp['policy_hash'][:16]}"
        except policy.InvalidPolicy as bad:
            state = f"REFUSED: {bad}"
        print(f"  {name}  {dist}  {state}")
    for name in sorted(policy.REGISTRY):
        if name not in registered:
            print(f"  {name}  clew-lineage  shipped")
    print()
    print("engine kinds, every graph: " + ", ".join(sorted(ENGINE_KINDS)) + ", and any label key")
    return 0
