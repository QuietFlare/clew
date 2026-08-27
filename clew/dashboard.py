"""
Clew — a self-contained HTML view over an evidence store.

    clew dashboard --bundles /path/to/bundles --out evidence.html

One file, no server, no network, no scripts. An auditor opens it from a USB
stick on a machine with no access to anything, and it still works. Printing
it produces something usable, because auditors print things.

THIS PAGE IS NOT THE RECORD
---------------------------
The bundles are. This is a view generated from them, and it says so at the
top, because a rendered summary is exactly the kind of artifact that gets
detached from its source and quoted years later. Every panel carries the
bundle hash it was drawn from so the page can always be traced back to
something checkable, and the integrity panel reports the verifier's own
output rather than a rendering of it.

It shares core/query.py with the MCP server on purpose. Two surfaces
answering the same questions two different ways would eventually disagree,
and on the day they did nobody could say which was wrong.

WHAT IS NOT KNOWN IS GIVEN THE SAME WEIGHT AS WHAT IS
-----------------------------------------------------
A compliance dashboard that renders gaps in small grey text below the fold is
worse than no dashboard: it manufactures the impression of a clean bill of
health out of an incomplete record. So coverage limits, undetermined verdicts
and subjects the log has never heard of appear near the top, in the same
visual weight as everything else, and the summary counts them explicitly.

NO CLOCK
--------
The page carries no generation timestamp, so regenerating it from unchanged
bundles produces an identical file. Two auditors comparing pages should be
comparing evidence, not diffing dates. The bundles' own hashes and the log
heads they anchor to are the identity of what is shown.
"""

import argparse
import html
import json
import sys
from pathlib import Path


from clew.core import bundlestore
from clew.core import policy as policy_module
from clew.core import query

STYLE = """
:root {
  --ink: #1a1d21; --muted: #5b636c; --line: #d7dbe0; --bg: #fbfcfd;
  --panel: #ffffff; --warn-bg: #fff6e8; --warn-line: #d99a2b;
  --stop-bg: #fdeceb; --stop-line: #c0392b; --ok: #1e7a48;
}
* { box-sizing: border-box; }
body { margin: 0; padding: 2rem 1.5rem 4rem; background: var(--bg);
  color: var(--ink); font: 15px/1.55 -apple-system, BlinkMacSystemFont,
  "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
main { max-width: 60rem; margin: 0 auto; }
h1 { font-size: 1.5rem; margin: 0 0 .25rem; }
h2 { font-size: 1.1rem; margin: 2.5rem 0 .75rem; padding-bottom: .35rem;
  border-bottom: 2px solid var(--ink); }
h3 { font-size: .95rem; margin: 1.5rem 0 .5rem; }
p, li { margin: .4rem 0; }
.lede { color: var(--muted); margin-bottom: 1.5rem; }
.panel { background: var(--panel); border: 1px solid var(--line);
  border-radius: 6px; padding: 1rem 1.15rem; margin: .75rem 0; }
.panel.warn { background: var(--warn-bg); border-left: 4px solid var(--warn-line); }
.panel.stop { background: var(--stop-bg); border-left: 4px solid var(--stop-line); }
table { border-collapse: collapse; width: 100%; margin: .5rem 0 1rem; }
th, td { text-align: left; padding: .4rem .6rem; border-bottom: 1px solid var(--line);
  vertical-align: top; }
th { font-size: .78rem; text-transform: uppercase; letter-spacing: .04em;
  color: var(--muted); font-weight: 600; }
code, .hash { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: .85em; }
.hash { color: var(--muted); word-break: break-all; }
.tag { display: inline-block; padding: .1rem .45rem; border-radius: 3px;
  font-size: .75rem; font-weight: 600; letter-spacing: .02em;
  border: 1px solid var(--line); }
.tag.ok { color: var(--ok); border-color: var(--ok); }
.tag.bad { color: var(--stop-line); border-color: var(--stop-line); }
.tag.unknown { color: var(--warn-line); border-color: var(--warn-line); }
.counts { display: flex; flex-wrap: wrap; gap: .5rem; margin: .5rem 0 0; }
.count { border: 1px solid var(--line); border-radius: 5px; padding: .5rem .8rem;
  background: var(--panel); min-width: 7rem; }
.count b { display: block; font-size: 1.4rem; line-height: 1.1; }
.count span { font-size: .75rem; color: var(--muted);
  text-transform: uppercase; letter-spacing: .04em; }
.count.unknown { border-color: var(--warn-line); background: var(--warn-bg); }
ul.coverage { margin: .35rem 0 0; padding-left: 1.1rem; }
ul.coverage li { color: var(--ink); }
.chain { font-family: ui-monospace, monospace; font-size: .8rem;
  color: var(--muted); word-break: break-all; }
@media print {
  body { background: #fff; padding: 0; font-size: 11pt; }
  .panel { break-inside: avoid; }
  h2 { break-after: avoid; }
}
"""


