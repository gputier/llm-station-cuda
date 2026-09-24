FROM python:3.11.10-slim
ARG LIVEBENCH_SHA
RUN apt-get update && apt-get install -y --no-install-recommends git build-essential && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/livebench/livebench /livebench \
    && cd /livebench && git checkout ${LIVEBENCH_SHA}
WORKDIR /livebench
RUN pip install --no-cache-dir -e .
# Client-side timeout/retry fix (bench/harness/README.md, no fork of the
# pinned repo): auto-imported by the interpreter at every process start.
COPY livebench_sitecustomize /livebench_sitecustomize
ENV PYTHONPATH=/livebench_sitecustomize
# Question-id selector for the mini/medium/large bench levels
# (benchrun.suites.livebench._select_question_ids). Baked in like every
# other launcher (lcb_run.py, bfcl_run.py, ...): a runtime bind mount would
# need a HOST path, but runner-99/runner-97 call "docker run" over the
# mounted host socket, resolved by the HOST daemon against the runner
# container's OWN filesystem view (/bench/..., orchestration section of
# this README). A -v source built from that path fails with exit 125,
# "invalid mount config", proven live on the 99, 2026-09-24.
COPY livebench_select_ids.py /select_ids.py
