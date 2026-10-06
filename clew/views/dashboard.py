"""
One self-contained HTML page over an evidence store.

    clew dashboard --bundles /path/to/bundles --out evidence.html

No server, no network, no scripts, prints legibly. The page is a view and
the bundles are the record: every panel carries the hash of the bundle it
came from. It shares query.py with the MCP server so the two cannot
disagree.

The page leads with what happened: one row per incident, with the sentence
that arrived, what it became, who decided and whether the bundle verifies.
Limits come next, each stated once with how many bundles it applies to,
because an unanswered item is not a clean one. Hashes come last, for the
reader who wants to check. No generation timestamp, so unchanged bundles
render to identical bytes.
"""

import argparse
import html
import json
import sys
from pathlib import Path


from clew.ledger import bundlestore
from clew.ledger import policy as policy_module
from clew.ledger import query

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
.said { font-style: italic; }
ul.checks { list-style: none; margin: 0; padding: 0; }
ul.checks li { margin: .15rem 0; }
.muted { color: var(--muted); }
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


def short(digest, n=12):
    return (digest or "")[:n]


# ------------------------------------------------------------- what happened

def told(bundle):
    """
    One incident as a reader would describe it: what arrived, what it became,
    who decided, what the plan said. Drawn only from the bundle's own documents.
    """
    documents = bundle["documents"]
    plan = bundlestore.plan_of(bundle)
    record = documents.get("triage.json")
    decision = documents.get("decision.json")
    gate = documents.get("gate.json")
    what = {"name": bundle["name"], "hash": bundle["hash"], "said": None, "trigger": None,
            "outcome": None, "by": None, "verdicts": None, "kind": "plan"}
    if record:
        what["said"] = (record.get("incident") or {}).get("text")
    if plan:
        what["trigger"] = plan.get("trigger")
        summary = query.plan_summary(plan)["result"]
        what["verdicts"] = ", ".join(f"{n} {action}" for action, n in summary["actions"].items())
        what["outcome"] = (f"asked, {plan['tasks_affected']} of {plan['tasks_total']} tasks"
                           if "tasks_affected" in plan else "asked")
        if decision:
            what["by"] = f"{decision.get('actor')}, after it was held"
        elif record:
            what["by"] = (f"settings {record.get('settings', {}).get('version')}, "
                          f"{record.get('backend')} at {record.get('confidence')}")
    elif decision:
        what["kind"] = "decision"
        what["outcome"] = "dismissed"
        what["by"] = decision.get("actor")
        what["trigger"] = "none"
    elif gate:
        what["kind"] = "gate"
        what["said"] = f"gate on {gate.get('samplesheet')}"
        what["outcome"] = "PASS" if gate.get("passed") else "STOP"
        what["verdicts"] = ", ".join(f"{n} {status}" for status, n in sorted(gate.get("counts", {}).items()))
    return what


def section_incidents(store):
    bundles, _, _ = store
    rows = []
    for bundle in bundles:
        what = told(bundle)
        intact = bundlestore.check_integrity(bundle)["result"]["all_passed"]
        rows.append(
            "<tr>"
            f'<td>{("<span class=said>" + esc(what["said"]) + "</span>") if what["said"] else "<span class=muted>no incident text sealed</span>"}'
            f'<br><a class="hash" href="#{esc(bundle["name"])}">{esc(bundle["name"])}</a></td>'
            f"<td><code>{esc(what['trigger'] or '')}</code></td>"
            f"<td>{esc(what['outcome'] or '')}</td>"
            f"<td>{esc(what['by'] or '')}</td>"
            f"<td>{esc(what['verdicts'] or '')}</td>"
            f"<td>{tag('verified', 'ok') if intact else tag('DID NOT VERIFY', 'bad')}</td>"
            "</tr>")
    return ("<h2>Incidents</h2>"
            "<p>Each row is one sealed answer. The sentence is the incident as it "
            "arrived, the trigger is what it became, and the last column is the "
            "verifier's word on the bundle it came from.</p>"
            "<table><tr><th>Incident</th><th>Trigger</th><th>Outcome</th>"
            "<th>Decided by</th><th>Verdicts</th><th>Bundle</th></tr>"
            + "".join(rows) + "</table>")