def esc(value):
    return html.escape("" if value is None else str(value))


def tag(text, kind=""):
    return f'<span class="tag {kind}">{esc(text)}</span>'


def coverage_panel(notes, heading="What this does not cover"):
    if not notes:
        return ""
    items = "".join(f"<li>{esc(note)}</li>" for note in notes)
    return (f'<div class="panel warn"><strong>{esc(heading)}</strong>'
            f'<ul class="coverage">{items}</ul></div>')


# ------------------------------------------------------------------ sections

def section_header(store, root):
    bundles, entries, conflicts = store
    parts = [
        "<h1>Clew evidence</h1>",
        '<p class="lede">A view generated from the bundles below. '
        '<strong>This page is not the record</strong> — the bundles are, and '
        'each panel names the bundle hash it was drawn from so anything here '
        'can be traced back and checked independently with '
        '<code>clew evidence verify</code>.</p>',
        f"<p>{len(bundles)} bundle(s) from <code>{esc(root)}</code>, "
        f"{len(entries)} log entries.</p>",
    ]
    if conflicts:
        seqs = ", ".join(str(c["seq"]) for c in conflicts[:8])
        parts.append(
            '<div class="panel stop"><strong>These bundles were sealed from '
            'different logs.</strong><p>Sequence numbers ' + esc(seqs) +
            ' carry different entries in different bundles. The combined '
            'history below interleaves two unrelated records and must not be '
            'read as one timeline. Resolve this before relying on anything '
            'drawn from the log.</p></div>')
    return "\n".join(parts)


def section_integrity(store):
    bundles, _, _ = store
    rows = []
    for bundle in bundles:
        result = bundlestore.check_integrity(bundle)
        checks = result["result"]["checks"]
        cells = []
        for check in checks:
            kind = "ok" if check["ok"] else ("bad" if check["ok"] is False
                                             else "unknown")
            label = check["check"] if check["ok"] else (
                f"{check['check']} FAILED" if check["ok"] is False
                else f"{check['check']} —")
            cells.append(f'{tag(label, kind)} <span class="hash">'
                         f'{esc(check["detail"])}</span>')
        rows.append(
            f"<tr><td><code>{esc(bundle['name'])}</code><br>"
            f'<span class="hash">{esc(bundle["hash"])}</span></td>'
            f"<td>{'<br>'.join(cells)}</td></tr>")

    return ("<h2>Integrity</h2>"
            "<p>The deterministic verifier's own output, not a rendering of "
            "it. <code>replay</code> means every verdict was recomputed from "
            "the sealed facts and matched.</p>"
            "<table><tr><th>Bundle</th><th>Checks</th></tr>"
            + "".join(rows) + "</table>"
            + coverage_panel([
                "Intact means the sealed record is internally consistent and "
                "every verdict re-derives. It says nothing about whether the "
                "facts sealed into it were true.",
                "Signatures are not checked here. That needs an "
                "allowed_signers file this reader trusts: "
                "clew evidence verify --allowed-signers.",
            ]))


def section_unknowns(store):
    """Deliberately near the top. Gaps get the same weight as findings."""
    bundles, _, _ = store
    undetermined = unknown_subjects = 0
    notes = []
    for bundle in bundles:
        plan = bundlestore.plan_of(bundle)
        if plan:
            missing = [i for i in plan.get("plan", []) if not i.get("action")]
            undetermined += len(missing)
            if missing:
                notes.append(
                    f"{bundle['name']}: {len(missing)} of "
                    f"{len(plan.get('plan', []))} items have no verdict — "
                    "storage was not verified and the answer depends on it.")
        gate = bundle["documents"].get("gate.json")
        if gate:
            count = gate.get("counts", {}).get("UNKNOWN", 0)
            unknown_subjects += count
            if count:
                notes.append(
                    f"{bundle['name']}: {count} subjects were not found in "
                    "the log. Commonly an identifier mismatch, not a clean "
                    "result.")
        notes.extend(f"{bundle['name']}: {note}"
                     for note in bundle["manifest"].get("coverage", []))

    counts = (
        f'<div class="counts">'
        f'<div class="count{" unknown" if undetermined else ""}">'
        f"<b>{undetermined}</b><span>verdicts withheld</span></div>"
        f'<div class="count{" unknown" if unknown_subjects else ""}">'
        f"<b>{unknown_subjects}</b><span>subjects unknown</span></div>"
        f"</div>")

    return ("<h2>What is not known</h2>"
            "<p>Placed here rather than in a footnote. An unanswered item is "
            "<strong>not a clean one</strong>, and a record that renders its "
            "gaps quietly manufactures the impression of a clean bill of "
            "health.</p>"
            + counts
            + coverage_panel(sorted(set(notes)), "Stated limits of this record"))


