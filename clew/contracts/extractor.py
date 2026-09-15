"""An extractor turns one engine's record of a run into the graph. The base owns what every engine shares."""

import argparse
import json
from abc import abstractmethod
from pathlib import Path

from clew.graph.graph import EXTERNAL, contract_violations

from .registry import Provider


class Extractor(Provider):
    group = "clew.extractors"
    description = ""

    @abstractmethod
    def add_arguments(self, parser):
        """This engine's source flags."""

    @abstractmethod
    def extract(self, args):
        """The graph, or None when the command already answered (a listing)."""

    def records(self, path):
        """
        Optional. If `path` is this engine's record, the runs in it:
        {"root": Path, "runs": [{"name", "id", "timestamp", "session"?, "mtime"?}]}
        `root` is where Clew keeps its sidecar. None when the path is not this engine's.
        """
        return None

    def load(self, root, run_id):
        """Optional, with records(): the graph of one run."""
        raise NotImplementedError(f"{self.name} does not load runs from a record")

    def summarize(self, graph, args):
        known = set(graph["tasks"])
        edges = graph["edges"]
        external = [e for e in edges if e["producer"] == EXTERNAL]
        dangling = [e for e in edges
                    if e["producer"] not in known and e["producer"] != EXTERNAL]
        print(f"tasks              : {len(known)}")
        print(f"input files (edges): {len(edges)}")
        print(f"  external inputs  : {len(external)}")
        print(f"  DANGLING         : {len(dangling)}")
        if dangling:
            print("\n=== DANGLING (producer not a task in this run) ===")
            for e in dangling[:10]:
                print(f"{e['consumer']}  <-  {e['producer']}  ({e['filename']})")
        self.coverage(graph)

    @staticmethod
    def coverage(graph):
        if graph.get("coverage"):
            print("\n=== what this graph does not cover ===")
            for note in graph["coverage"]:
                print(f"  - {note}")

    def parser(self):
        parser = argparse.ArgumentParser(prog=f"clew extract {self.name}",
                                         description=self.description)
        self.add_arguments(parser)
        parser.add_argument("--json-out", help="path to write the graph as JSON")
        return parser

    def run(self, argv=None):
        args = self.parser().parse_args(argv)
        graph = self.extract(args)
        if graph is None:
            return 0
        problems = contract_violations(graph)
        if problems:
            raise SystemExit(f"clew extract {self.name}: the graph breaks the contract:\n  "
                             + "\n  ".join(problems[:20]))
        self.summarize(graph, args)
        if args.json_out:
            Path(args.json_out).write_text(json.dumps(graph, indent=2))
            print(f"\nwrote {args.json_out}")
        return 0

    @classmethod
    def main(cls, argv=None):
        return cls.registered[cls.name].run(argv)
