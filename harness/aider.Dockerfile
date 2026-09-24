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
