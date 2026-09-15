"""One self-contained HTML page for a reclaim plan."""

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
    """Four figures and what they rest on. The target is named when there is one."""
    size, verdicts = plan.get("bytes", {}), plan.get("verdicts", {})
    proposed = sum(size.get(v, 0) for v in PROPOSED)
    dirs = sum(verdicts.get(v, 0) for v in PROPOSED)
    total = plan.get("tasks_total", 0)
    tiles = counts([
        ("reclaimable size", human(proposed), ""),
        ("removable directories", f"{dirs} of {total}", ""),
        ("retained size", human(size.get("KEEP", 0)), ""),
        ("already gone", str(verdicts.get("GONE", 0)), ""),
    ])
    rest = [c for c in plan.get("caveats", []) if c.startswith("Verdicts rest on")]
    note = f'<p class="note">{tag("Note", "")} {esc(rest[0])}</p>' if rest else ""
    where = (f' <span class="mono">{esc(plan["target"])}</span>' if plan.get("target") else "")
    return f'<h1>Storage reclaim{where}</h1>{tiles}{note}'


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
    """
    The causes that withheld directories, most common first, each with the
    processes it held. A run has three or four causes; the file names that
    make each directory's reason its own are in the table below, not here.
    """
    kept = [i for i in plan["plan"] if i["verdict"] == "KEEP"]
    if not kept:
        return ""
    causes = Counter(i.get("cause") or i["reason"] for i in kept)
    rows = ""
    for cause, n in causes.most_common():
        processes = Counter(i.get("process", "?").split(":")[-1] for i in kept
                            if (i.get("cause") or i["reason"]) == cause)
        held = ", ".join(f"{count} {esc(name)}" for name, count in
                         sorted(processes.items(), key=lambda kv: (-kv[1], kv[0])))
        rows += ('<div class="spread-row">'
                 f'<span class="why">{esc(cause)}<br><span class="hash">{held}</span></span>'
                 f'<span class="track"><i style="width:{100 * n / len(kept):.4g}%"></i></span>'
                 f'<span class="num">{n}</span>'
                 "</div>")
    return ("<details><summary>What withheld the rest</summary>"
            f'<div class="panel"><div class="spread">{rows}</div></div></details>')


def limits(plan):
    items = "".join(f"<li>{esc(n)}</li>" for n in plan.get("caveats", [])
                    if not n.startswith("Verdicts rest on"))
    return ("<details><summary>Limits of this answer</summary>"
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
        "<title>Clew storage reclaim</title>"
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
