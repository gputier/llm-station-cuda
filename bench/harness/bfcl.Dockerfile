FROM python:3.11.10-slim
ARG BFCL_SHA
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/ShishirPatil/gorilla /gorilla \
    && cd /gorilla && git checkout ${BFCL_SHA}
WORKDIR /gorilla/berkeley-function-call-leaderboard
# The pinned dependency list is installed as declared, minus sentence-transformers
# and faiss-cpu: they only serve the memory and web-search categories and pull
# torch and CUDA wheels (about 5 GB) never imported on the FC generate and
# evaluate path. soundfile is added: every handler module is imported at load
# time, and qwen_agent imports soundfile without declaring it.
RUN pip install --no-cache-dir --no-deps -e . \
    && python -c "import tomllib; deps = tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']; print('\n'.join(d for d in deps if not d.startswith(('sentence-transformers', 'faiss-cpu'))))" > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt soundfile \
    && rm /tmp/requirements.txt
COPY bfcl_run.py /bfcl_run.py
# Non-root (trivy DS-0002). bfcl_run.py's --limit path (mini/medium/large,
# benchrun.suites.bfcl) writes bfcl_eval's own TEST_IDS_TO_GENERATE_PATH,
# a fixed path INSIDE the pinned package tree
# (bfcl_eval.constants.eval_config, read live: "/gorilla/berkeley-function-
# call-leaderboard/test_case_ids_to_generate.json"), not something the
# launcher can point elsewhere (rule 3, no fork). RESULT_PATH/SCORE_PATH
# default into the same tree too, but the suite adapter always passes its
# own --result-dir/--score-dir (the bind-mounted /out), so only the WORKDIR
# itself needs to be writable, not a second, separate location.
RUN useradd --create-home --shell /usr/sbin/nologin bench \
    && chown -R bench:bench /gorilla
USER bench
