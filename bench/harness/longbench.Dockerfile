FROM python:3.11.10-slim
ARG LONGBENCH_SHA
ARG LONGBENCH_V2_DATA_URL
ARG LONGBENCH_V2_DATA_SHA256
RUN apt-get update && apt-get install -y --no-install-recommends git wget && rm -rf /var/lib/apt/lists/*
# THUDM/LongBench (MIT), pinned commit: only prompts/0shot.txt is read at run
# time (bench/harness/longbench_run.py, no fork of pred.py/result.py: their
# own top-level code has side effects that would run before any patch point
# exists, see the launcher's docstring). pred.py, result.py and LICENSE are
# kept in the image unmodified for provenance even though not executed.
RUN git clone https://github.com/THUDM/LongBench /longbench && cd /longbench && git checkout ${LONGBENCH_SHA}
RUN pip install --no-cache-dir openai tiktoken
# LongBench v2 data (zai-org/LongBench-v2 on Hugging Face, Apache-2.0),
# baked at build time and hash-verified: 503 real multiple-choice items,
# contexts from 8k to 2M words (bench/harness/README.md).
RUN wget -q -O /longbench_v2_data.json "${LONGBENCH_V2_DATA_URL}" \
    && echo "${LONGBENCH_V2_DATA_SHA256}  /longbench_v2_data.json" | sha256sum -c -
COPY longbench_run.py /longbench_run.py
WORKDIR /longbench
