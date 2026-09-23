FROM python:3.12.7-slim
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /bench
# Dependencies come from pyproject.toml alone. The package itself is installed
# editable against a stub: the real source is bind-mounted at /bench at run time
# (tests and runners alike), so no source is baked into the image.
COPY pyproject.toml .
RUN mkdir benchrun && touch benchrun/__init__.py \
    && pip install --no-cache-dir -e ".[dev]" \
    && rm -rf benchrun
