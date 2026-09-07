"""
Clew: one self-contained HTML page for a drift plan.

    clew drift --before a.json --after b.json --html drift.html

Same rules as the other pages: one file, no scripts, no network, no
generation timestamp. The roots come first, with their causes, because
that is the finding; the reproduced tasks are the proof and fold away.
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

from clew.views.dashboard import esc, tag
from clew.views.report import counts
from clew.views.style import STYLE, masthead

KIND = {"DRIFTED": "bad", "DOWNSTREAM": "unknown", "UNVERIFIED": "unknown",
        "REPRODUCED": "ok", "ADDED": "", "REMOVED": ""}


def headline(plan):
    v = plan.get("verdicts", {})
    tiles = counts([
        ("drift roots", v.get("DRIFTED", 0), "bad" if v.get("DRIFTED") else ""),
        ("downstream", v.get("DOWNSTREAM", 0), ""),
        ("reproduced", v.get("REPRODUCED", 0), ""),
        ("unverified", v.get("UNVERIFIED", 0), "unknown" if v.get("UNVERIFIED") else ""),
    ])
    return (f"<h1>Drift <span class=\"mono\">{esc(Path(plan['before']).name)}</span> "
            f"&rarr; <span class=\"mono\">{esc(Path(plan['after']).name)}</span></h1>{tiles}")


def roots(plan):
    rows = [i for i in plan["plan"] if i["verdict"] == "DRIFTED"]
    if not rows:
        return ""
    body = "".join(
        f"<tr><td class=\"mono\">{esc(i['task'])}</td>"
        f"<td><b>{esc(i['process'].split(':')[-1])}</b></td>"
        f"<td class=\"why\">{esc(i['reason'])}</td></tr>"
        for i in rows)
    return ("<h2>Where the runs part ways</h2>"
            '<div class="panel tablewrap"><table><thead><tr>'
            "<th>Task</th><th>Process</th><th>Cause</th></tr></thead>"
            f"<tbody>{body}</tbody></table></div>")


def by_verdict(plan):
    groups = defaultdict(lambda: defaultdict(int))
    for i in plan["plan"]:
        groups[i["verdict"]][i["process"].split(":")[-1]] += 1
    out = ""
    for verdict in ("DOWNSTREAM", "UNVERIFIED", "ADDED", "REMOVED", "REPRODUCED"):
        if verdict not in groups:
            continue
        rows = "".join(
            f"<tr><td><b>{esc(p)}</b> <span class=\"hash\">&times;{n}</span></td></tr>"
            for p, n in sorted(groups[verdict].items(), key=lambda kv: (-kv[1], kv[0])))
        section = (f"<h2>{tag(verdict, KIND[verdict])}</h2>"
                   '<div class="panel tablewrap"><table><tbody>'
                   f"{rows}</tbody></table></div>")
        if verdict == "REPRODUCED":
            section = (f"<details><summary>{sum(groups[verdict].values())} reproduced"
                       f"</summary>{section}</details>")
        out += section
    return out


def limits(plan):
    items = "".join(f"<li>{esc(n)}</li>" for n in plan.get("caveats", []))
    return ("<details><summary>What this does not settle</summary>"
            f'<div class="panel"><ul class="coverage">{items}</ul></div></details>')


def tasks(plan):
    rows = "".join(
        f"<tr><td class=\"mono\">{esc(i['task'])}</td>"
        f"<td>{esc(i['process'].split(':')[-1])}</td>"
        f"<td>{tag(i['verdict'], KIND.get(i['verdict'], ''))}</td>"
        f"<td class=\"why\">{esc(i['reason'])}</td></tr>"
        for i in sorted(plan["plan"], key=lambda i: i["task"]))
    return (f"<details><summary>All {len(plan['plan'])} tasks</summary>"
            '<div class="panel tablewrap"><table><thead><tr>'
            "<th>Task</th><th>Process</th><th>Verdict</th><th>Why</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div></details>")


def render(plan):
    body = "".join([headline(plan), roots(plan), by_verdict(plan), limits(plan), tasks(plan)])
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Clew drift</title>"
        f"<style>{STYLE}</style></head><body>"
        f'{masthead("Clew")}<main>{body}</main></body></html>'
    )


def write(plan, path):
    page = render(plan)
    if path == "-":
        print(page)
    else:
        Path(path).write_text(page)
        print(f"\nwrote {path}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Render a drift plan JSON as one HTML page.")
    parser.add_argument("--plan", required=True, help="plan JSON from clew drift --json")
    parser.add_argument("--out", default="-", help="output path, or - ")
    args = parser.parse_args(argv)
    write(json.loads(Path(args.plan).read_text()), args.out)


if __name__ == "__main__":
    main()
