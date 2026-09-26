FROM python:3.11.10-slim
ARG RULER_SHA
ARG RULER_ESSAY_CORPUS_SHA256
ARG RULER_SQUAD_URL
ARG RULER_SQUAD_SHA256
ARG RULER_HOTPOTQA_URL
ARG RULER_HOTPOTQA_SHA256
RUN apt-get update && apt-get install -y --no-install-recommends git git-lfs wget && rm -rf /var/lib/apt/lists/*
# json/english_words.json (cwe task) is a git-lfs pointer in the pinned repo;
# a plain clone leaves the 132-byte pointer file in place instead of its
# real 8.5 MB content (found live, cwe failed with a JSONDecodeError on the
# pointer text). git-lfs install registers the smudge filter before the
# clone pulls the real blob.
RUN git lfs install --skip-repo \
    && git clone https://github.com/NVIDIA/RULER /ruler && cd /ruler && git checkout ${RULER_SHA} \
    && git lfs pull
# Real deps of the tasks this suite runs (niah, variable_tracking,
# common_words_extraction, freq_words_extraction, qa) plus the openai client
# path and the essay/QA data assembly, read from every import at RULER_SHA
# (bench/harness/README.md). NOT installed: nemo-toolkit[all], tritonclient,
# transformer_engine, vllm, accelerate, transformers (docker/requirements.txt):
# the local llama-server path never touches any of them, and nemo-toolkit is
# replaced by ruler_stub_nemo, a five-line stand-in for the two JSONL helpers
# pred/call_api.py and eval/evaluate.py import from it.
# NLTK_DATA fixed to a world-readable, system-wide path (not nltk's own
# default, ~/nltk_data): scripts/data/prepare.py (RULER_SHA) does
# "nltk.data.find('tokenizers/punkt')" first and only calls
# nltk.download(...) itself if that lookup fails, so a non-root runtime user
# whose HOME differs from the user that downloaded it at build time (root,
# here) would silently miss the build-time download and try (and likely
# fail, no writable default download dir) a fresh one at run time, on every
# call, with no model server involved yet. Setting NLTK_DATA here makes the
# find() succeed for any runtime user, root or not.
ENV NLTK_DATA=/usr/local/share/nltk_data
RUN pip install --no-cache-dir \
    openai tiktoken tenacity pyyaml numpy pandas scipy tqdm nltk wonderwords html2text beautifulsoup4 \
    # nltk.downloader.Downloader.default_download_dir() only considers a
    # candidate from nltk.data.path (NLTK_DATA included) when it ALREADY
    # EXISTS on disk (proven live: the env var alone left the directory
    # never created, and download() silently fell back to root's own
    # ~/nltk_data, invisible to any other user); the mkdir below is what
    # actually makes nltk.download() pick NLTK_DATA over that fallback.
    && mkdir -p /usr/local/share/nltk_data \
    && python -c "import nltk; nltk.download('punkt'); nltk.download('punkt_tab')"
# All 13 canonical RULER tasks (bench/benchrun/suites/ruler.py DEFAULT_TASKS).
# Essay corpus: assembled at build time by the pinned repo's own
# download_paulgraham_essay.py, unmodified (rule 3), from the 218 URLs listed
# in its own scripts/data/synthetic/json/PaulGrahamEssays_URLs.txt (fixed by
# RULER_SHA). No single URL exists for the assembled corpus, so its sha256 is
# verified against the pin instead (bench/harness/README.md); the build fails
# if any of the 218 pages served different bytes than the pinned measurement.
RUN cd /ruler/scripts/data/synthetic/json \
    && python download_paulgraham_essay.py \
    && echo "${RULER_ESSAY_CORPUS_SHA256}  PaulGrahamEssays.json" | sha256sum -c -
# QA datasets, each a single pinned URL and sha256 (download_qa_dataset.sh,
# RULER_SHA): hotpotqa's primary URL (curtis.ml.cmu.edu) has no version pin,
# so this uses the same commit-pinned Hugging Face mirror the pinned script
# itself falls back to.
RUN cd /ruler/scripts/data/synthetic/json \
    && wget -q -O squad.json "${RULER_SQUAD_URL}" \
    && echo "${RULER_SQUAD_SHA256}  squad.json" | sha256sum -c - \
    && wget -q -O hotpotqa.json "${RULER_HOTPOTQA_URL}" \
    && echo "${RULER_HOTPOTQA_SHA256}  hotpotqa.json" | sha256sum -c -
COPY ruler_stub_nemo /ruler_stub_nemo
ENV PYTHONPATH=/ruler_stub_nemo
COPY ruler_run.py /ruler_run.py
WORKDIR /ruler/scripts
# Non-root (trivy DS-0002). Every write this launcher and the pinned
# call_api.py/prepare.py make (read at RULER_SHA) goes through an absolute
# --data-dir/--save-dir, always the bind-mounted /out (benchrun.suites.ruler);
# NLTK_DATA above already removes the one HOME-dependent read. Nothing else
# on this image's own filesystem is touched at runtime.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin bench
USER bench
