"""Suite adapters. Each module exposes one class implementing benchrun.runner.Suite."""

# User-defined docker network shared by the gateway and the harness
# containers, created once by whoever starts the gateway.
NETWORK = "bench-net"
# Named volume holding the Hugging Face cache of the harness datasets (about
# 9 GB for LiveCodeBench release_v6), filled on first use and reused by every
# run.
HF_CACHE_VOLUME = "bench-hf-cache"
