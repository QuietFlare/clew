# The Codespace image, built by .github/workflows/devcontainer-image.yml and
# pulled by .devcontainer/devcontainer.json, so a new Codespace opens in the
# time of one image pull instead of installing Java, Docker, Nextflow and
# the demo runs on every first open.
#
# Plain python:3.12-slim underneath, not Microsoft's devcontainer image: that
# one carries half a gigabyte of toolchain nothing here uses. What a
# Codespace needs of it is a user with sudo, git, curl and the C++ runtime
# the editor's server links against, added below.
#
# Docker CE comes from the docker-in-docker feature, applied on top of this
# file by the build (see .github/devcontainer-image/devcontainer.json,
# kept out of this folder so Codespaces does not offer it as a configuration).
# Java is installed here because Nextflow needs it to finish its own install.

FROM python:3.12-slim

ENV NXF_VER=26.09.2-edge
ENV NXF_CACHE_MODE=DEEP
# Where Nextflow keeps plugins it fetches; writable by the vscode user.
ENV NXF_HOME=/opt/nextflow

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git sudo curl ca-certificates procps libstdc++6 openjdk-21-jre-headless \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --shell /bin/bash vscode \
 && echo "vscode ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/vscode

# Nextflow's self-contained build: the launcher with its libraries embedded,
# so nothing is fetched on first use. NXF_HOME is where it keeps plugins.
RUN curl -fsSL -o /usr/local/bin/nextflow \
      "https://github.com/nextflow-io/nextflow/releases/download/v${NXF_VER}/nextflow-${NXF_VER}-dist" \
 && chmod 755 /usr/local/bin/nextflow \
 && mkdir -p /opt/nextflow && chown vscode:vscode /opt/nextflow

# The two recorded nf-core/demo runs. setup.sh links them into the checkout.
ADD https://github.com/QuietFlare/clew/releases/download/v0.6.1/clew-demo-runs.tgz /tmp/demo-runs.tgz
RUN mkdir -p /opt/clew && tar -xzf /tmp/demo-runs.tgz -C /opt/clew && rm /tmp/demo-runs.tgz \
 && chown -R vscode:vscode /opt/clew

# Clew's own dependencies for the agent extra, so the editable install of
# the checkout at open time has nothing left to download.
RUN pip install --no-cache-dir "mainsheet>=0.1"

USER vscode
WORKDIR /home/vscode
# Prove it runs as the user who will run it, with nothing left to download.
RUN nextflow -version
