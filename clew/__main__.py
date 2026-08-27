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
    "impact": ("clew.impact",
               "what a withdrawal, defect or update reaches, and what to do"),
    "gate": ("clew.gate",
             "block a run whose inputs the log says are not usable"),
    "log": ("clew.logbook",
            "the append-only event log: init, append, verify"),
    "rulebook": ("clew.rulebook",
                 "the versioned remediation policy: show, diff, register"),
    "evidence": ("clew.evidence",
                 "seal, verify, witness and sign evidence bundles"),
    "dashboard": ("clew.dashboard",
                  "one self-contained HTML page over sealed bundles"),
    "mcp": ("clew.mcp_server",
            "read-only MCP server over sealed bundles, for auditors"),
    "stitch": ("clew.stitch_graphs",
               "join run graphs where one run consumed another's outputs"),
    "extract-store": ("clew.extract_from_lineage_store",
                      "build a graph from the engine's native lineage store"),
    "extract-crate": ("clew.extract_from_rocrate",
                      "build a graph from a Workflow Run RO-Crate"),
    "extract-work": ("clew.extract_lineage",
                     "build a graph from work/ symlinks, any engine version"),
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
