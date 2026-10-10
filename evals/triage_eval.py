"""
The triage eval: fixed cases, a pass rule, and a baseline to compare against.

    python evals/triage_eval.py --backend name          # no key, no cost
    python evals/triage_eval.py                         # Jev, appends a baseline row
    python evals/triage_eval.py --repeat 3

Each case is one project's release notes at a pinned tag, fetched from
GitHub and cached, so a rerun reads the same text. A case is tried under
the heading a release email carries and as the bare notes.

It fails when a real problem is dismissed, or when a headed incident is
acted on wrongly. A bare incident that never names its tool may raise a
false alarm: that costs one unneeded plan and is reported, not failed.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clew.graph import blast_radius as core
from clew.intake import classifier, triage

CASES = ROOT / "evals" / "triage_cases.json"
BASELINE = ROOT / "evals" / "triage_baseline.jsonl"
CACHE = ROOT / "evals" / ".cache"
MOST_CHARACTERS = 2000
FORMS = ("headed", "bare")


def notes(repository, tag):
    """The release notes at one tag, from the cache when they are there."""
    kept = CACHE / f"{repository.replace('/', '__')}__{tag}.json"
    if kept.is_file():
        return json.loads(kept.read_text())["body"]
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "clew-eval"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    call = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/releases/tags/{tag}", headers=headers)
    try:
        with urllib.request.urlopen(call, timeout=30) as reply:
            body = (json.load(reply).get("body") or "").strip()
    except urllib.error.HTTPError as bad:
        raise SystemExit(f"{repository} {tag}: GitHub answered HTTP {bad.code}")
    if not body:
        raise SystemExit(f"{repository} {tag}: no release notes there; drop the case")
    CACHE.mkdir(parents=True, exist_ok=True)
    kept.write_text(json.dumps({"repository": repository, "tag": tag, "body": body}, indent=2))
    return body


def sort(incident, graph, offered, backend, key):
    asked = triage.request(incident, offered, triage.DEFAULT)
    if backend == "name":
        words = {option: trigger.split(":", 1)[1] for option, trigger, _ in offered}
        answer = classifier.by_name(incident, words)
    else:
        criteria = asked["questions"][classifier.QUESTION]["criteria"]
        answer = classifier.checked(classifier.jev(asked, key, classifier.endpoint()),
                                    criteria, triage.DEFAULT["model"])
    return triage.triage(incident, offered, triage.DEFAULT, answer, backend,
                         named=triage.named_in_run(incident, graph))


def grade(expected, record):
    """right, and the kind of error when it is not held."""
    right = record["choice"] == expected
    if right or record["outcome"] == triage.HELD:
        return right, None
    if record["outcome"] == triage.DISMISSED:
        return right, "real problem dismissed"
    return right, "acted on wrongly"


def summary(rows, form):
    mine = [r for r in rows if r["form"] == form]
    return {"cases": len(mine), "right": sum(r["right"] for r in mine),
            "dismissed_wrongly": sum(r["error"] == "real problem dismissed" for r in mine),
            "acted_wrongly": sum(r["error"] == "acted on wrongly" for r in mine),
            "held": sum(r["outcome"] == triage.HELD for r in mine)}


def last_row(backend):
    if not BASELINE.is_file():
        return None
    rows = [json.loads(line) for line in BASELINE.read_text().splitlines() if line.strip()]
    return next((r for r in reversed(rows) if r["backend"] == backend), None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--backend", choices=("jev", "name"), default="jev")
    parser.add_argument("--repeat", type=int, default=1, help="tries per case and form")
    parser.add_argument("--no-baseline", action="store_true", help="do not append a baseline row")
    args = parser.parse_args(argv)

    suite = json.loads(CASES.read_text())
    graph = core.load_graph(str(ROOT / suite["graph"]))
    offered = triage.options(graph)
    offered_names = {option for option, _, _ in offered} | {classifier.NONE}
    key = os.environ.get(classifier.KEY_VARIABLE)
    if args.backend == "jev" and not key:
        raise SystemExit(f"set {classifier.KEY_VARIABLE} first, or use --backend name")

    rows = []
    for case in suite["cases"]:
        if case["expected"] not in offered_names:
            raise SystemExit(f"{case['repository']}: {case['expected']!r} is not an option of this run")
        body = notes(case["repository"], case["tag"])[:MOST_CHARACTERS]
        texts = {"headed": f"[{case['repository']}] Release {case['tag']}\n\n{body}", "bare": body}
        for form in FORMS:
            for attempt in range(args.repeat):
                record = sort(texts[form], graph, offered, args.backend, key)
                right, error = grade(case["expected"], record)
                rows.append({"repository": case["repository"], "tag": case["tag"], "form": form,
                             "expected": case["expected"], "choice": record["choice"],
                             "confidence": record["confidence"], "outcome": record["outcome"],
                             "right": right, "error": error})
                sure = "    " if record["confidence"] is None else f"{record['confidence']:.2f}"
                print(f"{'ok  ' if right else 'DIFF'} {form:<7} {case['repository']:<22} "
                      f"expect {case['expected']:<9} got {record['choice']:<9} {sure} "
                      f"{record['outcome']:<10} {error or ''}")

    totals = {form: summary(rows, form) for form in FORMS}
    before = last_row(args.backend)
    print()
    for form in FORMS:
        now = totals[form]
        was = f"  (before: {before[form]['right']} of {before[form]['cases']})" if before else ""
        print(f"{form:<7} {now['right']} of {now['cases']} right, {now['held']} held, "
              f"{now['acted_wrongly']} acted on wrongly, {now['dismissed_wrongly']} dismissed wrongly{was}")

    failures = []
    if any(totals[form]["dismissed_wrongly"] for form in FORMS):
        failures.append("a real problem was dismissed")
    if totals["headed"]["acted_wrongly"]:
        failures.append("a headed incident was acted on wrongly")
    print("\nFAIL: " + " and ".join(failures) if failures else "\nPASS")

    if not args.no_baseline:
        row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "backend": args.backend,
               "model": triage.DEFAULT["model"] if args.backend == "jev" else None,
               "settings": triage.identify(triage.DEFAULT), "graph": suite["graph"],
               "options": len(offered), "repeat": args.repeat, "passed": not failures,
               **totals, "rows": rows}
        with BASELINE.open("a") as handle:
            handle.write(json.dumps(row) + "\n")
        print(f"appended to {BASELINE.relative_to(ROOT)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
