"""
A Snakemake job is named by rule and first output path, "trim (trimmed/sample_1.fq)".
PathKind finds each id in that path, bounded by separators so sample_1 stays out
of sample_10.fq. A merge named after two ids enters the graph for both.
"""

import csv
import re

from clew.contracts import REMOVE, Adapter, Trigger

SEPARATORS = "-_./"
TAG_PATTERN = re.compile(r"\(([^()]+)\)\s*$")


def read_ids(path, column):
    """{id: []} from one CSV column."""
    ids = {}
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            value = (row.get(column) or "").strip()
            if value:
                ids.setdefault(value, [])
    return ids


def mentions(path, value):
    """Whether `value` appears in `path` bounded by separators or the ends."""
    pattern = (rf"(?:^|[{re.escape(SEPARATORS)}])"
               rf"{re.escape(value)}"
               rf"(?:$|[{re.escape(SEPARATORS)}])")
    return re.search(pattern, path) is not None


class PathKind(Trigger):
    """Ids from a samplesheet column, found in job output paths. A removal."""

    mode = REMOVE

    def __init__(self, column):
        self.column = column

    def add_arguments(self, parser):
        parser.add_argument("--samplesheet", metavar="CSV",
                            help=f"ids come from its {self.column!r} column")

    def ids(self, path):
        return read_ids(path, self.column)

    def entries(self, graph, ids):
        # Longest id first; `mentions` stops a short id matching inside a long one.
        entry = {value: set() for value in ids}
        ordered = sorted(ids, key=lambda s: (-len(s), s))
        for task_hash, task in graph["tasks"].items():
            match = TAG_PATTERN.search(task.get("name", ""))
            if not match:
                continue
            tag = match.group(1).strip()
            for value in ordered:
                if mentions(tag, value):
                    entry[value].add(task_hash)
        return {value: sorted(nodes) for value, nodes in entry.items()}

    def sheet(self, args):
        path = getattr(args, "samplesheet", None)
        if not path:
            raise SystemExit(f"--samplesheet is required: ids of this kind come from its "
                             f"{self.column!r} column")
        return path

    def resolve(self, graph, value, args):
        entry = self.entries(graph, self.ids(self.sheet(args)))
        if value is not None and value not in entry:
            raise SystemExit(f"unknown {self.column} {value!r}; known: {', '.join(sorted(entry))}")
        return entry

    def values(self, args, graph=None):
        return sorted(self.ids(self.sheet(args)))


class Snakemake(Adapter):
    name = "snakemake"
    triggers = {"sample": PathKind(column="sample")}
