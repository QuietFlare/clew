"""
Clew: the whole argument in one command, on one real pipeline run.

    clew demo
    clew demo --work-root /path/to/work

Three questions, three audiences, one engine. Every number below is computed
live from graph5.json, a real nf-core/sarek run (5 synthetic donors,
81 tasks, 344 file-level edges) whose lineage was rebuilt from Nextflow's
work/ directory with no pipeline modification.

WHAT THE DEMO CAN AND CANNOT SETTLE
-----------------------------------
The run's work/ was cleaned before it shipped, so the demo cannot check
whether any artifact is still on disk, and Clew never guesses. A verdict
that depends on storage is shown as OPEN, with the verdict each storage
state would produce, so the reader sees the whole answer short of the one
fact only a disk can supply. Pass --work-root on a run whose work/ still
exists and those lines settle. The published report settles without it:
under policy v2 publication is asked before existence.
"""

import argparse
import os
from pathlib import Path


from clew.graph import blast_radius as core
from clew.graph import contribution
from clew.ledger import policy
from clew.domains import sarek

ROOT = Path(__file__).resolve().parent

OPEN = "OPEN"

# How each storage state reads in a sentence.
STATE_WORDS = {
    contribution.WRITABLE: "the workdir is still there and writable",
    contribution.WORM: "it sits on write-once storage",
    contribution.DESTROYED: "it was cleaned and no published copy remains",
}

NOT_CHECKED = "storage not checked: no --work-root given"
CLEANED = ("workdir cleaned, published copies not checked; "
           "clew impact --results looks there")


def plan_for(graph, affected, exclusive_set, published, work_root):
    """
    Verdict per affected task, grouped for display.

    Returns {(label, outcomes, why_open): [(hash, facts)]}. `label` is the
    action, or OPEN when the verdict depends on storage. For OPEN groups
    `outcomes` lists what each storage state would settle to, and
    `why_open` says which fact is missing.
    """
    plan = {}
    for task_hash in sorted(affected):
        facts = sarek.classify(graph, task_hash, task_hash in exclusive_set,
                               published=published, work_root=work_root)
        why_open = NOT_CHECKED
        if facts["storage"] == contribution.DESTROYED:
            # A cleaned workdir settles nothing on its own: the published
            # copy may still exist, and this demo does not look there.
            # Leaving it open is the same rule clew impact applies.
            facts["storage"] = None
            why_open = CLEANED
        dims = dict(exclusive=facts["exclusive"], terminal=facts["terminal"])
        decision = policy.decide(facts["contribution"],
                                 storage=facts["storage"], **dims)
        if decision["action"]:
            key = (decision["action"], (), "")
        else:
            outcomes = tuple(
                (state, policy.decide(facts["contribution"],
                                      storage=state, **dims))
                for state in contribution.STORAGE)
            key = (OPEN, tuple((s, d["action"], d["rule"])
                               for s, d in outcomes), why_open)
        plan.setdefault(key, []).append((task_hash, facts))
    return plan


