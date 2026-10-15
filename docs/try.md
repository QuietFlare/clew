# Try Clew on nf-core/demo

The walkthrough uses [nf-core/demo](https://github.com/nf-core/demo) 1.2.0
on its test profile. First set Nextflow up to record lineage, then record
two runs, then ask Clew about them one command at a time. Every answer
comes from the record; nothing is computed first.

In a Codespace steps 1 and 2 are done: the environment is set and both
runs are in `examples/demo-run`. Read them to see what was set, then go
on to step 3.

## 1. Set Nextflow up for lineage

Three settings make a store that Clew can read in full. Docker is needed
too, since the test profile runs in containers.

Nextflow 26.09.0-edge or later. Earlier versions label their checksums as
content checksums but compute them in standard mode, and Clew says so when
it reads such a store.

```bash
export NXF_VER=26.09.2-edge
```

`NXF_CACHE_MODE=DEEP` in the environment before Nextflow starts. This is
what makes the recorded checksums content hashes rather than path and
time. No config setting replaces it.

```bash
export NXF_CACHE_MODE=DEEP
```

Lineage enabled in the config of the folder you run from. The runs below
go in `examples/demo-run`, which is where every later command looks.

```bash
mkdir -p examples/demo-run && cd examples/demo-run
```

```bash
cat > nextflow.config <<'EOF'
lineage.enabled = true
EOF
```

Check all three before running anything:

```bash
nextflow -version && echo $NXF_CACHE_MODE && cat nextflow.config
```

## 2. Record two runs

The first run is the pipeline as shipped. It pulls the containers, so it
takes longer than the second.

```bash
nextflow run nf-core/demo -r 1.2.0 -profile test,docker -name baseline --outdir results-baseline
```

The second cuts five bases from the start of every read and changes
nothing else:

```bash
cat > trim.config <<'EOF'
process { withName: 'SEQTK_TRIM' { ext.args = '-b 5' } }
EOF
```

```bash
nextflow run nf-core/demo -r 1.2.0 -profile test,docker -name trimmed --outdir results-trimmed -c trim.config
```

Both runs are now in `.lineage` beside `work` and the two results
folders. Go back to the checkout root, where the rest of the page runs:

```bash
cd ../..
```

In a Codespace, both runs are already recorded in this folder. Do not
record them again there: a second run with the same name makes `--run`
ambiguous. To record them yourself, use a fresh folder and your own paths
from then on.

Anywhere else, if you would rather not run Nextflow, fetch the two runs
recorded on CI instead:

```bash
curl -sL https://github.com/QuietFlare/clew/releases/download/v0.6.1/clew-demo-runs.tgz | tar -xz -C examples
```

## 3. See what was recorded

```bash
clew extract nextflow --store examples/demo-run/.lineage --list-runs
```

Two runs, each with a timestamp and a hash. Every other command names the
store with `--runs` and a run with `--run`.

## 4. Impact: a container is bad, what did it reach

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

## 5. Drift: two runs differ, where did it start

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

## 6. Reclaim: which work directories can go, with proof

```bash
clew reclaim --runs examples/demo-run/.lineage --run baseline \
    --work-root examples/demo-run/work --results examples/demo-run/results-baseline
```

Four directories are REDUNDANT: every output has a published copy with the
same content checksum, and the checksum came from the store, not from
reading the files. Four are KEEP, with the reason. Nothing is deleted.
`--apply --receipt receipt.jsonl` would delete the four and write one line
per directory before removing it.

## 7. The page

```bash
clew ui
```

On your own machine the page opens. In a Codespace it cannot, so the
command prints the forwarded link instead: open that one, with its token,
not the port's bare address. Pick `examples/demo-run` in the run bar, by
clicking through the folders or typing the path, write an incident such
as "the seqtk container has a defect", and press Run. Triage, the plan
and the seal appear as their files appear.

Triage with a typed classifier needs `TYPESAFE_API_KEY`, and the agent
that runs the steps needs `ANTHROPIC_API_KEY`. Every command above needs
neither.

In a Codespace, both work without keys for a few runs: the calls go
through a proxy QuietFlare runs, which tells the two services apart by
your Codespace's own GitHub token and stops at a small allowance per
GitHub login. The page's badges say "via proxy" when that is the case.
Your incident text passes through that proxy on its way to the model;
it keeps who, when, and the token counts, never the text. To use your
own keys instead, set them as Codespace secrets before opening it, and
the proxy is never touched. If the allowance runs out, ask Seema for
more or set your own key.

## Bring your own run

The three settings from step 1 in your own folder, then your pipeline as
usual. After that, `clew extract nextflow --store .lineage --list-runs`
and the commands above with your paths. To see a run of yours on the page
in a Codespace, zip its `.lineage` folder on your machine and Browse to
the zip; it holds the record, which is small, and none of the data.
[Sources](sources.md) says what each engine records and what that limits.

## A run without lineage: the work tree

For a run that happened without the store, Clew reads the symlinks
Nextflow leaves in `work`, on any Nextflow version. It needs the run's
weblog, which the `nf-weblog` plugin posts as JSON events to a URL. The
old `-with-weblog` flag is gone from Nextflow. Start a receiver in a
second terminal; it appends each event to the file named:

```bash
mkdir -p ~/worktrial && python examples/weblog_receiver.py ~/worktrial/weblog.jsonl
```

In the first terminal, a config that turns the plugin on. A `plugins`
block in your config replaces the pipeline's own, so nf-core/demo's
`nf-schema` has to be listed again or the run fails at its first
function:

```bash
cat > ~/worktrial/weblog.config <<'EOF'
plugins {
    id 'nf-schema@2.7.2'
    id 'nf-weblog'
}
weblog {
    enabled = true
    url = 'http://127.0.0.1:8765'
}
EOF
```

```bash
cd ~/worktrial && nextflow run nf-core/demo -r 1.2.0 -profile test,docker -name symlinks --outdir results -c weblog.config
```

```bash
clew extract nextflow-work --jsonl ~/worktrial/weblog.jsonl --work ~/worktrial/work
```

Eight tasks, six edges resolved to a task: the FastQC archives that
MultiQC reads. Everything else is external: the raw reads, and the
report files the workflow itself writes for MultiQC, which no task
produced. Add `--json-out graph.json` and the commands above take
`--graph graph.json` in place of `--runs` and `--run`. Do this before
`nextflow clean`: it removes the symlinks, and a trail never captured
cannot be rebuilt. The trail has one gap the store does not: a file a
task passes on unchanged is staged from the original, so that hop is
not on disk. [Sources](sources.md) has the rest.
