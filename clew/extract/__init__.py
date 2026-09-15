"""
Extractors. Each is an Extractor from clew.contracts, declared in pyproject.toml like any provider.

    clew extract <engine> [flags] --json-out graph.json
"""

import sys

from clew.contracts import Extractor, discover


def registry():
    return discover(Extractor)


def usage(extractors):
    width = max(len(name) for name in extractors)
    lines = ["usage: clew extract <engine> [options]", "", "engines:"]
    for name in sorted(extractors):
        lines.append(f"  {name.ljust(width)}  {extractors[name].description}")
    lines += ["", "clew extract <engine> --help shows that engine's options."]
    return "\n".join(lines)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    extractors = registry()
    if not argv or argv[0] in ("-h", "--help"):
        print(usage(extractors))
        return 0
    if argv[0] not in extractors:
        print(f"clew extract: unknown engine {argv[0]!r}\n\n{usage(extractors)}",
              file=sys.stderr)
        return 2
    return extractors[argv[0]].run(argv[1:])
