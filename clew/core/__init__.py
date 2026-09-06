"""Old import paths. clew.core became clew.graph and clew.ledger in 0.3."""

import importlib
import sys
import warnings

MOVED = {
    "graph": "clew.graph.graph",
    "triggers": "clew.graph.triggers",
    "blast_radius": "clew.graph.blast_radius",
    "contribution": "clew.graph.contribution",
    "eventlog": "clew.ledger.eventlog",
    "policy": "clew.ledger.policy",
    "evidence": "clew.ledger.bundle",
    "bundlestore": "clew.ledger.bundlestore",
    "query": "clew.ledger.query",
    "gate": "clew.ledger.gate",
}

for _old, _new in MOVED.items():
    sys.modules[f"clew.core.{_old}"] = importlib.import_module(_new)


def __getattr__(name):
    if name not in MOVED:
        raise AttributeError(name)
    warnings.warn(f"clew.core.{name} is now {MOVED[name]}",
                  DeprecationWarning, stacklevel=2)
    return sys.modules[f"clew.core.{name}"]
