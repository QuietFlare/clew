"""
Clew — the whole argument in one command, on one real pipeline run.

    python3 demo.py

Three questions, three audiences, one engine. Every number below is computed
live from graph5.json — a real nf-core/sarek run (5 synthetic donors,
81 tasks, 344 file-level edges) whose lineage was rebuilt from Nextflow's
work/ directory with no pipeline modification.
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import blast_radius as core
from core import contribution
from domains import sarek

ROOT = Path(__file__).resolve().parent


def plan_for(graph, entry_nodes, affected, exclusive_set, published):
    """Verdict per affected task, as {action: [(hash, facts)]}."""
    plan = {}
    for task_hash in sorted(affected):
        facts = sarek.classify(graph, task_hash, task_hash in exclusive_set,
                               published=published)
        action = contribution.remediate(
            facts["contribution"], storage=facts["storage"],
            exclusive=facts["exclusive"], terminal=facts["terminal"],
        )
        plan.setdefault(action, []).append((task_hash, facts))
    return plan


def show(plan, graph, sample_rows=3):
    for action in sorted(plan):
        rows = plan[action]
        print(f"    {action:<12} {len(rows):>3}  — {contribution.explain(action)}")
        for task_hash, facts in rows[:sample_rows]:
            print(f"        {task_hash}  {sarek.describe(graph, task_hash)}")
            if facts["terminal"]:
                print(f"            {facts['reason']}")
        if len(rows) > sample_rows:
            print(f"        ... {len(rows) - sample_rows} more")


def main():
    graph = core.load_graph(ROOT / "graph5.json")
    donors = sarek.load_donors(ROOT / "donors.csv")
    published = sarek.load_assertions(ROOT / "assertions.json")
    n = len(graph["tasks"])

    print(f"Run: nf-core/sarek, {len(donors)} donors, {n} tasks, "
          f"{len(graph['edges'])} edges, lineage rebuilt from work/ symlinks.\n")

    # ------------------------------------------------------------------ act 1
    print("=" * 70)
    print("1. ENGINEER — 'We bumped the reference genome. What must be re-run?'")
    print("=" * 70)
    subjects = sarek.external_input_entry_nodes(graph, "genome.fasta")
    radius = core.blast_radius(graph, subjects)
    affected = radius["input:genome.fasta"]["affected"]
    entry = subjects["input:genome.fasta"]
    print(f"\n  genome.fasta was consumed directly by {len(entry)} tasks;")
    print(f"  everything calibrated against it: {len(affected)} of {n} tasks.\n")
    show(plan_for(graph, entry, affected, set(), published), graph)
    print(f"\n  The {n - len(affected)} untouched tasks are provably out of "
          "scope — no chain of derivation reaches them.")

    # ------------------------------------------------------------------ act 2
    print()
    print("=" * 70)
    print("2. QA — 'A defect was reported in a GATK4 container. What did it touch?'")
    print("=" * 70)
    subjects = sarek.container_entry_nodes(graph, "gatk4")
    radius = core.blast_radius(graph, subjects)
    affected = radius["container:gatk4"]["affected"]
    entry = subjects["container:gatk4"]
    print(f"\n  {len(entry)} tasks ran in a gatk4 container; with everything")
    print(f"  derived from their outputs: {len(affected)} of {n} tasks suspect.\n")
    show(plan_for(graph, entry, affected, set(), published), graph)
    print("\n  Note: nothing is DESTROYED. A defect casts doubt; it does not")
    print("  remove a source. The artifacts are still wanted — rebuilt, not deleted.")

    # ------------------------------------------------------------------ act 3
    print()
    print("=" * 70)
    print("3. COMPLIANCE — 'donor_003 withdrew consent. What happens now?'")
    print("=" * 70)
    entry_by_donor = sarek.subject_entry_nodes(graph, donors)
    radius = core.blast_radius(graph, entry_by_donor)
    r = radius["donor_003"]
    print(f"\n  donor_003's material enters at {len(entry_by_donor['donor_003'])} tasks;"
          f" {len(r['affected'])} of {n} tasks affected.")
    print(f"  {len(r['exclusive'])} exist only because of donor_003; "
          f"{len(r['shared'])} also serve other donors.\n")
    show(plan_for(graph, entry_by_donor["donor_003"], r["affected"],
                  r["exclusive"], published), graph)

    print("""
  One traversal, two verdicts: the donor's own artifacts are destroyed,
  but the published cohort report is immutable history — the answer there
  is disclosure, not deletion. The publication is an EXTERNAL ASSERTION
  (assertions.json records who claimed it and when); Clew records the
  claim, it does not certify it.
""")

    # ------------------------------------------------------------------ close
    print("=" * 70)
    print("Same engine, three triggers — only the entry-node selection differed.")
    print("Not shown here yet: append-only event log, policy versioning, signed")
    print("evidence bundles. Honest caveats: publication/MTA/destruction are")
    print("asserted from outside; uninstrumented systems are unknown, not clean.")


if __name__ == "__main__":
    main()
