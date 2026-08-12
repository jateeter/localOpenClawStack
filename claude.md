# localOpenClawStack Guidance

Last reviewed: 2026-08-09

See `/Users/johnt/workspace/GitHub/claude.md` for the integrated application map. Update both this file and the root map when OpenClaw versioning, gateway wiring, auth/bootstrap, or Manager/PE integration expectations change.

## Role

This repo provides the local OpenClaw ACP/xACP gateway and Open WebUI stack used by RealityEngine PE source mapping and agent activation tests.

## Codebase Map

- `openclaw/`: OpenClaw runtime configuration and local state.
- `openclaw/devices/`: device definitions.
- `openclaw/identity/`: identity/session material.
- `openclaw/logs/`: runtime logs.
- `scripts/`: start/stop/bootstrap and validation helpers.
- `machine-behaviors/agents/`: generated machine-behavior agent specs and `INDEX.json`.
- `machine-behaviors/agents/profiles/`: agent profiles — which subset of the corpus a deployment loads.
- `browser-config/`: browser/OpenWebUI runtime configuration.
- Compose files: local gateway, Open WebUI, and supporting containers.

## Key Commands

```bash
docker compose config
docker compose ps
./scripts/init-secrets.sh
./scripts/update-versions.sh
./scripts/start.sh
./scripts/start.sh --agent-profile=regression
./scripts/generate-regression-profile.py --check
./scripts/verify-deployment.sh
./scripts/stop.sh
```

Use the repo's actual scripts when present; Docker Compose state is time-sensitive and should be verified live.

## Runtime Contract

- OpenClaw gateway is expected at `http://localhost:18789`.
- WebUI is expected at `http://localhost:8080`.
- Published ports are loopback-only, and `.env` must carry immutable digest pins for the Node base, Open WebUI, and browser images.
- Release refresh is explicit through `update-versions.sh` or `start.sh --update`; ordinary startup consumes the existing pins without mutating versions.
- `start.sh` owns persisted gateway hardening, WebUI administrator synchronization, and live deployment verification. CI delegates to this entrypoint.
- RealityEngine PE tests should use `ACP_ENABLED=true`, gateway URL, session key, target agent, and `ACP_COMPLETION_SOURCE_MAPPING_ID=acp-openclaw-completion`.
- Agent loading is profile-selected. `--agent-profile=full` (the default) loads the whole 1320-agent corpus; `--agent-profile=regression` loads only the 12 agents bound to the RealityEngine regression machine corpus. `start.sh` resolves the profile once and hands the same index to the sync, the config verifier, and the live count gate.
- `machine-behaviors/agents/profiles/regression.txt` is generated from `RealityEngine_CI/config/standard-deployment-corpus.txt`, not hand-maintained. Re-run `./scripts/generate-regression-profile.py` when that corpus changes; `--check` is the drift guard and fails when the two disagree.
- Narrowing the profile prunes the previous profile's workspaces and agent directories. Switching back re-materializes them; the sibling repos are needed only to regenerate or check a profile, not to start the stack.
- Treat upstream version freshness as time-sensitive; re-check before claiming current release status.

## LSP Support

Use Docker/YAML/JSON language servers for compose and config, Bash language server for scripts, and markdown LSP for docs.

## Editing Rules

- Do not commit runtime state, logs, browser profiles, Open WebUI data, credentials, or generated tasks unless explicitly requested.
- Keep bootstrap behavior explicit about default credentials and gateway token requirements.
