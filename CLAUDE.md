# localOpenClawStack Guidance

Last reviewed: 2026-10-01

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
- Agent loading is profile-selected. `--agent-profile=full` (the default) loads the whole 1323-agent corpus; `--agent-profile=regression` loads the **18** agents of the regression-test corpus (see "Which agent corpus matters" below). `start.sh` resolves the profile once and hands the same index to the sync, the config verifier, and the live count gate.
- `machine-behaviors/agents/profiles/regression.txt` is generated from `RealityEngine_CI/config/regression-corpus.txt`, not hand-maintained: 26 entries, giving 18 agents, with 5 arbitration fixtures and 3 localAIStack machines excluded by rule and named in the file's header. Re-run `./scripts/generate-regression-profile.py` when that corpus changes; `--check` is the drift guard and fails when the two disagree. `RealityEngine_Machines/tests/contracts/openclaw_profile_drift_test.py` runs it on every corpus change.
- Narrowing the profile prunes the previous profile's workspaces and agent directories. Switching back re-materializes them; the sibling repos are needed only to regenerate or check a profile, not to start the stack.
- The gateway auto-restores `openclaw.json` from `openclaw.json.bak` when a read looks like a clobber, and a narrowed profile is a >50% size drop. `scripts/adopt-config-baseline.sh` moves that guard's baseline (`.bak`, `.last-good`, and `openclaw/logs/config-health.json`) onto each deliberate config rewrite so the profile survives the restart; `sync-machine-agents.sh` calls it. Anything else that rewrites `openclaw/openclaw.json` must call it too.
- Treat upstream version freshness as time-sensitive; re-check before claiming current release status.

