FROM python:3.12.7-slim
ARG DOCKER_CLI_URL
ARG DOCKER_CLI_SHA256
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client git curl \
    && rm -rf /var/lib/apt/lists/*
# Docker client only (no dockerd/containerd/runc): runner-99/runner-97
# (harness/compose.yaml) run the campaign themselves and call "docker run" to
# start each suite's harness container, over the host daemon's socket
# (mounted at /var/run/docker.sock). No fork of a Docker-published image:
# just the official static client tarball, pinned by URL and sha256
# (harness/pins.env, Task 16), one binary extracted.
RUN curl -fsSL -o /tmp/docker-cli.tgz "${DOCKER_CLI_URL}" \
    && echo "${DOCKER_CLI_SHA256}  /tmp/docker-cli.tgz" | sha256sum -c - \
    && tar -xzf /tmp/docker-cli.tgz -C /usr/local/bin --strip-components=1 docker/docker \
    && rm /tmp/docker-cli.tgz
WORKDIR /bench
# Dependencies come from pyproject.toml alone. The package itself is installed
# editable against a stub: the real source is bind-mounted at /bench at run time
# (tests and runners alike), so no source is baked into the image.
COPY pyproject.toml .
RUN mkdir benchrun && touch benchrun/__init__.py \
    && pip install --no-cache-dir -e ".[dev]" \
    && rm -rf benchrun
