# Codex Guidance: localOpenClawStack

Read `claude.md` for the current codebase map and OpenClaw integration context.

## Role

This repo provides the local OpenClaw ACP/xACP gateway and Open WebUI stack used by RealityEngine PE source mapping and agent activation tests.

## Development Rules

- Treat gateway URL, token/session settings, target agent, and image versions as runtime-sensitive.
- Keep `ACP_COMPLETION_SOURCE_MAPPING_ID=acp-openclaw-completion` aligned with Manager and engine PE tests.
- Re-check upstream/current version status before making release freshness claims.
- Do not commit credentials, runtime state, logs, browser profiles, Open WebUI data, or generated tasks unless explicitly requested.

## Bug Triage

- Validate in layers: Docker Compose config, container health, gateway `/healthz`, `/v1/models`, PE adapter dispatch, dispatch ledger, and PE source activation.
- If gateway works but PE source activation fails, inspect adapter payloads and mapping IDs before changing stack scripts.
- If WebUI auth is involved, distinguish persisted account state from known plaintext credentials.

## Verification

Common commands:

```bash
docker compose config
docker compose ps
curl -sf http://localhost:18789/healthz
curl -sf http://localhost:18789/v1/models
```

## Artifact Hygiene

`openclaw/` is mostly runtime state. Only documentation files such as `openclaw/claude.md` should be force-added when explicitly intended.

