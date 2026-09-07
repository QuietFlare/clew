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
               "what a withdrawal, defect or update reaches, and what to do"),
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
    "stitch": ("clew.extract.stitch",
               "join run graphs where one run consumed another's outputs"),
    "digest": ("clew.extract.digest",
               "hash a run's files once, for graphs without content digests"),
    "extract-store": ("clew.extract.nextflow_store",
                      "build a graph from the engine's native lineage store"),
    "extract-crate": ("clew.extract.rocrate",
                      "build a graph from a Workflow Run RO-Crate"),
    "extract-work": ("clew.extract.nextflow_work",
                     "build a graph from work/ symlinks, any engine version"),
    "extract-horus": ("clew.extract.horus",
                      "build a graph from a horus-lineage run directory"),
    "extract-dnanexus": ("clew.extract.dnanexus",
                         "build a graph from a DNAnexus analysis"),
    "extract-latch": ("clew.extract.latch",
                      "build a graph from a Latch execution"),
    "extract-cromwell": ("clew.extract.cromwell",
                         "build a graph from Cromwell workflow metadata"),
    "extract-snakemake": ("clew.extract.snakemake",
                          "build a graph from Snakemake's metadata store"),
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
        from clew import __version__
        print(f"clew {__version__}")
        return 0
    if not argv or argv[0] in ("-h", "--help"):
        print(usage())
        return 0
    if argv[0] not in COMMANDS:
        print(f"clew: unknown command {argv[0]!r}\n\n{usage()}",
              file=sys.stderr)
        return 2
    module = importlib.import_module(COMMANDS[argv[0]][0])
    return module.main(argv[1:])


if __name__ == "__main__":
    sys.exit(main())
