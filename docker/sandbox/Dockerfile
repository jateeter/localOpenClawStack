# OpenClaw's sandbox image, openclaw-sandbox:bookworm-slim, which agents with
# sandbox.mode "all" run in. Vendored unchanged from OpenClaw's source,
# scripts/docker/sandbox/Dockerfile at 157d8c2 (the 2026.10.7 release): the npm
# package names the image but does not ship this file. scripts/native-gateway.sh
# builds it when it is missing. Re-vendor it when the pinned release changes.
FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251

ENV DEBIAN_FRONTEND=noninteractive

RUN --mount=type=cache,id=openclaw-sandbox-bookworm-apt-cache,target=/var/cache/apt,sharing=locked \
  --mount=type=cache,id=openclaw-sandbox-bookworm-apt-lists,target=/var/lib/apt,sharing=locked \
  apt-get update \
  && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    git \
    jq \
    python3 \
    ripgrep

RUN useradd --create-home --shell /bin/bash sandbox
USER sandbox
WORKDIR /home/sandbox

CMD ["sleep", "infinity"]
