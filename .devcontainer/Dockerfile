# The Codespace image, built by .github/workflows/devcontainer-image.yml and
# pulled by .devcontainer/devcontainer.json, so a new Codespace opens in the
# time of one image pull instead of installing Java, Docker, Nextflow and
# the demo runs on every first open.
#
# Docker CE comes from the docker-in-docker feature, applied on top of this
# file by the build (see image/devcontainer.json). Java is installed here
# because Nextflow needs it to finish its own install.

FROM mcr.microsoft.com/devcontainers/python:3.12

ENV NXF_VER=26.09.2-edge
ENV NXF_CACHE_MODE=DEEP

RUN apt-get update \
 && apt-get install -y --no-install-recommends openjdk-21-jre-headless \
 && rm -rf /var/lib/apt/lists/*

# Nextflow, with its dependencies fetched now, not on first use.
RUN curl -s https://get.nextflow.io | bash \
 && mv nextflow /usr/local/bin/nextflow \
 && chmod 755 /usr/local/bin/nextflow \
 && nextflow -version

# The two recorded nf-core/demo runs. setup.sh links them into the checkout.
ADD https://github.com/QuietFlare/clew/releases/download/v0.6.1/clew-demo-runs.tgz /tmp/demo-runs.tgz
RUN mkdir -p /opt/clew && tar -xzf /tmp/demo-runs.tgz -C /opt/clew && rm /tmp/demo-runs.tgz \
 && chown -R vscode:vscode /opt/clew

# Clew's own dependencies for the agent extra, so the editable install of
# the checkout at open time has nothing left to download.
RUN pip install --no-cache-dir "mainsheet>=0.1"
