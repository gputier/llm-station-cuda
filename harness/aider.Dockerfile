FROM python:3.11.10-slim
ARG AIDER_SHA
ARG POLYGLOT_SHA
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates git build-essential golang default-jdk rustc cargo cmake \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*
# The polyglot-benchmark JavaScript exercises run through benchmark/npm-test.sh,
# which symlinks node_modules from /npm-install: without this the JS unit
# tests fail to find jest, not because the model's code is wrong.
RUN mkdir -p /npm-install && cd /npm-install && npm init -y && npm install \
    jest \
    @babel/core@7.25.2 \
    @exercism/babel-preset-javascript@0.2.1 \
    @exercism/eslint-config-javascript@0.6.0 \
    @types/jest@29.5.12 \
    @types/node@20.12.12 \
    babel-jest@29.6.4 \
    core-js@3.37.1 \
    eslint@8.49.0
RUN git clone https://github.com/Aider-AI/aider /aider && cd /aider && git checkout ${AIDER_SHA} && pip install --no-cache-dir -e . -r requirements/requirements-dev.txt
RUN git clone https://github.com/Aider-AI/polyglot-benchmark /polyglot && cd /polyglot && git checkout ${POLYGLOT_SHA}
# Client-side timeout/retry fix (bench/harness/README.md, no fork of the
# pinned repo): auto-imported by the interpreter at every process start.
COPY aider_sitecustomize /aider_sitecustomize
ENV PYTHONPATH=/aider_sitecustomize
WORKDIR /aider
# Non-root (trivy DS-0002). The pinned aider itself, not just benchmark.py,
# touches the home directory on every invocation regardless of the
# --output/--keywords flags benchmark.py passes: aider/versioncheck.py's
# check_version() unconditionally mkdir+touch's
# ~/.aider/caches/versioncheck in its own "finally" block (not guarded by
# the try above it), and aider/analytics.py writes ~/.aider/analytics.json
# the first time telemetry is recorded; both read at AIDER_SHA. A non-root
# user therefore needs an OWNED home directory, not just write access to
# the bind-mounted results dir benchmark.py itself uses.
#
# benchmark.py's own main() also opens the /aider clone as a GitPython repo
# (git.Repo(search_parent_directories=True), cwd /aider) to read its commit
# hash for the results file: git itself refuses to touch a repository owned
# by a different uid ("dubious ownership", proven live switching to a
# non-root user before this chown existed), so /aider's ownership has to
# move to the same user that runs it, not just get a safe.directory
# exception (which only silences the check, still leaves a root-owned tree
# a non-root process cannot otherwise write to if aider itself ever does).
RUN useradd --create-home --shell /usr/sbin/nologin bench \
    && chown -R bench:bench /aider
USER bench
