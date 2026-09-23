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
