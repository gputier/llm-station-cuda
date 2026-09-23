FROM python:3.11.10-slim
ARG LIVEBENCH_SHA
RUN apt-get update && apt-get install -y --no-install-recommends git build-essential && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/livebench/livebench /livebench \
    && cd /livebench && git checkout ${LIVEBENCH_SHA}
WORKDIR /livebench
RUN pip install --no-cache-dir -e .