def show(plan, graph, sample_rows=3):
    for label, outcomes, why_open in sorted(plan):
        rows = plan[(label, outcomes, why_open)]
        if label == OPEN:
            print(f"    {label:<12} {len(rows):>3}  {why_open}")
            for state, action, rule in outcomes:
                print(f"        {action:<12} if {STATE_WORDS[state]} "
                      f"(rule {rule})")
        else:
            print(f"    {label:<12} {len(rows):>3}  {contribution.explain(label)}")
        for task_hash, facts in rows[:sample_rows]:
            print(f"        {task_hash}  {sarek.describe(graph, task_hash)}")
            if facts["terminal"]:
                print(f"            {facts['reason']}")
        if len(rows) > sample_rows:
            print(f"        ... {len(rows) - sample_rows} more")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="The shipped sample run: three triggers, one engine.")
    parser.add_argument(
        "--work-root", metavar="DIR", default=os.environ.get("CLEW_WORK_ROOT"),
        help="the run's work directory, if it still exists. Without it the "
             "storage question is left open, never guessed. $CLEW_WORK_ROOT")
    args = parser.parse_args(argv)
    work_root = args.work_root

    graph = core.load_graph(ROOT / "data" / "graph5.json")
    donors = sarek.load_donors(ROOT / "data" / "donors.csv")
    published = sarek.load_assertions(ROOT / "data" / "assertions.json")
    n = len(graph["tasks"])

    print(f"Run: nf-core/sarek, {len(donors)} donors, {n} tasks, "
          f"{len(graph['edges'])} edges, lineage rebuilt from work/ symlinks.")
    if work_root:
        print(f"Storage checked under {work_root}.\n")
    else:
        print("Storage not checked: this run's work/ was cleaned before it "
              "shipped, and Clew never guesses.\nEach OPEN line below names "
              "the verdict for every storage state; on a run whose work/ "
              "still exists,\n`clew demo --work-root DIR` settles them.\n")

    # ------------------------------------------------------------------ act 1
    print("=" * 70)
    print("1. ENGINEER: 'We bumped the reference genome. What must be re-run?'")
    print("=" * 70)
    subjects = sarek.external_input_entry_nodes(graph, "genome.fasta")
    radius = core.blast_radius(graph, subjects)
    affected = radius["input:genome.fasta"]["affected"]
    entry = subjects["input:genome.fasta"]
    print(f"\n  genome.fasta was consumed directly by {len(entry)} tasks;")
    print(f"  everything calibrated against it: {len(affected)} of {n} tasks.\n")
    show(plan_for(graph, affected, set(), published, work_root), graph)
    print(f"\n  The {n - len(affected)} untouched tasks are provably out of "
          "scope: no chain of derivation reaches them.")

    # ------------------------------------------------------------------ act 2
    print()
    print("=" * 70)
    print("2. QA: 'A defect was reported in a GATK4 container. What did it touch?'")
    print("=" * 70)
    subjects = sarek.container_entry_nodes(graph, "gatk4")
    radius = core.blast_radius(graph, subjects)
    affected = radius["container:gatk4"]["affected"]
    entry = subjects["container:gatk4"]
    print(f"\n  {len(entry)} tasks ran in a gatk4 container; with everything")
    print(f"  derived from their outputs: {len(affected)} of {n} tasks suspect.\n")
    show(plan_for(graph, affected, set(), published, work_root), graph)
    print("\n  Note: nothing can be DESTROYED here. A defect casts doubt; it")
    print("  does not remove a source. The artifacts are still wanted:")
    print("  rebuilt, not deleted. The published report is the one settled")
    print("  verdict, and it settles without a disk: publication outlives bytes.")

    # ------------------------------------------------------------------ act 3
    print()
    print("=" * 70)
    print("3. COMPLIANCE: 'donor_003 withdrew consent. What happens now?'")
    print("=" * 70)
    entry_by_donor = sarek.subject_entry_nodes(graph, donors)
    radius = core.blast_radius(graph, entry_by_donor)
    r = radius["donor_003"]
    print(f"\n  donor_003's material enters at {len(entry_by_donor['donor_003'])} tasks;"
          f" {len(r['affected'])} of {n} tasks affected.")
    print(f"  {len(r['exclusive'])} exist only because of donor_003; "
          f"{len(r['shared'])} also serve other donors.\n")
    show(plan_for(graph, r["affected"], r["exclusive"], published, work_root),
         graph)

    print("""
  One traversal, two answers. The donor's own artifacts go entirely once
  the workdir is confirmed writable, and nothing else needs them. The
  published cohort report is immutable history: the answer there is
  disclosure, not deletion, whatever the disk says. The publication is
  an EXTERNAL ASSERTION (assertions.json records who claimed it and
  when); Clew records the claim, it does not certify it.
""")

    # ------------------------------------------------------------------ act 4
    chain = ROOT / "data" / "graph_chain.json"
    sheet = ROOT / "data" / "samplesheets" / "rnaseq_yeast.csv"
    if chain.exists() and sheet.exists():
        from clew.domains import rnaseq

        print()
        print("=" * 70)
        print("4. THE CHAIN: one withdrawal, two pipelines")
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
        print(f"  OTHER pipeline: DESeq2 results, plots, the report bundle.\n")
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
    print("Same engine, three triggers: only the entry-node selection differed.")
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
