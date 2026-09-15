"""A domain is what a site knows about one pipeline. docs/providers.md has worked examples."""

from .registry import Provider


class Adapter(Provider):
    group = "clew.adapters"

    # kind name -> Trigger. What can go wrong here, in this pipeline's own
    # words, and where each enters. Empty is valid: the engine's own kinds
    # still apply.
    triggers = {}

    # reference files: triggers in their own right, never owned by anyone
    load_bearing_inputs = ()

    def contribution(self, graph, task_hash, kind):
        """
        Optional. The class of this task's output with respect to one value of
        `kind` being removed: SEPARABLE, REGENERABLE or IRREDUCIBLE. None keeps
        the engine's evidence-based answer. The plan records that the adapter said so.
        """
        return None

    def pending(self):
        """
        Optional. Triggers recorded at this site and not yet asked:
        [{"kind": "batch", "value": "B017", "asserted_by": "qa", "date": "2026-09-10"}]
        With any returned, `clew impact --pipeline X` and no trigger answers each.
        """
        return []


def check(trigger):
    """Problems with one trigger record; empty when usable."""
    if not isinstance(trigger, dict):
        return ["not an object"]
    return [f"{f} missing or empty" for f in ("kind", "value")
            if not isinstance(trigger.get(f), str) or not trigger[f]]
