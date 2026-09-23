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
