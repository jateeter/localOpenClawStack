# localOpenClawStack Guidance

Last reviewed: 2026-08-26

See `/Users/johnt/workspace/GitHub/CLAUDE.md` for the integrated application map. Update both this file and the root map when OpenClaw versioning, gateway wiring, auth/bootstrap, or Manager/PE integration expectations change.

## Role

This repo provides the local OpenClaw ACP/xACP gateway and Open WebUI stack used by RealityEngine PE source mapping and agent activation tests.

## Codebase Map

- `openclaw/`: OpenClaw runtime configuration and local state.
- `openclaw/devices/`: device definitions.
- `openclaw/identity/`: identity/session material.
- `openclaw/logs/`: runtime logs.
- `scripts/`: start/stop/bootstrap and validation helpers.
- `machine-behaviors/agents/`: generated input-analyst agent specs, one per corpus machine except the five arbitration conformance fixtures, plus `INDEX.json` (whose `provenance` records the corpus fingerprint they were derived from). Regenerate with `python3 machine-behaviors/materialize_agents.py --fresh` whenever the machine corpus changes; see `machine-behaviors/OC_AGENT_TEMPLATE.md` §8.
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
- Agent loading is profile-selected. `--agent-profile=full` (the default) loads the whole 1323-agent corpus; `--agent-profile=regression` loads only the 12 agents bound to the RealityEngine regression machine corpus. `start.sh` resolves the profile once and hands the same index to the sync, the config verifier, and the live count gate.
- `machine-behaviors/agents/profiles/regression.txt` is generated from `RealityEngine_CI/config/standard-deployment-corpus.txt`, not hand-maintained. Re-run `./scripts/generate-regression-profile.py` when that corpus changes; `--check` is the drift guard and fails when the two disagree.
- Narrowing the profile prunes the previous profile's workspaces and agent directories. Switching back re-materializes them; the sibling repos are needed only to regenerate or check a profile, not to start the stack.
- The gateway auto-restores `openclaw.json` from `openclaw.json.bak` when a read looks like a clobber, and a narrowed profile is a >50% size drop. `scripts/adopt-config-baseline.sh` moves that guard's baseline (`.bak`, `.last-good`, and `openclaw/logs/config-health.json`) onto each deliberate config rewrite so the profile survives the restart; `sync-machine-agents.sh` calls it. Anything else that rewrites `openclaw/openclaw.json` must call it too.
- Treat upstream version freshness as time-sensitive; re-check before claiming current release status.

## LSP Support

Use Docker/YAML/JSON language servers for compose and config, Bash language server for scripts, and markdown LSP for docs.

## Editing Rules

- Do not commit runtime state, logs, browser profiles, Open WebUI data, credentials, or generated tasks unless explicitly requested.
- Keep bootstrap behavior explicit about default credentials and gateway token requirements.

## Standing rules — authoritative in `../RealityEngine_CI/docs/ENGINEERING_CONTRACT.md`

These apply here and are **not** restated in this file. They were previously
copied into eighteen `CLAUDE.md` files across six repositories, which is the
duplication problem the rules themselves warn about: copies drift, a rule added
to one applies only where someone looked, and with no authority a reader cannot
tell which copy is current.

| Rule | In short |
| --- | --- |
| Qualify every "registry" | Never the bare word — instance / machine / cesgen / arbitration / domain / semantic-bus / tag. |
| Verify a merge beyond the hosted checks | A green PR is not a verified PR; the hosted path cannot reach the integration points. Name what you could not exercise, and record what you noticed but did not chase. |
| Never commit to main | Branch from `origin/main`, PR, verify, squash-merge, clean up. |
| _CI is the authority | Peripheral repos keep minimal CI that forces local validation; RealityEngine_CI verifies fixes against a live universe. Check its `docs/` before adding CI anywhere else. |
| Use bash, not zsh | Shell work runs in `/opt/homebrew/bin/bash` (5.x), not zsh or macOS `/bin/bash` 3.2: any loop, unquoted variable, glob or `set --` goes through it with `set -euo pipefail`, and you check the command's exit status, not the pipeline tail. |

Read the contract for the full text, the qualifier table, and the cleanup steps.
