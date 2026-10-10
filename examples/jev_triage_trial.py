"""
Can a typed classifier turn an incident report into the right trigger?

    python examples/jev_triage_trial.py --dry-run
    python examples/jev_triage_trial.py --out evals/triage_answers.json

The options are the tools the shipped sarek run actually used, read from
its graph, plus `none`. Each incident is one call. The answer is compared
with a hand-written key and with a baseline that picks a tool only when
its name appears in the incident.

The incidents and the key are written by hand for this trial. They show
where the classifier is weak, not how it does on real advisories.
"""

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path

from jev_trial import MODEL, ask

GRAPH = Path(__file__).resolve().parent.parent / "clew" / "data" / "graph5.json"
NONE = "none"

INSTRUCTIONS = (
    "The incident reports a defect or a change in a software tool. Which tool "
    "used in this run does it concern? Choose none when the tool it "
    "concerns was not used in this run.")

# (incident, expected tool, how the incident names it)
INCIDENTS = [
    ("GATK 4.5 BaseRecalibrator writes wrong covariates for reads with soft "
     "clips. Upgrade to 4.6.", "gatk4", "named"),
    ("samtools 1.21: stats reports a wrong insert size on CRAM input.",
     "samtools", "named"),
    ("Strelka2 2.9.10 miscounts indel alleles next to homopolymers.",
     "strelka", "named"),
    ("MultiQC 1.33 drops samples whose names contain a dot.",
     "multiqc", "named"),
    ("bcftools stats 1.21 counts multiallelic sites twice.",
     "bcftools", "named"),
    ("mosdepth 0.3.10 reports zero coverage for the last window of each "
     "contig.", "mosdepth", "named"),
    ("HTSlib 1.21 fails to decode CRAM blocks that embed the reference.",
     "htslib", "named"),

    ("The duplicate marking step flags optical duplicates wrongly on "
     "patterned flowcells.", "gatk4", "described"),
    ("Advisory for the short-read aligner: mem mode clips supplementary "
     "alignments wrongly.", "bwa", "described"),
    ("The read quality report shows wrong per-base scores for reads longer "
     "than 500 bases.", "fastqc", "described"),
    ("bgzip and tabix: indexes for files over 4 GB are truncated.",
     "htslib", "described"),
    ("The TsTv-by-count and TsTv-by-qual summaries are off by one in the "
     "last bin.", "vcftools", "described"),
    ("Base quality recalibration tables come out wrong when the known-sites "
     "file has no index.", "gatk4", "described"),
    ("The small variant caller run in single-sample mode ignores the call "
     "regions file.", "strelka", "described"),

    ("STAR 2.7.11 writes corrupted splice junction files in two-pass mode.",
     NONE, "absent"),
    ("Salmon 1.10 shows a quantification bias on long reads.",
     NONE, "absent"),
    ("DeepVariant 1.6 miscalls in the HLA region with the PacBio model.",
     NONE, "absent"),
    ("The cluster scheduler is down for maintenance on Saturday.",
     NONE, "absent"),
    ("BLAST+ 2.15 changes its database format.", NONE, "absent"),
    ("fastp 0.23 drops reads when adapter trimming meets a poly-G tail.",
     NONE, "near miss"),
    ("BWA-MEM2 2.2.1 indexes cannot be read by earlier releases.",
     NONE, "near miss"),
]


def tools_in(graph):
    """{tool: processes}, tools read off each image name."""
    used = defaultdict(set)
    for task in graph["tasks"].values():
        image = (task.get("container") or "").rsplit("/", 1)[-1].split(":")[0]
        process = (task.get("process") or "").split(":")[-1]
        for tool in filter(None, image.split("_")):
            used[tool].add(process)
    return used


def request(incident, used, model):
    criteria = {tool: "in the image used by " + ", ".join(sorted(processes))
                for tool, processes in sorted(used.items())}
    criteria[NONE] = "the incident concerns nothing this run used"
    return {
        "model": model,
        "state": {"incident": incident},
        "questions": {"tool": {"type": "choice", "instructions": INSTRUCTIONS,
                               "criteria": criteria}},
    }


def by_name(incident, used):
    """The baseline: the one tool whose name the incident contains, else none."""
    hits = [tool for tool in used if tool in incident.lower()]
    return hits[0] if len(hits) == 1 else NONE


def kind_of_miss(expected, got):
    if got == expected:
        return None
    if got == NONE:
        return "missed"        # a real problem dismissed: no plan is made
    if expected == NONE:
        return "false alarm"   # an unneeded plan: cheap, and visible
    return "wrong tool"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--limit", type=int, help="stop after this many incidents")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the first request and send nothing")
    parser.add_argument("--out", metavar="PATH", help="write every answer as JSON")
    args = parser.parse_args(argv)

    used = tools_in(json.loads(GRAPH.read_text()))
    todo = INCIDENTS[:args.limit]

    if args.dry_run:
        print(json.dumps(request(todo[0][0], used, args.model), indent=2))
        print(f"\n{len(todo)} incidents, {len(used)} tools, nothing sent")
        return 0

    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        raise SystemExit("set TYPESAFE_API_KEY first")

    rows = []
    for incident, expected, naming in todo:
        reply = ask(request(incident, used, args.model), key)
        answer = reply["answers"]["tool"]
        miss = kind_of_miss(expected, answer["choice"])
        rows.append({"incident": incident, "expected": expected, "naming": naming,
                     "baseline": by_name(incident, used), "answer": answer,
                     "model": reply.get("model"), "miss": miss})
        print(f"{(miss or 'ok'):<11} {naming:<10} {expected:<9} "
              f"{answer['choice']:<10} {answer['confidence']:.2f}  {incident[:48]}")

    right = [r for r in rows if not r["miss"]]
    wrong = [r for r in rows if r["miss"]]
    baseline = sum(r["baseline"] == r["expected"] for r in rows)
    print(f"\nclassifier {len(right)} of {len(rows)}, "
          f"name-match baseline {baseline} of {len(rows)}")
    for label in ("missed", "false alarm", "wrong tool"):
        print(f"  {label:<11} {sum(r['miss'] == label for r in rows)}")
    if right:
        print("lowest confidence when right  "
              f"{min(r['answer']['confidence'] for r in right):.2f}")
    if wrong:
        print("highest confidence when wrong "
              f"{max(r['answer']['confidence'] for r in wrong):.2f}")

    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=2))
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
