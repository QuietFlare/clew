"""
One self-contained HTML page for a single impact plan.

    clew impact --graph g.json --trigger input:reference.dat --html plan.html

Same rules as the dashboard: one file, no scripts, no network, prints
legibly, no timestamp. It borrows the dashboard's stylesheet and helpers so
the two cannot disagree. Undetermined verdicts and the limits of the cost
figures sit at the top at full weight.
"""

import argparse
import html
import json
from collections import Counter
from pathlib import Path

from clew.views.dashboard import coverage_panel, esc, tag
from clew.views.style import STYLE, masthead

UNKNOWN_TARGET = "not recorded"

COST_CAVEAT = (
    "Where a task runs is recorded fact. Anything derived from it is a "
    "relative signal for ranking one change against another, not an "
    "absolute cost or carbon figure."
)


# Every action the policy can return. NOTIFY_ONLY and DESTROY are settled
# verdicts with serious consequences, so they take the serious class, not
# the "open" one, which is reserved for verdicts the plan could not reach.
ACTION_KIND = {
    "REGENERATE": "", "PURGE": "", "QUARANTINE": "unknown",
    "NOTIFY_ONLY": "bad", "DESTROY": "bad", "ALREADY_GONE": "ok",
}

CONTRIBUTION_KIND = {
    "SEPARABLE": "ok", "REGENERABLE": "", "IRREDUCIBLE": "bad",
}


def possible_actions(item):
    """
    The actions a task could take once storage is known. `possible` is
    present exactly when `action` is not; a blank cell would read as nothing
    to do.
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
    ] + ([("machines", len(targets), "")] if targets else [])
      + ([("to recompute", seconds(rerun_seconds(plan) or 0), "")]
         if any(duration_of(i) is not None for i in rows) else []))

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


def duration_of(item):
    """Seconds the engine recorded for the original task, or None."""
    return (item.get("metrics") or {}).get("duration_s")


def recorded_seconds(items):
    """Sum of what was recorded; tasks without a figure add nothing."""
    return sum(duration_of(i) or 0 for i in items)


def rerun_seconds(plan):
    """
    Recorded time of the tasks the plan re-runs, or None when there are
    none or the engine recorded nothing. A purge edits a file and a
    quarantine locks one; only REGENERATE spends compute again.
    """
    regen = ((plan.get("cost") or {}).get("by_action") or {}).get("REGENERATE")
    if not regen:
        return None
    return (regen.get("metrics") or {}).get("duration_s")


def seconds(n):
    """39.8 s, 4.2 min, 1.3 h: the unit a reader would reach for."""
    if n >= 3600:
        return f"{n / 3600:.1f} h"
    if n >= 120:
        return f"{n / 60:.1f} min"
    return f"{n:.1f} s"


def by_process(plan):
    """
    Rolled up by process, since nobody acts on 183 rows one at a time. The
    target and time columns appear only when an engine recorded them.
    """
    shown = any(i.get("target") for i in plan["plan"])
    timed = any(duration_of(i) is not None for i in plan["plan"])

    groups = {}
    for item in plan["plan"]:
        key = (item.get("process", "?"),
               item.get("contribution", ""),
               settled_or_possible(item),
               item.get("target", ""))
        groups.setdefault(key, []).append(item)

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
        if timed:
            missing = sum(1 for m in members if duration_of(m) is None)
            cell = seconds(recorded_seconds(members))
            if missing:
                cell += f' <span class="hash">+{missing} unrecorded</span>'
            cells.append(f'<td class="num">{cell}</td>')
        rows += f"<tr>{''.join(cells)}</tr>"

    heads = ["<th>Process</th>", "<th>Action</th>", "<th>Contribution</th>"]
    if shown:
        heads.append("<th>Target</th>")
    if timed:
        heads.append("<th>Recorded time</th>")

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


def untouched(plan):
    """
    The tasks the trigger never reached, by name. A count alone leaves the
    reader guessing which branch was spared.
    """
    spared = plan.get("untouched") or []
    if not spared:
        return ""
    names = ", ".join(esc(t.get("process") or t["task"]) for t in spared)
    return ('<h2>Untouched</h2><div class="panel"><p>'
            f"{len(spared)} task{'s' if len(spared) != 1 else ''} not reached "
            f"by this trigger: <b>{names}</b>.</p></div>")


def limits(plan):
    """The caveats, one click away rather than six paragraphs up top."""
    notes = list(plan.get("caveats") or []) + [COST_CAVEAT]
    items = "".join(f"<li>{esc(n)}</li>" for n in notes)
    return ("<details><summary>Limits of this answer</summary>"
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
                  f'<td class="why">{esc(i.get("evidence", i.get("reason", "")))}</td>']
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
        untouched(plan),
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
