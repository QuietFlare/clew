"""A trigger kind: how kind:value becomes entry nodes, and whether the value is traced or removed."""

from enum import Enum

from clew.graph import triggers as engine


class Mode(Enum):
    TRACE = "trace"    # follow what it touched; everything stays, worst case quarantine
    REMOVE = "remove"  # the source is removed; what only it fed can be destroyed


TRACE, REMOVE = Mode.TRACE, Mode.REMOVE


class Trigger:
    mode = Mode.TRACE

    def add_arguments(self, parser):
        """Flags this kind needs, if any."""

    def resolve(self, graph, value, args):
        """{id: [entry nodes]}; a removal includes every peer, and value None means all."""
        raise NotImplementedError

    def values(self, args, graph=None):
        """Every id this kind can name, for the gate. Optional."""
        raise NotImplementedError("this kind cannot list its values")


class FieldKind(Trigger):
    """An engine kind: resolved from a field every graph carries."""

    def __init__(self, name, finder):
        self.name, self.finder = name, finder

    def resolve(self, graph, value, args):
        if value is None:
            raise SystemExit(f"{self.name}: a value is required, for example {self.name}:x")
        return {f"{self.name}:{value}": self.finder(graph, value)}


class LabelKind(Trigger):
    """Any other word: a label key the graph carries on tasks or edges."""

    def __init__(self, key):
        self.key = key

    def resolve(self, graph, value, args):
        if value is None:
            raise SystemExit(f"{self.key}: a value is required")
        return {f"{self.key}:{value}": engine.label(self.key)(graph, value)}


ENGINE_KINDS = {name: FieldKind(name, finder) for name, finder in engine.KINDS.items()}


def lookup(domain, kind, graph=None):
    """The domain's kind, else the engine's, else a label the graph carries, else None."""
    if domain is not None and kind in domain.triggers:
        return domain.triggers[kind]
    if kind in ENGINE_KINDS:
        return ENGINE_KINDS[kind]
    if graph is not None and kind in engine.label_keys(graph):
        return LabelKind(kind)
    return None


def parse(spec):
    """`kind:value` or bare `kind`, which means every value of that kind."""
    kind, sep, value = spec.partition(":")
    if not kind:
        raise SystemExit(f"trigger {spec!r} should look like kind:value, "
                         "for example container:toolkit or batch:B017")
    return kind, (value if sep else None) or None
