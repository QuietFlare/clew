"""
The clew command.

Every subcommand is its own module with its own --help. This file only
routes to them, and imports the chosen one lazily so that, for example,
running the stdlib-only demo never touches the database driver.
"""

import importlib
import sys

COMMANDS = {
    "demo": ("clew.demo",
             "the shipped sample run: three triggers, one engine"),
    "impact": ("clew.questions.impact",
               "what a removal, defect or update reaches, and what to do"),
    "gate": ("clew.questions.gate",
             "block a run whose inputs the log says are not usable"),
    "reclaim": ("clew.questions.reclaim",
                "which work directories are safe to delete, with proof"),
    "drift": ("clew.questions.drift",
              "where two runs of the same workflow part ways, and why"),
    "log": ("clew.ledger.logbook",
            "the append-only event log: init, append, verify"),
    "rulebook": ("clew.ledger.rulebook",
                 "the versioned remediation policy: show, diff, register"),
    "evidence": ("clew.ledger.evidence",
                 "seal, verify, witness and sign evidence bundles"),
    "dashboard": ("clew.views.dashboard",
                  "one self-contained HTML page over sealed bundles"),
    "mcp": ("clew.views.mcp_server",
            "read-only MCP server over sealed bundles, for auditors"),
    "extract": ("clew.extract",
                "build a graph from an engine's record: clew extract <engine>"),
    "providers": ("clew.providers",
                  "every domain and extractor installed, and the package each came from"),
    "stitch": ("clew.extract.stitch",
               "join run graphs where one run consumed another's outputs"),
    "digest": ("clew.extract.digest",
               "hash a run's files once, for graphs without content digests"),
}

# The names extractors had before `clew extract <engine>`. Still accepted.
ALIASES = {
    "extract-store": "nextflow",
    "extract-work": "nextflow-work",
    "extract-crate": "ro-crate",
    "extract-horus": "horus",
    "extract-dnanexus": "dnanexus",
    "extract-latch": "latch",
    "extract-cromwell": "cromwell",
    "extract-snakemake": "snakemake",
}


def usage():
    lines = [f"usage: clew <command> [options]", "",
             "commands:"]
    width = max(len(name) for name in COMMANDS)
    for name, (_, help_text) in COMMANDS.items():
        lines.append(f"  {name.ljust(width)}  {help_text}")
    lines += ["", "clew <command> --help shows that command's options."]
    return "\n".join(lines)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("-V", "--version"):
        from importlib.metadata import version
        print(f"clew {version('clew-lineage')}")
        return 0
    if not argv or argv[0] in ("-h", "--help"):
        print(usage())
        return 0
    if argv[0] in ALIASES:
        argv = ["extract", ALIASES[argv[0]]] + argv[1:]
    if argv[0] not in COMMANDS:
        print(f"clew: unknown command {argv[0]!r}\n\n{usage()}",
              file=sys.stderr)
        return 2
    module = importlib.import_module(COMMANDS[argv[0]][0])
    return module.main(argv[1:])


if __name__ == "__main__":
    sys.exit(main())
