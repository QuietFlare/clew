"""
Clew — one self-contained HTML page for a single impact plan.

    clew impact --graph g.json --trigger input:reference.dat --html plan.html

The evidence dashboard renders sealed bundles, which is right for an
audit and heavy for the question "what does this change reach, and where
does it run". This renders one plan, straight from `clew impact`, with
the same rules the dashboard follows:

    one file, no scripts, no network, prints legibly, and no generation
    timestamp, so the same plan always renders to the same bytes

It borrows the dashboard's stylesheet and helpers rather than growing a
second set. Two surfaces answering the same question two ways would
eventually disagree, and on that day nobody could say which was wrong.

WHAT IS NOT KNOWN IS SHOWN
--------------------------
Undetermined verdicts and the limits of the cost figures sit at the top,
in the same weight as everything else. A page that renders gaps in small
grey text below the fold manufactures a clean bill of health out of an
incomplete record.
"""

import argparse
import html
import json
from collections import Counter
from pathlib import Path

from clew.dashboard import coverage_panel, esc, tag
from clew.style import STYLE, masthead

UNKNOWN_TARGET = "not recorded"

COST_CAVEAT = (
    "Where a task runs is recorded fact. Anything derived from it is a "
    "relative signal for ranking one change against another, not an "
    "absolute cost or carbon figure."
)


ACTION_KIND = {
    "REGENERATE": "", "PURGE": "", "QUARANTINE": "unknown",
    "DISCLOSE": "bad", "ALREADY_GONE": "ok",
}

CONTRIBUTION_KIND = {
    "SEPARABLE": "ok", "REGENERABLE": "", "IRREDUCIBLE": "bad",
}


def possible_actions(item):
    """
    The actions a task could take once storage is known.

    `possible` maps each candidate action to the rule that would produce
    it, and is present precisely when `action` is not. Rendering it as a
    blank cell would read as "nothing to do", which is the failure the
    plan format guards against.
    """
    possible = item.get("possible")
    if isinstance(possible, dict):
        return tuple(sorted(possible))
    return (possible,) if possible else ()


def settled_or_possible(item):
    """One label for a task, whether or not its verdict is settled."""
    if item.get("action"):
        return item["action"]
    candidates = possible_actions(item)
    return " or ".join(candidates) if candidates else "?"


def counts(items):
    """The row of figures a reader takes in before anything else."""
    tiles = "".join(
        f'<div class="count{(" " + kind) if kind else ""}">'
        f"<b>{esc(value)}</b><span>{esc(label)}</span></div>"
        for label, value, kind in items)
    return f'<div class="counts">{tiles}</div>'


def headline(plan):
    """
    The picture, before any prose: how much of the run this reaches, and
    how much of that is still open.
    """
    rows = plan["plan"]
    affected, total = plan["tasks_affected"], plan["tasks_total"]
    total = total or 1
    open_count = sum(1 for i in rows if not i.get("action"))
    settled = affected - open_count
    targets = {i.get("target") for i in rows if i.get("target")}

    pct = lambda n: f"{100 * n / total:.4g}%"
    bar = (
        '<div class="bar">'
        f'<i class="hit" style="width:{pct(settled)}"></i>'
        f'<i class="open" style="width:{pct(open_count)}"></i>'
        "</div>"
        '<div class="barkey">'
        f'<span><i class="dot" style="background:hsl(var(--accent))"></i>'
        f"<b>{settled}</b> settled</span>"
        f'<span><i class="dot" style="background:hsl(var(--steel));'
        f'opacity:.45"></i><b>{open_count}</b> open</span>'
        f'<span><i class="dot" style="background:hsl(var(--muted))"></i>'
        f"<b>{total - affected}</b> untouched</span>"
        "</div>"
    )

    tiles = counts([
        ("of the run", f"{round(100 * affected / total)}%", ""),
        ("tasks reached", affected, ""),
        ("still open", open_count, "unknown" if open_count else ""),
    ] + ([("machines", len(targets), "")] if targets else []))

    return (
        "<h1>Impact of "
        f"<span class=\"mono\">{esc(plan['trigger'])}</span></h1>"
        f'<p class="lede">{affected} of {total} tasks are affected'
        f"{f', {open_count} still awaiting a storage check' if open_count else ''}"
        f"{f', across {len(targets)} machine' if targets else ''}"
        f"{'s' if len(targets) > 1 else ''}.</p>"
        f'<div class="panel">{bar}</div>'
        f"{tiles}"
    )


def by_target(plan):
    """
    Share of the affected work per machine, drawn as length so two
    machines can be compared without reading two numbers.
    """
    counted = Counter(i["target"] for i in plan["plan"] if i.get("target"))
    if not counted:
        # An engine that runs one machine per run has no host to report.
        # A row reading "not recorded" is noise, not information.
        return ""
    top = max(counted.values())
    rows = "".join(
        '<div class="spread-row">'
        f"<span class=\"mono\">{esc(target)}</span>"
        f'<span class="track"><i style="width:{100 * n / top:.4g}%"></i></span>'
        f'<span class="num">{n}</span>'
        "</div>"
        for target, n in counted.most_common())
    return ("<h2>Where the work lands</h2>"
            f'<div class="panel"><div class="spread">{rows}</div></div>')


