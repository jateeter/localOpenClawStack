# localOpenClawStack Guidance

Last reviewed: 2026-08-26

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
- The gateway auto-restores `openclaw.json` from `openclaw.json.bak` when a read looks like a clobber, and a narrowed profile is a >50% size drop. `scripts/adopt-config-baseline.sh` moves that guard's baseline (`.bak`, `.last-good`, and `openclaw/logs/config-health.json`) onto each deliberate config rewrite so the profile survives the restart; `sync-machine-agents.sh` calls it. Anything else that rewrites `openclaw/openclaw.json` must call it too.
- Treat upstream version freshness as time-sensitive; re-check before claiming current release status.

## LSP Support

Use Docker/YAML/JSON language servers for compose and config, Bash language server for scripts, and markdown LSP for docs.

## Editing Rules

- Do not commit runtime state, logs, browser profiles, Open WebUI data, credentials, or generated tasks unless explicitly requested.
- Keep bootstrap behavior explicit about default credentials and gateway token requirements.

## MUST: every use of the word "registry" carries a qualifier

**The word "registry" MUST NEVER appear unqualified. Every single use of the
word takes a qualifier naming which registry is meant.**

This is a hard requirement, not a style preference. It applies to every
occurrence in every context, with no exceptions: prose, end-of-task summaries,
commit messages, PR bodies, issue titles and bodies, code comments, docstrings,
variable and function names, log lines, and documentation.

Wrong, in every case — these are all violations:

- "the registry"
- "a versioned registry"
- "the registry file" / "update the registry" / "registry-backed"
- "check the registry first"
- "registry drift"

Right — a qualifier every time:

- "the **instance** registry"
- "a versioned **cesgen** registry"
- "the **arbitration** registry"
- "**machine** registry drift"

If you type the word "registry" and the word immediately before it is not a
qualifier, stop and add one. Re-read every summary and every message for the
bare word before sending it — that is where this rule is actually broken, because
the surrounding context makes the referent feel obvious in the moment. That
feeling is exactly the assumption the rule exists to block.

Qualifiers currently in use. **This list is open, not exhaustive** — a registry
added later gets a qualifier too; nothing is ever promoted to being "the
registry" by virtue of being the one under discussion:

- **instance** registry — `/tmp/re-registry/re-registry.json`, served at
  `:5999/re-registry.json`. Running RE/PE instances with `re_url`/`pe_url`/ports,
  plus `services` and `allocation`. What `RE_REGISTRY_URL` points at.
- **machine** registry — the machines a runtime holds in memory, reported by
  `GET /api/machines`. Distinct from `GET /api/machines/json/list`, the on-disk
  corpus catalog.
- **cesgen** registry — `RealityEngine_Machines/domains/ces-contract-registry.json`.
  Which CES output-stream contract shards exist, what corpus each was recorded
  against, whether each is current.
- **arbitration** registry — `machines/domains/arbitration-registry.json`.
- **domain** registry — `machines/domains/domain-registry.json`.
- **semantic-bus** registry — `machines/domains/semantic-bus-registry.json`.
- **tag** registry — `RealityEngine_CI/docs/TAG_REGISTRY.md`.

## MUST: verify a merge beyond the hosted checks

**A green PR is not a verified PR. Never merge on the hosted checks alone.**

The hosted path does not exercise this system's integration points. A PR can show
every check green and still be unverified, because the checks that ran were a
security scan and — at most — a corpus gate. `localAIStack`, `localOpenClawStack`,
Ollama, Qdrant, MQTT, the OpenClaw ACP gateway and the multi-engine universe are
**not** reachable from the hosted runners, so nothing on that path can tell you
whether the change works where it has to work.

Observed repeatedly: RealityEngine_Machines PRs report exactly one check
(GitGuardian). That is not evidence about the corpus, the registries, the
engines, or any bridge.

Before merging, verify **locally**, and say in the PR which of these you ran and
what they returned:

- The repo's own gates — `validate-corpus.sh`, the contract suite,
  `npm test`, `make test`, `sbt test` — whichever the change touches.
- The integration points the change can reach: a live 3-of-3 universe, the
  local AI stack, the OpenClaw gateway, MQTT — whichever the change can affect.
- The specific behaviour the change claims, with the numbers it produced.

If an integration point cannot be exercised, **say so in the PR** and name it.
An unverified area that is named is a known gap; an unverified area that is
silent reads as tested.

A hosted green tells you the change did not break the hosted path. That is worth
having and is not the question being asked at merge time.
