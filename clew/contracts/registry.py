"""Defining a named subclass of a contract registers it. An entry point imports the module; that is all."""

import hashlib
import importlib.util
import json
import os
import sys
from abc import ABC
from importlib import metadata
from pathlib import Path

# A folder of single-file providers a person approved, for sites that build
# one without packaging it. Off unless this names the folder.
LOCAL_VARIABLE = "CLEW_PROVIDER_DIR"
# Set only while a new provider is being judged, before anyone has approved it.
TRIAL_VARIABLE = "CLEW_PROVIDER_TRIAL"


class Provider(ABC):
    name = None
    group = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if "registered" not in cls.__dict__ and Provider in cls.__bases__:
            cls.registered = {}
        if not cls.name:
            return
        root = next(c for c in cls.__mro__ if Provider in c.__bases__)
        # ABCMeta has not marked this class yet, so check the root's set by hand.
        missing = sorted(m for m in root.__abstractmethods__
                         if getattr(getattr(cls, m), "__isabstractmethod__", False))
        if missing:
            raise TypeError(f"{cls.__name__} does not implement {', '.join(missing)}")
        root.registered[cls.name] = cls()


def entry_points(group):
    """[(name, distribution name, entry point)] declared for a group, built-ins included."""
    # Walk distributions rather than metadata.entry_points(): an entry point
    # only knows its distribution from 3.10, but a distribution has always
    # known its entry points. One path for every supported Python.
    listed, seen = [], set()
    try:
        dists = list(metadata.distributions())
    except Exception:  # a broken distribution must not take the CLI down
        return []
    for dist in dists:
        try:
            package, entries = dist.metadata["Name"], dist.entry_points
        except Exception:
            continue
        for entry in entries:
            if entry.group == group and (entry.name, entry.value) not in seen:
                seen.add((entry.name, entry.value))
                listed.append((entry.name, package, entry))
    return listed


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def approval_of(source):
    """(record, problem) for one local provider file. The record sits beside it."""
    record = Path(source).with_suffix(".approval.json")
    try:
        approval = json.loads(record.read_text())
    except (OSError, ValueError):
        return None, "no approval record beside it"
    if not approval.get("actor"):
        return approval, "its approval names nobody"
    if approval.get("sha256") != file_hash(source):
        return approval, f"changed since {approval['actor']} approved it"
    return approval, None


def local_module(source):
    """The module name a local provider file is imported under: its hash is part of it."""
    return f"clew_local_{Path(source).stem}_{file_hash(source)[:12]}"


def load_local():
    """
    Import the provider files in the local folder. A file loads only when
    its approval record names a person and the hash of the file as they
    approved it. Returns [(file, problem or None)], so a refusal can be shown.
    """
    folder = os.environ.get(LOCAL_VARIABLE)
    if not folder or not Path(folder).is_dir():
        return []
    seen = []
    for source in sorted(Path(folder).glob("*.py")):
        problem = None if os.environ.get(TRIAL_VARIABLE) else approval_of(source)[1]
        if problem is None:
            module = local_module(source)
            if module not in sys.modules:
                try:
                    spec = importlib.util.spec_from_file_location(module, source)
                    loaded = importlib.util.module_from_spec(spec)
                    sys.modules[module] = loaded
                    spec.loader.exec_module(loaded)
                except Exception as bad:  # one broken file must not take the rest down
                    sys.modules.pop(module, None)
                    problem = f"failed to import: {bad}"
        seen.append((source, problem))
    return seen


def discover(contract):
    """{name: provider} for one contract, from every installed package and the local folder."""
    declared = entry_points(contract.group)
    for _, _, entry in declared:
        entry.load()
    load_local()
    if not declared and not contract.registered:
        raise SystemExit(
            f"no {contract.group} providers are installed. Clew's own are declared in "
            "its package metadata, so a checkout must be installed: "
            "python3 -m pip install -e .")
    return dict(contract.registered)
