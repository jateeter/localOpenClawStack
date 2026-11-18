# OpenClaw's sandbox image, openclaw-sandbox:trixie-slim, which agents with
# sandbox.mode "all" run in. Vendored unchanged from OpenClaw's source,
# scripts/docker/sandbox/Dockerfile at v2026.11.5 (last changed in 3a4e7aefd,
# "build(docker): move images to Debian trixie"): the npm package names the
# image but does not ship this file. scripts/native-gateway.sh builds it when it
# is missing, and harden-config.sh names it in the config. Re-vendor it when
# OpenClaw changes it.
FROM debian:trixie-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f

ENV DEBIAN_FRONTEND=noninteractive

RUN --mount=type=cache,id=openclaw-sandbox-trixie-apt-cache,target=/var/cache/apt,sharing=locked \
  --mount=type=cache,id=openclaw-sandbox-trixie-apt-lists,target=/var/lib/apt,sharing=locked \
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
