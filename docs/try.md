# Try Clew on a recorded run

Two runs of [nf-core/demo](https://github.com/nf-core/demo) 1.2.0 are in
`examples/demo-run`: `baseline`, and `trimmed`, which is the same pipeline
with five bases cut from the start of every read. Nextflow recorded both in
a lineage store with content checksums, so every command below answers
from the record and nothing has to be computed first.

In a Codespace the runs are already there. Anywhere else, fetch them once:

```bash
curl -sL https://github.com/QuietFlare/clew/releases/download/v0.6.1/clew-demo-runs.tgz | tar -xz -C examples
```

## See what was recorded

```bash
clew extract nextflow --store examples/demo-run/.lineage --list-runs
```

Two runs, each with a timestamp and a hash. Every other command names the
store with `--runs` and a run with `--run`.

## Impact: a container is bad, what did it reach

Say the seqtk container turns out to have a defect.

```bash
clew impact --runs examples/demo-run/.lineage --run trimmed --trigger container:seqtk \
    --work-root examples/demo-run/work --results examples/demo-run/results-trimmed
```

Three SEQTK_TRIM tasks are affected, the five others are untouched, and
each affected task gets REGENERATE under rule R8 with the published files
it reaches listed beneath it. The policy version and hash are printed at
the top because every verdict is a verdict under that table.

Add `--json plan.json` to keep the plan, then seal it and check the seal:

```bash
clew evidence seal --plan plan.json --out bundle
```

```bash
clew evidence verify bundle
```

Verify recomputes every verdict from the inputs in the bundle, with no
database and no credentials. Hand the folder to someone else and they can
do the same.

## Drift: two runs differ, where did it start

```bash
clew drift --runs examples/demo-run/.lineage --before baseline --after trimmed
```

SEQTK_TRIM is a root in all three samples with cause "script changed",
which is the trimming parameter, read from the record. MULTIQC is marked
downstream: it differs because trim did. COWPY reproduced.

FASTQC also shows as a root, with cause "same inputs and recipe, different
outputs", and only its `.zip` files differ. FastQC writes a timestamp into
the archive, so two identical runs never produce the same bytes. Nothing
changed in the science, and no setup comparison would have found this.
Once you know, tell drift to look past it:

```bash
clew drift --runs examples/demo-run/.lineage --before baseline --after trimmed \
    --ignore versions.yml --ignore '*_fastqc.zip'
```

Now FASTQC reproduces, and MULTIQC stops being downstream and becomes a
root of its own, with cause "input changed" naming two files:
`methods_description_mqc.yaml` and `workflow_summary_mqc.yaml`. The
pipeline writes those for the report, and they carry the run's name and
command line, which differed. That cause was read from the record too.
Four roots in all: three SEQTK_TRIM and one MULTIQC.

## Reclaim: which work directories can go, with proof

```bash
clew reclaim --runs examples/demo-run/.lineage --run baseline \
    --work-root examples/demo-run/work --results examples/demo-run/results-baseline
```

Four directories are REDUNDANT: every output has a published copy with the
same content checksum, and the checksum came from the store, not from
reading the files. Four are KEEP, with the reason. Nothing is deleted.
`--apply --receipt receipt.jsonl` would delete the four and write one line
per directory before removing it.

## The page

```bash
clew ui --forwarded --no-browser
```

In a Codespace, open the printed link, or the port labelled Clew UI. On
your own machine drop `--forwarded`. Pick `examples/demo-run` in the run
bar, write an incident such as "the seqtk container has a defect", and
press Run. Triage, the plan and the seal appear as their files appear.

Triage with a typed classifier needs `TYPESAFE_API_KEY`, and the agent
that runs the steps needs `ANTHROPIC_API_KEY`. In a Codespace, set them as
Codespace secrets before opening it. Without them, triage matches by name
and the agent tabs say what is missing. Every command above needs neither.

## Bring your own run

The Codespace ships Nextflow 26.09.2-edge with `NXF_CACHE_MODE=DEEP` set,
which is what makes the store carry content checksums; Nextflow before
26.09.0-edge labels its checksums with that mode but computes them in
standard mode, and Clew says so when it reads such a store. Add lineage
to your config and run as usual:

```groovy
lineage.enabled = true
```

Then `clew extract nextflow --store .lineage --list-runs` and the commands
above with your paths. [Sources](sources.md) says what each engine
records and what that limits.
