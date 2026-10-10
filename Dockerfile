# Clew, ready to use: the page, the commands, and the two demo runs.
#
#   docker run -p 8770:8770 ghcr.io/quietflare/clew
#
# then open the link the container prints. Your own runs come in through
# Browse on the page, or mount a folder:  -v /path/to/launch:/runs/mine
# The agent needs ANTHROPIC_API_KEY:      -e ANTHROPIC_API_KEY
#
# Built and published by .github/workflows/container.yml on every release,
# and by hand from a checkout with:  docker build -t clew . && docker run -p 8770:8770 clew

FROM python:3.12-slim

# The two recorded nf-core/demo runs docs/try.md walks through.
ADD https://github.com/QuietFlare/clew/releases/download/v0.6.1/clew-demo-runs.tgz /tmp/demo-runs.tgz
RUN mkdir -p /runs && tar -xzf /tmp/demo-runs.tgz -C /runs && rm /tmp/demo-runs.tgz

COPY . /src
RUN pip install --no-cache-dir "/src[agent]" && rm -rf /src

# The page keeps what it is given, uploads and the agent's records, here.
VOLUME /home/clew
WORKDIR /runs
EXPOSE 8770
CMD ["clew", "ui", "--bind", "0.0.0.0", "--no-browser", "--port", "8770", "--home", "/home/clew"]
