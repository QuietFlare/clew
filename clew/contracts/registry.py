"""Defining a named subclass of a contract registers it. An entry point imports the module; that is all."""

from abc import ABC
from importlib import metadata


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


def discover(contract):
    """{name: provider} for one contract, from every installed package."""
    declared = entry_points(contract.group)
    for _, _, entry in declared:
        entry.load()
    if not declared and not contract.registered:
        raise SystemExit(
            f"no {contract.group} providers are installed. Clew's own are declared in "
            "its package metadata, so a checkout must be installed: "
            "python3 -m pip install -e .")
    return dict(contract.registered)