def section_log(store):
    _, entries, _ = store
    if not entries:
        return ("<h2>The log</h2>"
                + coverage_panel(["No log entries are sealed into these "
                                  "bundles, so nothing here witnesses a log "
                                  "head or a chain."]))
    rows = []
    for entry in entries:
        body = query.body_of(entry)
        summary = ", ".join(f"{k}={v}" for k, v in sorted(body.items())
                            if not isinstance(v, (dict, list)))
        rows.append(
            f"<tr><td>{entry['seq']}</td>"
            f"<td>{esc(entry['effective_from'][:19])}</td>"
            f"<td>{esc(entry['recorded_at'][:19])}</td>"
            f"<td><code>{esc(entry['event_type'])}</code></td>"
            f"<td>{esc(entry['subject'])}</td>"
            f"<td>{esc(entry['actor'])}</td>"
            f'<td><span class="hash">{esc(entry["hash"][:16])}</span>'
            f'<br><span class="hash">{esc(summary[:80])}</span></td></tr>')

    return ("<h2>The log</h2>"
            "<p>Two clocks. <strong>Effective</strong> is when a decision was "
            "made in the world; <strong>recorded</strong> is when it reached "
            "the log. Where they differ, both matter — a fact effective in "
            "March and recorded in August means work done in between was done "
            "in good faith and still has to be accounted for.</p>"
            "<table><tr><th>Seq</th><th>Effective</th><th>Recorded</th>"
            "<th>Type</th><th>Subject</th><th>Asserted by</th>"
            "<th>Entry</th></tr>" + "".join(rows) + "</table>"
            + coverage_panel([
                "These are the facts someone recorded. Facts never recorded "
                "cannot appear here, and their absence is not evidence.",
                "Event types are the recording organisation's vocabulary. "
                "Clew assigns them no meaning.",
            ]))


def section_policy(store):
    _, entries, conflicts = store
    result = bundlestore.with_conflicts(query.policy_history(entries),
                                        conflicts)
    adoptions = result["result"]["adoptions"]
    if not adoptions:
        return "<h2>Policy</h2>" + coverage_panel(result["coverage"])

    rows = "".join(
        f"<tr><td><code>{esc(a['version'])}</code></td>"
        f"<td>{esc(a['effective_from'][:19])}</td>"
        f"<td>{esc(a['actor'])}</td>"
        f'<td><span class="hash">{esc(a["policy_hash"])}</span></td></tr>'
        for a in adoptions)
    return ("<h2>Policy</h2>"
            "<p>Which remediation table was in force, and from when. The hash "
            "is what makes the version label checkable — two parties can prove "
            "they were reading the same table.</p>"
            "<table><tr><th>Version</th><th>Effective from</th>"
            "<th>Adopted by</th><th>sha256</th></tr>" + rows + "</table>"
            + coverage_panel(result["coverage"]))


def section_plan(bundle, store):
    plan = bundlestore.plan_of(bundle)
    policy_document = bundlestore.policy_of(bundle)
    summary = query.plan_summary(plan)["result"]

    counts = "".join(
        f'<div class="count{" unknown" if action == policy_module.UNDETERMINED else ""}">'
        f"<b>{n}</b><span>{esc(action)}</span></div>"
        for action, n in summary["actions"].items())

    rows = []
    for item in plan.get("plan", []):
        detail = query.verdict(plan, policy_document, item["task"])["result"]
        action = detail["action"] or policy_module.UNDETERMINED
        kind = "unknown" if not detail["action"] else (
            "bad" if action in ("DESTROY", "QUARANTINE") else "")
        chain = " → ".join(detail.get("evidence_path") or [])
        because = detail["because"] if detail["action"] else (
            "no verdict: storage was not verified and the answer depends on "
            "it. Possible: " + ", ".join(sorted(detail.get("possible") or {})))
        rows.append(
            f"<tr><td><code>{esc(item['task'])}</code><br>"
            f"{esc(detail['process'])}</td>"
            f"<td>{tag(action, kind)}"
            + (f"<br><code>{esc(detail['rule'])}</code>" if detail["rule"] else "")
            + f'</td><td>{esc(because)}'
            + (f'<br><span class="chain">{esc(chain)}</span>' if chain else "")
            + "</td></tr>")

    return (f"<h3>{esc(plan.get('trigger'))}</h3>"
            f'<p><span class="hash">bundle {esc(bundle["hash"])}</span><br>'
            f"policy <code>{esc(plan.get('policy_version'))}</code> "
            f'<span class="hash">{esc(plan.get("policy_hash"))}</span></p>'
            f'<div class="counts">{counts}</div>'
            "<table><tr><th>Task</th><th>Verdict</th>"
            "<th>Why, and the chain that reaches it</th></tr>"
            + "".join(rows) + "</table>")


