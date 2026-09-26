FROM python:3.11.10-slim
ARG LCB_SHA
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/livecodebench/livecodebench /lcb && cd /lcb && git checkout ${LCB_SHA}
# --no-deps: the pinned pyproject pulls torch and vllm, never imported on the
# codegeneration + evaluate path of an OpenAI-compatible alias. The second
# line installs only what that path imports, read from the pinned source.
# anthropic stays at 0.42.0: lcb_runner/prompts imports HUMAN_PROMPT and
# AI_PROMPT, gone from later releases (ImportError on 1.8.0).
# datasets stays at 3.2.0: the dataset is a loading script, which
# datasets 4.0 dropped.
RUN pip install --no-cache-dir --no-deps -e /lcb \
    && pip install --no-cache-dir "openai>=1.59.6" "datasets==3.2.0" "pebble>=5.1.0" "anthropic==0.42.0" attrs tqdm numpy pandas
# torch is imported but never needed: see lcb_stub_torch/torch/__init__.py.
COPY lcb_stub_torch /lcb_stub_torch
ENV PYTHONPATH=/lcb_stub_torch
COPY lcb_run.py /lcb_run.py
WORKDIR /lcb
# Non-root (trivy DS-0002). Two real write targets, read from the pinned
# source (lcb_runner/utils/path_utils.py at LCB_SHA), neither overridable by
# an argument: get_output_path() writes "output/<model>/..." (bind-mounted
# by benchrun.suites.lcb at /lcb/output, covered by chowning WORKDIR), but
# get_cache_path() ALSO writes a sibling "cache/<model>/..." under the same
# relative root, never bind-mounted, so /lcb itself (not just its output
# subtree) has to be owned by the runtime user. HOME stays /root on
# purpose: benchrun.suites.lcb bind-mounts the shared HF dataset cache at
# the fixed path "/root/.cache/huggingface" (not something this Dockerfile
# can change), and huggingface_hub's own cache resolution keys off $HOME,
# so a non-root user with a different HOME would silently stop sharing that
# cache rather than fail loudly.
RUN useradd --uid 1000 --no-create-home --home-dir /root --shell /usr/sbin/nologin bench \
    && mkdir -p /root/.cache/huggingface \
    && chown -R bench:bench /lcb /root
USER bench