def by_process(plan):
    """
    Rolled up by process. Nobody acts on one task at a time, and a flat
    list of 183 rows is not a thing anyone reads.

    The target column appears only when something recorded one. An
    engine that runs a whole workflow on one machine has no host to
    report, and a column of "not recorded" is noise pretending to be
    information.
    """
    shown = any(i.get("target") for i in plan["plan"])

    groups = {}
    for item in plan["plan"]:
        key = (item.get("process", "?"),
               item.get("contribution", ""),
               settled_or_possible(item),
               item.get("target", ""))
        groups.setdefault(key, []).append(item["task"])

    rows = ""
    for (process, contribution, action, target), members in sorted(
            groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        cells = [
            f'<td><b>{esc(process)}</b> <span class="hash">&times;'
            f"{len(members)}</span></td>",
            f"<td>{tag(action, ACTION_KIND.get(action, 'unknown'))}</td>",
            f"<td>{tag(contribution, CONTRIBUTION_KIND.get(contribution, ''))}"
            "</td>",
        ]
        if shown:
            cells.append(f'<td class="mono">{esc(target)}</td>')
        rows += f"<tr>{''.join(cells)}</tr>"

    heads = ["<th>Process</th>", "<th>Action</th>", "<th>Contribution</th>"]
    if shown:
        heads.append("<th>Target</th>")

    return ("<h2>What follows</h2>"
            '<div class="panel tablewrap"><table><thead><tr>'
            f"{''.join(heads)}</tr></thead>"
            f"<tbody>{rows}</tbody></table></div>")


def unsettled(plan):
    """
    Open items, named. Folded away rather than deleted: a page that
    drops them reads as a clean bill of health it has not earned.
    """
    open_items = [i for i in plan["plan"] if not i.get("action")]
    if not open_items:
        return ""
    rows = "".join(
        f'<li><span class="mono">{esc(i["task"])}</span> &mdash; could be '
        f"{tag(settled_or_possible(i), 'unknown')}</li>"
        for i in open_items)
    return (
        f"<details><summary>{len(open_items)} awaiting a storage check"
        "</summary>"
        '<div class="panel warn"><p>Unanswered, not clean. Re-run with '
        "<code>--work-root</code> where the artifacts live to settle "
        f"them.</p><ul class=\"coverage\">{rows}</ul></div></details>")


def limits(plan):
    """The caveats, one click away rather than six paragraphs up top."""
    notes = list(plan.get("caveats") or []) + [COST_CAVEAT]
    items = "".join(f"<li>{esc(n)}</li>" for n in notes)
    return ("<details><summary>What this does not settle</summary>"
            f'<div class="panel"><ul class="coverage">{items}</ul></div>'
            "</details>")


def tasks(plan):
    """Every task, for whoever needs the row rather than the summary."""
    shown = any(i.get("target") for i in plan["plan"])
    rows = ""
    for i in sorted(plan["plan"], key=lambda i: i["task"]):
        cells = [f'<td class="mono">{esc(i["task"])}</td>',
                 f"<td>{esc(i.get('process', ''))}</td>"]
        if shown:
            cells.append(f'<td class="mono">{esc(i.get("target", ""))}</td>')
        cells += [f"<td>{esc(i.get('storage') or 'not checked')}</td>",
                  f'<td class="why">{esc(i.get("reason", ""))}</td>']
        rows += f"<tr>{''.join(cells)}</tr>"

    heads = ["<th>Task</th>", "<th>Process</th>"]
    if shown:
        heads.append("<th>Target</th>")
    heads += ["<th>Storage</th>", "<th>Why</th>"]

    return (f"<details><summary>All {len(plan['plan'])} affected tasks"
            "</summary>"
            '<div class="panel tablewrap"><table><thead><tr>'
            f"{''.join(heads)}</tr></thead>"
            f"<tbody>{rows}</tbody></table></div></details>")


def render(plan):
    """The whole page, as one string."""
    body = "".join([
        headline(plan),
        by_target(plan),
        by_process(plan),
        unsettled(plan),
        limits(plan),
        tasks(plan),
        f'<p class="note" style="margin-top:2rem">Policy '
        f"{esc(plan['policy_version'])} "
        f'<span class="hash">{esc(plan["policy_hash"][:16])}</span>. '
        "This page is a view; the plan and the graph it came from are "
        "the record.</p>",
    ])

    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Clew impact</title>"
        f"<style>{STYLE}</style></head><body>"
        f'{masthead("Clew")}'
        f"<main>{body}</main></body></html>"
    )


def write(plan, path):
    """Render *plan* to *path*, or to stdout for '-'."""
    page = render(plan)
    if path == "-":
        print(page)
    else:
        Path(path).write_text(page)
        print(f"\nwrote {path}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Render an impact plan JSON as one HTML page.")
    parser.add_argument("--plan", required=True,
                        help="plan JSON from clew impact --json")
    parser.add_argument("--out", default="-", help="output path, or - ")
    args = parser.parse_args(argv)
    write(json.loads(Path(args.plan).read_text()), args.out)


if __name__ == "__main__":
    main()