def section_gate(bundle):
    result = bundle["documents"]["gate.json"]
    counts = "".join(
        f'<div class="count{" unknown" if status == "UNKNOWN" else ""}">'
        f"<b>{n}</b><span>{esc(status)}</span></div>"
        for status, n in sorted(result.get("counts", {}).items()))
    rows = "".join(
        f"<tr><td>{esc(subject)}</td>"
        f"<td>{tag(detail['status'], {'BLOCKED': 'bad', 'CLEARED': 'ok'}.get(detail['status'], 'unknown'))}</td>"
        f"<td>{esc(detail['reason'])}"
        + (f'<br><span class="hash">log seq {detail["fact"]["seq"]}, '
           f'{esc(detail["fact"]["hash"][:16])}</span>' if detail["fact"] else "")
        + "</td></tr>"
        for subject, detail in sorted(result.get("subjects", {}).items()))

    verdict = ("PASS" if result.get("passed") else "STOP")
    return (f"<h3>Gate — {esc(result.get('samplesheet'))} "
            f"{tag(verdict, 'ok' if result.get('passed') else 'bad')}</h3>"
            f'<p><span class="hash">bundle {esc(bundle["hash"])}</span><br>'
            f"as of {esc(result.get('as_of') or 'all facts in effect')}, "
            f"blocking on <code>"
            f"{esc(', '.join(result.get('blocking_types', [])))}</code></p>"
            f'<div class="counts">{counts}</div>'
            "<table><tr><th>Subject</th><th>Status</th>"
            "<th>On what basis</th></tr>" + rows + "</table>")


def section_bundles(store):
    bundles, _, _ = store
    parts = ["<h2>Findings</h2>"]
    for bundle in bundles:
        if bundlestore.plan_of(bundle):
            parts.append(section_plan(bundle, store))
        elif "gate.json" in bundle["documents"]:
            parts.append(section_gate(bundle))
    return "\n".join(parts)


def render(store, root):
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Clew evidence</title>
<style>{STYLE}</style></head>
<body><main>
{section_header(store, root)}
{section_unknowns(store)}
{section_integrity(store)}
{section_bundles(store)}
{section_log(store)}
{section_policy(store)}
<h2>What Clew claims</h2>
<div class="panel">
<p>Three things, all checkable: the log is append-only and unmodified, the
computation is deterministic and reproducible, and the result follows from the
inputs. Anyone can re-run it and get the same answer.</p>
<p><strong>Clew claims nothing about whether the inputs were true or the
policy was correct.</strong> Those belong to whoever has the domain authority
to defend them. This is a system of record, not an attester — it does not
decide whether a use was compliant, it makes it impossible to lose the record
of what was decided, on what basis, and when.</p>
<p>It does not prove physical destruction. No cryptography reaches a freezer.
The claim is proof of non-use, not proof of destruction.</p>
</div>
</main></body></html>
"""


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate a self-contained HTML view of an evidence store.")
    parser.add_argument("--bundles", required=True, metavar="DIR")
    parser.add_argument("--out", required=True, metavar="FILE")
    args = parser.parse_args(argv)

    store = bundlestore.load_store(args.bundles)
    if not store[0]:
        raise SystemExit(f"no readable bundle found in {args.bundles}")
    Path(args.out).write_text(render(store, args.bundles))
    print(f"wrote {args.out}  ({len(store[0])} bundles, "
          f"{len(store[1])} log entries)")
    if store[2]:
        print(f"WARNING: {len(store[2])} sequence conflicts — these bundles "
              f"were sealed from different logs; the page says so.")


if __name__ == "__main__":
    main()
