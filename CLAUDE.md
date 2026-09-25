# localOpenClawStack Guidance

Last reviewed: 2026-09-25

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
- Agent loading is profile-selected. `--agent-profile=full` (the default) loads the whole 1323-agent corpus; `--agent-profile=regression` loads the **15** agents of the regression-test corpus (see "Which agent corpus matters" below). `start.sh` resolves the profile once and hands the same index to the sync, the config verifier, and the live count gate.
- `machine-behaviors/agents/profiles/regression.txt` is generated from `RealityEngine_CI/config/regression-corpus.txt`, not hand-maintained: 21 entries, giving 15 agents, with 3 arbitration fixtures and 3 localAIStack machines excluded by rule and named in the file's header. Re-run `./scripts/generate-regression-profile.py` when that corpus changes; `--check` is the drift guard and fails when the two disagree. `RealityEngine_Machines/tests/contracts/openclaw_profile_drift_test.py` runs it on every corpus change.
- Narrowing the profile prunes the previous profile's workspaces and agent directories. Switching back re-materializes them; the sibling repos are needed only to regenerate or check a profile, not to start the stack.
- The gateway auto-restores `openclaw.json` from `openclaw.json.bak` when a read looks like a clobber, and a narrowed profile is a >50% size drop. `scripts/adopt-config-baseline.sh` moves that guard's baseline (`.bak`, `.last-good`, and `openclaw/logs/config-health.json`) onto each deliberate config rewrite so the profile survives the restart; `sync-machine-agents.sh` calls it. Anything else that rewrites `openclaw/openclaw.json` must call it too.
- Treat upstream version freshness as time-sensitive; re-check before claiming current release status.

## Which agent corpus matters

**Decided 2026-09-25. Do not re-derive it.**

- **The regression-test corpus is the corpus of interest.** It is `RealityEngine_CI/config/regression-corpus.txt`, the corpus the regression lanes boot, and its agents must be current on every regression run. Of its 21 entries, **15 carry an agent**. The other 6 are 3 arbitration conformance fixtures, agent-free by rule (`RealityEngine_Machines/docs/CORPUS_EXIT_CRITERIA.md` §3.3), and 3 localAIStack machines (`rag_corrective_cycle`, `session_rag_context`, `session_agent_context`) that OpenClaw does not cover.
- **The full agent corpus (1,323) is constructed once per week**, as part of the full-corpus regression test. It is not rebuilt per change, and a change is not verified against it. Construct it with `python3 machine-behaviors/materialize_agents.py --fresh` and verify it with `RealityEngine_CI/scripts/check-corpus-exit-criteria.py --machines ../RealityEngine_Machines --openclaw .`.
- **Current against *which* corpus.** `agents/INDEX.json` → `provenance.corpus.digest` names the corpus the agents were derived from. It is the fingerprint the released OWL baselines carry. When the digest does not match the machine corpus, the agents are stale, even if every count gate passes.

**Not enforced yet: RealityEngine_CI#467.** No gate detects a stale agent corpus:
- The exit-criteria check tests counts, joins and axis names, not actions or RAG.
- `materialize_agents.py` has no `--check`.
- ~~The regression profile derives from the 12-machine standard-deployment list~~ **Fixed 2026-09-25:** it derives from `regression-corpus.txt` and loads all 15 agents, including `fall-detection`, `rs-ring-latch-stage-a` and `rs-ring-latch-stage-b`.
- ~~The full-corpus job runs every 5 days and does not build agents~~ **Fixed 2026-09-25 (RealityEngine_CI#468):** `full-corpus-cycle.yml` runs weekly (Sunday 05:00 UTC) and rebuilds all 1,323 agents, failing if the committed specs differ. Stale agents are therefore caught within a week; a per-run check is what #467 still asks for.

Because of this, the agent corpus sat five weeks behind the machine corpus, through the #154 action changes, with every check green. Until #467 lands, regenerate by hand after any corpus change that touches a regression machine, and say in the PR that you did.

## LSP Support

Use Docker/YAML/JSON language servers for compose and config, Bash language server for scripts, and markdown LSP for docs.

## Editing Rules

- Do not commit runtime state, logs, browser profiles, Open WebUI data, credentials, or generated tasks unless explicitly requested.
- Keep bootstrap behavior explicit about default credentials and gateway token requirements.

## Standing rules — authoritative in `../RealityEngine_CI/docs/ENGINEERING_CONTRACT.md`

These apply here and are **not** restated in this file. The table is an index
to the contract, not a copy of it: it names every rule so you know what to look
up, and the contract's wording governs wherever the two differ.

| Rule | In short |
| --- | --- |
| Qualify every "registry" | Never the bare word — instance / machine / cesgen / arbitration / domain / semantic-bus / tag. |
| Regenerate a stale `<name>` registry, don't fail it | Each `<name>` registry is a view of the running system. A gate regenerates it and fails only on a disagreement that survives regeneration. |
| Verify a merge beyond the hosted checks | A green PR is not a verified PR; the hosted path cannot reach the integration points. Name what you could not exercise, and record what you noticed but did not chase. |
| _CI is the authority | Peripheral repos keep minimal CI that forces local validation; RealityEngine_CI verifies fixes against a live universe. Check its `docs/` before adding CI anywhere else. |
| Name it `CLAUDE.md` | Uppercase, always. On a case-insensitive filesystem `claude.md` is the same inode; dedupe on `st_ino`, never on a resolved path. |
| Never commit to main | Branch from `origin/main`, PR, verify, squash-merge, clean up. |
| Use bash, not zsh | Shell work runs in `/opt/homebrew/bin/bash` (5.x), not zsh or macOS `/bin/bash` 3.2: any loop, unquoted variable, glob or `set --` goes through it with `set -euo pipefail`, and you check the command's exit status, not the pipeline tail. |

Read the contract for the full text, the qualifier table, and the cleanup steps.
