"""
Clew — the whole argument in one command, on one real pipeline run.

    clew demo

Three questions, three audiences, one engine. Every number below is computed
live from graph5.json — a real nf-core/sarek run (5 synthetic donors,
81 tasks, 344 file-level edges) whose lineage was rebuilt from Nextflow's
work/ directory with no pipeline modification.
"""

import os
import sys
from collections import Counter
from pathlib import Path


from clew.core import blast_radius as core
from clew.core import contribution
from clew.core import policy
from clew.domains import sarek

ROOT = Path(__file__).resolve().parent


# Where this run's artifacts live, if anywhere. Set CLEW_WORK_ROOT to the
# work directory to have the demo check the disk; without it Clew reports the
# storage question as open rather than answering it, which is the honest
# answer and the one most readers will see — the sarek run's scratch was
# cleaned long ago, as pipeline scratch always is.
WORK_ROOT = os.environ.get("CLEW_WORK_ROOT")


def plan_for(graph, entry_nodes, affected, exclusive_set, published):
    """Verdict per affected task, as {action: [(hash, facts)]}."""
    plan = {}
    for task_hash in sorted(affected):
        facts = sarek.classify(graph, task_hash, task_hash in exclusive_set,
                               published=published, work_root=WORK_ROOT)
        action = policy.remediate(
            facts["contribution"], storage=facts["storage"],
            exclusive=facts["exclusive"], terminal=facts["terminal"],
        ) or policy.UNDETERMINED
        plan.setdefault(action, []).append((task_hash, facts))
    return plan


def show(plan, graph, sample_rows=3):
    for action in sorted(plan):
        rows = plan[action]
        explanation = ("no verdict — storage was not checked; set CLEW_WORK_ROOT"
                       if action == policy.UNDETERMINED
                       else contribution.explain(action))
        print(f"    {action:<12} {len(rows):>3}  — {explanation}")
        for task_hash, facts in rows[:sample_rows]:
            print(f"        {task_hash}  {sarek.describe(graph, task_hash)}")
            if facts["terminal"]:
                print(f"            {facts['reason']}")
        if len(rows) > sample_rows:
            print(f"        ... {len(rows) - sample_rows} more")


def main(argv=None):
    graph = core.load_graph(ROOT / "data" / "graph5.json")
    donors = sarek.load_donors(ROOT / "data" / "donors.csv")
    published = sarek.load_assertions(ROOT / "data" / "assertions.json")
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

    # ------------------------------------------------------------------ act 4
    chain = ROOT / "data" / "graph_chain.json"
    sheet = ROOT / "data" / "samplesheets" / "rnaseq_yeast.csv"
    if chain.exists() and sheet.exists():
        from clew.domains import rnaseq

        print()
        print("=" * 70)
        print("4. THE CHAIN — one withdrawal, two pipelines")
        print("=" * 70)
        g2 = core.load_graph(chain)
        entry2 = rnaseq.subject_entry_nodes(g2, rnaseq.load_subjects(sheet))
        radius2 = core.blast_radius(g2, entry2)
        r2 = radius2["SRR10441036_cox4d"]
        da = sorted(h for h in r2["affected"] if h.startswith("da:"))
        print(f"\n  A real yeast rnaseq run (171 tasks) published a count matrix;")
        print(f"  a separate differentialabundance run (12 tasks) consumed it.")
        print(f"  Withdrawing one sample: {len(r2['affected'])} of "
              f"{len(g2['tasks'])} tasks affected, {len(da)} of them in the")
        print(f"  OTHER pipeline — DESeq2 results, plots, the report bundle.\n")
        forward2 = core.forward_index(g2["edges"])
        target = next(h for h in da
                      if g2["tasks"][h]["process"].endswith("DESEQ2_DIFFERENTIAL"))
        for path in core.paths_to(entry2["SRR10441036_cox4d"], target,
                                  forward2, limit=1):
            hops = " -> ".join(f"{h}[{rnaseq.describe(g2, h)}]" for h in path)
            print(f"  evidence, crossing the run boundary:\n    {hops}\n")
        print("  Engine-level lineage sees each launch in isolation. The")
        print("  crossing is the part only the stitched graph can answer.")

    # ------------------------------------------------------------------ close
    print()
    print("=" * 70)
    stamp = policy.identify()
    print("Same engine, three triggers — only the entry-node selection differed.")
    print(f"Every verdict above is under policy {stamp['policy_version']}, "
          f"sha256 {stamp['policy_hash'][:16]};")
    print("`clew rulebook show` prints the table and the rationale for")
    print("each rule; `clew rulebook diff v1 v2` shows what the last change to")
    print("it was, and why. `clew evidence build` seals any of the above into")
    print("a bundle that replays offline, and `clew gate` stops a run whose")
    print("inputs are not permitted before the pipeline starts.")
    print("Honest caveats: publication/MTA/destruction are asserted from")
    print("outside; uninstrumented systems are unknown, not clean.")


if __name__ == "__main__":
    main()