def section_header(store, root):
    bundles, entries, conflicts = store
    parts = [
        "<h1>Clew evidence</h1>",
        '<p class="lede">A view over the bundles below. '
        '<strong>This page is not the record</strong>, the bundles are. Every '
        'row names the bundle it came from, and anything here can be checked '
        'with <code>clew evidence verify</code>.</p>',
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


# -------------------------------------------------------------------- limits

def section_unknowns(store):
    """Second, after what happened, and before any hash. Gaps get the same weight as findings."""
    bundles, _, _ = store
    undetermined = unknown_subjects = 0
    seen = {}

    def note(text):
        seen[text] = seen.get(text, 0) + 1

    for bundle in bundles:
        plan = bundlestore.plan_of(bundle)
        if plan:
            missing = [i for i in plan.get("plan", []) if not i.get("action")]
            undetermined += len(missing)
            if missing:
                note(f"{len(missing)} of {len(plan.get('plan', []))} items have no verdict: "
                     "storage was not verified and the answer depends on it")
        gate = bundle["documents"].get("gate.json")
        if gate:
            count = gate.get("counts", {}).get("UNKNOWN", 0)
            unknown_subjects += count
            if count:
                note(f"{count} subjects were not found in the log, commonly an "
                     "identifier mismatch, not a clean result")
        for text in bundle["manifest"].get("coverage", []):
            note(text)

    counts = (
        f'<div class="counts">'
        f'<div class="count{" unknown" if undetermined else ""}">'
        f"<b>{undetermined}</b><span>verdicts withheld</span></div>"
        f'<div class="count{" unknown" if unknown_subjects else ""}">'
        f"<b>{unknown_subjects}</b><span>subjects unknown</span></div>"
        f"</div>")
    many = len(bundles) > 1
    notes = [f"{text}" + (f" ({n} of {len(bundles)} bundles)" if many else "")
             for text, n in sorted(seen.items(), key=lambda item: (-item[1], item[0]))]
    return ("<h2>What is not known</h2>"
            "<p>An unanswered item is <strong>not a clean one</strong>. Each "
            "limit below is stated once, with how many bundles it applies to.</p>"
            + counts
            + coverage_panel(notes, "Stated limits of this record"))


# ----------------------------------------------------------------- integrity

def section_integrity(store):
    bundles, _, _ = store
    rows = []
    for bundle in bundles:
        result = bundlestore.check_integrity(bundle)
        items = []
        for check in result["result"]["checks"]:
            kind = "ok" if check["ok"] else ("bad" if check["ok"] is False else "unknown")
            label = check["check"] if check["ok"] else (
                f"{check['check']} FAILED" if check["ok"] is False else f"{check['check']} not checked")
            items.append(f'<li>{tag(label, kind)} <span class="muted">{esc(check["detail"])}</span></li>')
        rows.append(
            f'<tr id="{esc(bundle["name"])}"><td><code>{esc(bundle["name"])}</code><br>'
            f'<span class="hash">{esc(bundle["hash"])}</span></td>'
            f'<td><ul class="checks">{"".join(items)}</ul></td></tr>')
    return ("<h2>Integrity</h2>"
            "<p>The verifier's own output. <code>replay</code> means every "
            "verdict was recomputed from the sealed facts and matched; "
            "<code>decision</code> means a person's decision is about the record "
            "sealed beside it.</p>"
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


# ------------------------------------------------------------------ findings

def section_plan(bundle, store):
    plan = bundlestore.plan_of(bundle)
    policy_document = bundlestore.policy_of(bundle)
    summary = query.plan_summary(plan)["result"]
    what = told(bundle)

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
        because = detail["reason"] if detail["action"] else (
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

    opening = (f'<p class="said">{esc(what["said"])}</p>' if what["said"] else "")
    return (f"<h3>{esc(plan.get('trigger'))}</h3>" + opening
            + (f"<p>Decided by {esc(what['by'])}.</p>" if what["by"] else "")
            + f'<p><span class="hash">bundle {esc(bundle["hash"])}</span><br>'
            f"policy <code>{esc(plan.get('policy_version'))}</code> "
            f'<span class="hash">{esc(plan.get("policy_hash"))}</span></p>'
            f'<div class="counts">{counts}</div>'
            "<table><tr><th>Task</th><th>Verdict</th>"
            "<th>Why, and the chain that reaches it</th></tr>"
            + "".join(rows) + "</table>")


def section_decision(bundle):
    record = bundle["documents"].get("triage.json") or {}
    decision = bundle["documents"]["decision.json"]
    shown = decision.get("triage") or {}
    return (f"<h3>Dismissed by {esc(decision.get('actor'))}</h3>"
            + (f'<p class="said">{esc((record.get("incident") or {}).get("text"))}</p>'
               if record.get("incident") else "")
            + f"<p>Reason: {esc(decision.get('reason') or 'none given')}. "
            f"Decided {esc((decision.get('decided_at') or '')[:19])}. "
            f"Triage had said {esc(shown.get('choice'))} at {esc(shown.get('confidence'))}: "
            f"{esc(shown.get('reason'))}.</p>"
            f'<p><span class="hash">bundle {esc(bundle["hash"])}</span><br>'
            f"settings <code>{esc(record.get('settings', {}).get('version'))}</code></p>")


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
    return (f"<h3>Gate, {esc(result.get('samplesheet'))} "
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
        elif "decision.json" in bundle["documents"]:
            parts.append(section_decision(bundle))
    return "\n".join(parts)


# ----------------------------------------------------------- log and policy

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
            "<p><strong>Effective</strong> is when a fact became true, "
            "<strong>recorded</strong> is when the log heard it. Work done "
            "between the two was done in good faith and still has to be "
            "accounted for.</p>"
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
            "is what makes the version label checkable.</p>"
            "<table><tr><th>Version</th><th>Effective from</th>"
            "<th>Adopted by</th><th>sha256</th></tr>" + rows + "</table>"
            + coverage_panel(result["coverage"]))


def render(store, root):
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Clew evidence</title>
<style>{STYLE}</style></head>
<body><main>
{section_header(store, root)}
{section_incidents(store)}
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
to defend them. This is a system of record, not an attester. It does not
decide whether a use was compliant. It makes it impossible to lose the record
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
        print(f"WARNING: {len(store[2])} sequence conflicts, these bundles "
              f"were sealed from different logs; the page says so.")


if __name__ == "__main__":
    main()
