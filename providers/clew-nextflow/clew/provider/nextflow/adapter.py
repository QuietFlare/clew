"""
Nextflow names each task "PROCESS (tag)" and nf-core launches from a CSV
samplesheet whose ids appear in those tags. SheetKind joins the two:

    triggers = {"patient": SheetKind(column="patient", members="sample")}
"""

import csv
import re

from clew.contracts import REMOVE, Adapter, Trigger

# The trailing parenthetical on a task's display name: "ALIGN (ID-003)".
# Many tasks use the same shape for other things, so a captured value is
# only accepted when it matches an id from the sheet.
TAG_PATTERN = re.compile(r"\(([^()]+)\)\s*$")


def read_sheet(path, column, members=None):
    """{id: [member ids]} from one column. One id under two owners is refused."""
    ids = {}
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            owner = (row.get(column) or "").strip()
            if not owner:
                continue
            ids.setdefault(owner, [])
            if members:
                member = (row.get(members) or "").strip()
                if member and member not in ids[owner]:
                    ids[owner].append(member)
    owners = {}
    shared = {}
    for owner, names in ids.items():
        for label in [owner] + names:
            if label in owners and owners[label] != owner:
                shared.setdefault(label, {owners[label]}).add(owner)
            owners.setdefault(label, owner)
    if shared:
        listing = "; ".join(f"{label!r} under {', '.join(sorted(owners))}"
                            for label, owners in sorted(shared.items()))
        raise SystemExit(f"{path}: the same id appears under more than one "
                         f"{column}, so tasks tagged with it cannot be attributed: {listing}")
    return ids


def task_tag(task):
    """The trailing parenthetical of a task's display name, or None."""
    match = TAG_PATTERN.search(task.get("name", ""))
    return match.group(1).strip() if match else None


def owner_of(tag, label_to_owner):
    """The id a tag belongs to. Lane suffixes ("ID-003-L1") match at a separator only, longest label first."""
    if tag in label_to_owner:
        return label_to_owner[tag]
    for label in sorted(label_to_owner, key=len, reverse=True):
        for separator in ("-", "_", "."):
            if tag.startswith(label + separator):
                return label_to_owner[label]
    return None


def entry_nodes_by_tag(graph, ids):
    """{id: [tasks tagged with it or a member]}. Over-include; under-reporting is the failure."""
    label_to_owner = {}
    for owner, names in ids.items():
        label_to_owner[owner] = owner
        for name in names:
            label_to_owner[name] = owner
    entry = {owner: set() for owner in ids}
    for task_hash, task in graph["tasks"].items():
        tag = task_tag(task)
        if not tag:
            continue
        owner = owner_of(tag, label_to_owner)
        if owner:
            entry[owner].add(task_hash)
    return {owner: sorted(nodes) for owner, nodes in entry.items()}


class SheetKind(Trigger):
    """Ids from a samplesheet column, found in task tags. A removal."""

    mode = REMOVE

    def __init__(self, column, members=None, locate=None):
        self.column, self.members = column, members
        self.locate = locate  # optional: graph -> sheet path, when the site knows where runs keep it

    def add_arguments(self, parser):
        parser.add_argument("--samplesheet", metavar="CSV",
                            help=f"the run's samplesheet; ids come from its {self.column!r} column")

    def sheet(self, args, graph=None):
        path = getattr(args, "samplesheet", None) or (self.locate(graph) if self.locate and graph else None)
        if not path:
            raise SystemExit(f"--samplesheet is required: ids of this kind come from its "
                             f"{self.column!r} column")
        return path

    def ids(self, path):
        return read_sheet(path, self.column, self.members)

    def entries(self, graph, ids):
        return entry_nodes_by_tag(graph, ids)

    def resolve(self, graph, value, args):
        entry = self.entries(graph, self.ids(self.sheet(args, graph)))
        if value is not None and value not in entry:
            raise SystemExit(f"unknown {self.column} {value!r}; known: {', '.join(sorted(entry))}")
        return entry

    def values(self, args, graph=None):
        return sorted(self.ids(self.sheet(args, graph)))


class NextflowAdapter(Adapter):
    """Set `name`, `triggers` and `load_bearing_inputs`."""
