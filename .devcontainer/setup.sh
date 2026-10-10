#!/usr/bin/env bash
# Runs once when the container is created, inside the prebuild when there
# is one. Installs Clew from this checkout with the agent extra, a Nextflow
# that records content checksums in its lineage store, and two recorded
# runs to point Clew at. docs/try.md says what to do next.
set -euo pipefail

pip install -e '.[agent]'

curl -s https://get.nextflow.io | bash
sudo mv nextflow /usr/local/bin/nextflow

mkdir -p examples
curl -sL -o /tmp/clew-demo-runs.tgz \
  https://github.com/QuietFlare/clew/releases/download/v0.6.1/clew-demo-runs.tgz
tar -xzf /tmp/clew-demo-runs.tgz -C examples
rm /tmp/clew-demo-runs.tgz

echo
echo "Clew is installed. Open docs/try.md and start with:"
echo "  clew extract nextflow --store examples/demo-run/.lineage --list-runs"
