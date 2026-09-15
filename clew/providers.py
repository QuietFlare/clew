"""
    clew providers

Every adapter and extractor Clew can see, and the package each came from.
The check to run when your own package does not show up.
"""

from clew.contracts import Adapter, Extractor
from clew.contracts.registry import entry_points
from clew.contracts.trigger import ENGINE_KINDS


def main(argv=None):
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
