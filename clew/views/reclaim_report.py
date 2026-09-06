"""
Clew: one self-contained HTML page for a reclaim plan.

    clew reclaim --graph g.json --work-root work/ --results results/ --html plan.html

Same rules as the impact page: one file, no scripts, no network, no
generation timestamp, so the same plan renders to the same bytes. The
kept directories and their reasons sit next to the reclaimable ones, in
the same weight, because a page that shows only what can go reads as a
clean bill of health it has not earned.
"""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from clew.views.dashboard import esc, tag
from clew.views.report import counts
from clew.views.style import STYLE, masthead

PROPOSED = ("REDUNDANT", "SUPERSEDED", "FAILED", "INTERMEDIATE")
KIND = {"REDUNDANT": "ok", "SUPERSEDED": "ok", "FAILED": "ok",
        "INTERMEDIATE": "", "KEEP": "unknown", "GONE": ""}


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def headline(plan):
    """The figures, nothing else."""
    size, verdicts = plan.get("bytes", {}), plan.get("verdicts", {})
    proposed = sum(size.get(v, 0) for v in PROPOSED)
    dirs = sum(verdicts.get(v, 0) for v in PROPOSED)
    where = (f' <span class="mono">{esc(plan["target"])}</span>'
             if plan.get("target") else "")
    tiles = counts([
        ("reclaimable", human(proposed), ""),
        ("directories", f"{dirs} of {plan.get('tasks_total', 0)}", ""),
        ("kept", human(size.get("KEEP", 0)), ""),
        ("not on disk", verdicts.get("GONE", 0), ""),
    ])
    return (f"<h1>Reclaim <span class=\"mono\">{esc(plan['work_root'])}</span>"
            f"{where}</h1>{tiles}")


def by_verdict(plan):
    """Rolled up by process within each verdict."""
    groups = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for item in plan["plan"]:
        cell = groups[item["verdict"]][item.get("process", "?").split(":")[-1]]
        cell[0] += 1
        cell[1] += item.get("bytes", 0)
    out = ""
    for verdict in PROPOSED + ("KEEP",):
        if verdict not in groups:
            continue
        rows = "".join(
            f"<tr><td><b>{esc(process)}</b> <span class=\"hash\">&times;{n}</span></td>"
            f'<td class="num">{esc(human(size))}</td></tr>'
            for process, (n, size) in sorted(groups[verdict].items(),
                                             key=lambda kv: (-kv[1][1], kv[0])))
        out += (f"<h2>{tag(verdict, KIND[verdict])}</h2>"
                '<div class="panel tablewrap"><table><thead><tr>'
                "<th>Process</th><th>Bytes</th></tr></thead>"
                f"<tbody>{rows}</tbody></table></div>")
    return out


def why_kept(plan):
    """The reasons that withheld directories, most common first."""
    reasons = Counter(i["reason"] for i in plan["plan"] if i["verdict"] == "KEEP")
    if not reasons:
        return ""
    top = max(reasons.values())
    rows = "".join(
        '<div class="spread-row">'
        f'<span class="why">{esc(reason)}</span>'
        f'<span class="track"><i style="width:{100 * n / top:.4g}%"></i></span>'
        f'<span class="num">{n}</span>'
        "</div>"
        for reason, n in reasons.most_common())
    return ("<h2>What withheld the rest</h2>"
            f'<div class="panel"><div class="spread">{rows}</div></div>')


def limits(plan):
    items = "".join(f"<li>{esc(n)}</li>" for n in plan.get("caveats", []))
    return ("<details><summary>What this does not settle</summary>"
            f'<div class="panel"><ul class="coverage">{items}</ul></div>'
            "</details>")


def directories(plan):
    """Every directory, for whoever needs the row rather than the summary."""
    shown = any(i.get("target") for i in plan["plan"])
    rows = ""
    for i in sorted(plan["plan"], key=lambda i: i["task"]):
        cells = [f'<td class="mono">{esc(i["task"])}</td>',
                 f"<td>{esc(i.get('process', '').split(':')[-1])}</td>"]
        if shown:
            cells.append(f'<td class="mono">{esc(i.get("target", ""))}</td>')
        cells += [f"<td>{tag(i['verdict'], KIND.get(i['verdict'], ''))}</td>",
                  f'<td class="num">{esc(human(i.get("bytes", 0)))}</td>',
                  f'<td class="why">{esc(i.get("reason", ""))}</td>']
        rows += f"<tr>{''.join(cells)}</tr>"
    heads = ["<th>Task</th>", "<th>Process</th>"]
    if shown:
        heads.append("<th>Target</th>")
    heads += ["<th>Verdict</th>", "<th>Bytes</th>", "<th>Why</th>"]
    return (f"<details><summary>All {len(plan['plan'])} directories</summary>"
            '<div class="panel tablewrap"><table><thead><tr>'
            f"{''.join(heads)}</tr></thead><tbody>{rows}</tbody></table></div></details>")


def render(plan):
    body = "".join([
        headline(plan), by_verdict(plan), why_kept(plan), limits(plan),
        directories(plan),
    ])
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Clew reclaim</title>"
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
    parser = argparse.ArgumentParser(description="Render a reclaim plan JSON as one HTML page.")
    parser.add_argument("--plan", required=True, help="plan JSON from clew reclaim --json")
    parser.add_argument("--out", default="-", help="output path, or - ")
    args = parser.parse_args(argv)
    write(json.loads(Path(args.plan).read_text()), args.out)


if __name__ == "__main__":
    main()
