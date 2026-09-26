# Agentic harness image: Claude Code driven against the gateway, no other
# egress during the agent phase. The container only ever talks to gw-t<N> on
# a dedicated internal network during that phase; it never ships credentials
# for a real Anthropic account (ANTHROPIC_API_KEY=dummy).
#
# Base is Ruby, not Node: Asolta-Public-Cloud's late-2026 gems (grpc,
# sqlite3) now require Ruby >= 3.2 in their newest releases, and Debian
# bookworm's own `ruby-full` package ships Ruby 3.1.2, which a real bundle
# install on a scheduler task failed against, proven live (grpc and sqlite3
# both refused to resolve). Node is added on top via NodeSource, the same
# pattern bench/harness/aider.Dockerfile already uses for its own Node need.
FROM ruby:3.2-bookworm
# Test runners of the task set's four languages: Ruby (Asolta-Public-Cloud,
# RSpec, base image), Node/TypeScript (WorkingTeam, vitest), Go (WatchMe, go
# test), Rust (mail-imap-mcp, cargo test). git lets Claude Code inspect
# history inside the task's one-commit repo (git blame, git log) even though
# no fix commit is reachable there. python3 has no target repository written
# in it: it drives benchrun.tasks.builder (bind-mounted from /bench, see
# bench/scripts) to both constitute and re-verify the task set on the same
# toolchain that runs its tests, one image instead of two.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl ca-certificates git python3 \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*
# Debian bookworm's `golang` package is go1.19: WatchMe's go.mod declares
# `go 1.26.0`, which go1.19 refuses to build against, proven live ("go.mod
# requires go >= 1.26.0"). The official binary tarball is used instead of
# apt, same reasoning as the Ruby base image above.
RUN goarch=$(dpkg --print-architecture | sed 's/amd64/amd64/;s/arm64/arm64/') \
    && curl -fsSL "https://go.dev/dl/go1.27.1.linux-${goarch}.tar.gz" -o /tmp/go.tar.gz \
    && tar -C /usr/local -xzf /tmp/go.tar.gz && rm /tmp/go.tar.gz
ENV PATH="/usr/local/go/bin:${PATH}"
# RUSTUP_HOME/CARGO_HOME outside /root: root's home defaults to 0700, so the
# non-root "agent" user below could not reach a rustup install left at
# /root/.cargo (proven live: "cargo: command not found" with the right PATH,
# the binary simply unreadable). /usr/local/rustup and /usr/local/cargo is
# the same layout the official rust image uses for this reason.
ENV RUSTUP_HOME=/usr/local/rustup CARGO_HOME=/usr/local/cargo
ENV PATH="/usr/local/cargo/bin:${PATH}"
RUN curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable
# Pinned 2026-09-22 (npm registry, dist-tags.latest at build time): a moving
# "latest" would change the harness under every other suite silently.
# Read from pins.env (CLAUDE_CODE_VERSION), passed as --build-arg the same
# way harness/bfcl.Dockerfile and the other pinned harnesses take their own
# SHA: pins.env is the one source of truth, not a second copy in the
# Dockerfile itself.
ARG CLAUDE_CODE_VERSION
RUN test -n "$CLAUDE_CODE_VERSION" && npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}
# corepack enable symlinks pnpm/yarn into /usr/bin: done here, as root, once,
# so a task's non-root prepare_cmd never needs to (proven live: "EACCES"
# trying to symlink into /usr/bin as the non-root "agent" user).
RUN corepack enable
# Claude Code refuses --permission-mode bypassPermissions as root/sudo
# ("--dangerously-skip-permissions cannot be used with root/sudo privileges
# for security reasons"), proven live: a dedicated non-root user runs it.
# CARGO_HOME needs to be WRITABLE by that user too, not just readable: cargo
# fetch caches downloaded crates there, proven live ("Permission denied"
# creating the registry cache dir under a read-only chmod).
RUN useradd --create-home --shell /bin/bash agent \
    && chown -R agent:agent /usr/local/rustup /usr/local/cargo
WORKDIR /repo
RUN chown agent:agent /repo
# Every "docker run" that starts this image already passes "--user agent"
# (benchrun.suites.agentic, both the private-task and the swe/ container):
# this USER only sets the default a bare "docker run bench-agent ..." would
# get, and closes trivy's DS-0002 (a Dockerfile must name a non-root user of
# its own, the explicit --user flag at run time does not satisfy the static
# check). No prepare_cmd, agent phase or test_cmd in this suite ever needs
# root: HOME=/home/agent is passed alongside --user agent at every call site.
USER agent
