#!/usr/bin/env bash
# Runs once when the Codespace is created, on top of the image that
# .github/workflows/devcontainer-image.yml built: Java, Docker, Nextflow and
# the two recorded runs are already there. What is left is this checkout,
# installed editable so edits run at once, and the runs linked to where
# docs/try.md looks for them.
set -euo pipefail

pip install -e '.[agent]'

mkdir -p examples
ln -sfn /opt/clew/demo-run examples/demo-run

echo
echo "Clew is installed. Open docs/try.md and start with:"
echo "  clew extract nextflow --store examples/demo-run/.lineage --list-runs"
