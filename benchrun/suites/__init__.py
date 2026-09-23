"""Suite adapters. Each module exposes one class implementing benchrun.runner.Suite."""

# User-defined docker network shared by the gateway and the harness
# containers, created once by whoever starts the gateway.
NETWORK = "bench-net"