- **The default model is `ollama/nemotron-3-nano:4b-mlx`**, the RealityEngine universe model (RC3): the MLX build of Nemotron 3 Nano 4B (Ollama 0.40.5+). The plain `:4b` tag is the GGUF build on llama.cpp. `OPENCLAW_DEFAULT_MODEL` only seeds a *fresh* `openclaw.json`; an existing config keeps its model. Switch on command with `scripts/set-model.sh <ollama-tag>`, which rewrites `agents.defaults.model.primary` and every agent whose own model was the old default (main included, since `sync-machine-agents.sh` keeps main's model; an agent pinned elsewhere keeps it and is reported; `test-set-model.sh`), re-runs `harden-config.sh` so the Ollama provider lists the model, and adopts the result as the config baseline. It does not restart the gateway. The model calls tools; Ollama refuses `format` for it, which matters to the PEs' dispatch and not to the gateway (`RealityEngine_CI/docs/OLLAMA_INTEGRATION.md`).

- **One OpenClaw release, one config format (RealityEngine_CI#592).** The container
  image and the native host gateway both run the pinned release, 2026.10.7, npm
  `latest` (`update-versions.sh` reads that dist-tag; it read `extended-stable`
  until 2026-10-24, when the stack sat on 2026.6.10). A local `.env`
  `OPENCLAW_VERSION` overrides the compose default, so a stale `.env` pins an old
  gateway. The release needs Node `>=26.12.1`; the `.env` `NODE_IMAGE` digest must
  resolve to it.
  - Its format is `agents.entries` keyed by id, `gateway.nodes.commands.deny`,
    `agents.defaults.modelPolicy.allow`, `agents.ownership: "explicit"`, and an
    agent id of at most 64 characters. 548 of the 1,322 machine-name slugs are
    longer (#72), so `oc_agent_template.agent_id_for` keeps a slug that fits and
    cuts a longer one to 55 characters plus `-` and the first 8 hex digits of
    sha256(machine id) (owner option A, 2026-10-25); `displayName` keeps the full
    name, and `run_corpus_tests.py` C5.10-C5.13 hold the rule. Engines and PEs
    address OpenClaw sensors by machine code, not agent id, so the change is
    local to this repo. `sync-machine-agents.sh` still refuses an invalid id by
    name.
  - Every script that reads or writes `openclaw.json` goes through
    `scripts/lib/oc-config.sh`: an existing config decides its own format, a fresh
    one takes `OPENCLAW_SCHEMA` (default `entries`; `list` is the pre-2026.9
    format, read only to migrate it). `OPENCLAW_STATE_DIR` moves the state
    directory, `OPENCLAW_HOME_DIR` is that directory as the gateway sees it
    (`/home/node/.openclaw` in the container), `OPENCLAW_GATEWAY_BIND`,
    `OPENCLAW_HTTP_ENDPOINTS` (the `/v1` API, default on: Open WebUI and
    `verify-deployment.sh` use it) and `OPENCLAW_OLLAMA_NUM_CTX` (default 32768;
    Ollama's 4096 truncates an agent's bootstrap prompt).
  - **State migrates before a gateway starts.** `scripts/migrate-state.sh`, run by
    `start.sh`: when the config was last written by another version, or is still
    in the `list` format, it backs the state directory up to `.state-backups/`,
    stops the gateway and runs `openclaw doctor --fix` offline, stepping through
    2026.9.7 for container state older than it. With `OPENCLAW_STATE_DIR` it runs
    the host's `openclaw`, which must be the pin.
  - `scripts/test-native-schema.sh` runs the generators for both formats and, when
    `openclaw` is installed, the real `openclaw config validate`.

- **The native gateway (RealityEngine_CI#592).** `scripts/start.sh --native` (what
  `startUniverse.sh --openclaw` runs by default) puts the gateway on the Mac and
  keeps only the browser sidecar and Open WebUI in Docker.
  - `scripts/native-gateway.sh up|down|status|check|render` runs the host's own
    `openclaw` (npm -g, the pin, on a Node its `engines` accepts; Homebrew's prefix
    first) as the user LaunchAgent `org.realityengine.openclaw-gateway` on
    127.0.0.1. Its home is `~/.realityengine/openclaw` (`OPENCLAW_NATIVE_HOME`,
    handed to OpenClaw as `OPENCLAW_STATE_DIR`); `~/.openclaw` is never touched.
  - `up` runs `migrate-state.sh`, `harden-config.sh`, `sync-machine-agents.sh` and
    `verify-openclaw-config.sh` against that home, checks the config binds
    loopback with Ollama on 127.0.0.1, builds the sandbox image, loads the
    LaunchAgent, and checks `/v1/models` lists every agent of the profile and
    refuses a call without the token.
  - The LaunchAgent runs `scripts/native-gateway-run.sh`, which reads only
    `OPENCLAW_GATEWAY_TOKEN` from `.env` at start: no secret is in the plist.
  - Agents run sandboxed in Docker. `openclaw-sandbox:bookworm-slim` is built from
    `docker/sandbox/Dockerfile`, vendored from OpenClaw's source at the pinned
    release (the npm package names the image but does not ship it).
  - The browser sidecar publishes CDP on `127.0.0.1:${RE_PORT_BROWSER_CDP:-9222}`
    only. The image starts Chromium as `wrapped-chromium ${CHROME_CLI}` and proxies
    its debugging port through nginx on 9223; compose sets
    `CHROME_CLI=--remote-debugging-port=9222`, without which the proxy answers 502
    and no gateway, container or native, could attach. `/json/version` returns a
    port-less `ws://localhost/...`; OpenClaw attaches regardless.
  - Open WebUI reaches the native gateway at
    `http://host.docker.internal:<port>/v1` (`OPENWEBUI_OPENAI_BASE_URL`).
  - **Not done:** a page the Mac serves (Manager, PIM, Open WebUI) cannot be opened
    in the sidecar yet. The gateway resolves a page's host on the Mac, which does
    not resolve `host.docker.internal`, and `localhost` in the sidecar is the
    container. #592 lists the URL translation still needed.
  - The scripts that generate the native home take the caller's environment over
    `.env`: `.env` holds the container's values (`OLLAMA_BASE_URL` on
    host.docker.internal), and sourcing it alone pointed the native gateway there.

## Which agent corpus matters

**Decided 2026-09-25. Do not re-derive it.**

- **The regression-test corpus is the corpus of interest.** It is `RealityEngine_CI/config/regression-corpus.txt`, the corpus the regression lanes boot, and its agents must be current on every regression run. Of its 26 entries, **18 carry an agent**. The other 8 are 5 arbitration conformance fixtures, agent-free by rule (`RealityEngine_Machines/docs/CORPUS_EXIT_CRITERIA.md` §3.3), and 3 localAIStack machines (`rag_corrective_cycle`, `session_rag_context`, `session_agent_context`) that OpenClaw does not cover.
- **The full agent corpus (1,323) is constructed once per week**, as part of the full-corpus regression test. It is not rebuilt per change, and a change is not verified against it. Construct it with `python3 machine-behaviors/materialize_agents.py --fresh` and verify it with `RealityEngine_CI/scripts/check-corpus-exit-criteria.py --machines ../RealityEngine_Machines --openclaw .`.
- **Current against *which* corpus.** `agents/INDEX.json` → `provenance.corpus.digest` names the corpus the agents were derived from. It is the fingerprint the released OWL baselines carry. When the digest does not match the machine corpus, the agents are stale, even if every count gate passes.

**Not enforced yet: RealityEngine_CI#467.** No gate detects a stale agent corpus:
- The exit-criteria check tests counts, joins and axis names, not actions or RAG.
- ~~`materialize_agents.py` has no `--check`~~ **Fixed 2026-10-01 (#467 item 2):** `python3 machine-behaviors/materialize_agents.py --check --manifest ../RealityEngine_CI/config/regression-corpus.txt` derives the 15 regression agents in memory and fails on any spec that differs from the committed one. It writes nothing and needs no live universe. RealityEngine_CI's regression harness runs it on every run. `--check` without `--manifest` covers the whole corpus, including `INDEX.*` and orphaned specs.
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
