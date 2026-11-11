# localOpenClawStack Scripts Guidance

This directory contains operational helpers for OpenClaw startup, shutdown, bootstrap, and validation.

- Keep scripts explicit about required tokens, ports, and bootstrap assumptions.
- Use `bash-language-server` for shell changes.
- Verify Docker Compose state live after script changes.
- Do not write secrets or local runtime data into tracked files.

## Agent profiles

- `agent-profile.sh NAME` resolves a profile to an agent index path and is the only
  place that knows profiles exist. Everything downstream reads an index of one shape.
- `generate-regression-profile.py` derives `profiles/regression.txt` from
  `RealityEngine_CI/config/regression-corpus.txt`, the regression-test corpus. Entries
  with no agent *by rule* (arbitration fixtures, localAIStack machines) are excluded
  and named in the header; any other unresolved entry is an error. Run `--check`
  after any corpus change; a stale profile is the failure the profile exists to prevent.
- Filtered indexes land in `machine-behaviors/agents/.generated/` and are not tracked.
- `sync-machine-agents.sh` prunes machine-behavior agents outside the active profile.
  It identifies them by their generated workspace path, so a hand-added agent survives.
- It reports the profile carried by the index it was actually handed, not the
  `OPENCLAW_AGENT_PROFILE` default, so an explicit `OPENCLAW_AGENT_INDEX_PATH` cannot
  make the log claim a profile it did not sync.

## Agents follow the deployed corpus (owner rule, 2026-10-06)

If the engines load the regression corpus, OpenClaw loads the regression agents;
full corpus, full agents. Never a profile chosen by corpus *name*.

- `agent-profile.sh manifest:<path>` is the agents of exactly the machines that
  manifest deploys, through `generate-regression-profile.py --allow-empty`
  (`OPENCLAW_CORPUS_MACHINES_ROOT` resolves entries). `startUniverse.sh` passes
  the manifest it materialised, so standard-deployment gets its 12 agents, not
  the regression profile's 15.
- `start.sh` records what it loaded as `.generated/INDEX.deployed.json`, and
  `verify-openclaw-config.sh` defaults to it. Run on its own, the verifier used
  to compare a 16-agent regression deployment with the full 1,323-agent index.
- `verify-agents-match-corpus.py` (from `verify-deployment.sh`) reads the
  running universe from the instance registry, asks every engine what it loaded
  (`GET /api/machines/json/list`), requires them to agree, and fails if the
  deployed agents are not exactly that corpus's. No universe, or none of its
  engines answering, is reported, not failed. Test: `test-agents-follow-corpus.sh`.

## Per-clone agents (#91)

- `clone-agents.mjs load|unload|purge|list` loads and unloads the agent of one
  workflow step's clone: a copy of the template machine's agent (its workspace
  files and model settings), with the id `clone-<sha256(cloneId)[:16]>`.
  - RealityEngine_Manager's instantiation component calls it through a FIFO
    queue. Each change is one temporary file renamed over `openclaw.json`, under
    an exclusive lock, and re-adopted as the baseline.
  - Clone agents live under `workspaces/clones/`, so `sync-machine-agents.sh`
    keeps them.
- An unload removes the agent's entry, workspace and state directory, as the
  owner ruled on 2026-11-04. Manager unloads with `--entry-only`, waits until
  the gateway stops listing the agent, then runs `purge`, so the gateway's
  skills watcher never sees its directory vanish.
- Test: `test-clone-agents.sh` checks concurrent loads and unloads in both
  config schemas. It needs no gateway.

## Config clobber guard

- OpenClaw's gateway auto-restores `openclaw.json` from `openclaw.json.bak` when a
  read looks destructive — chiefly `size-drop-vs-last-good`, a shrink to under half
  the baseline. The baseline is the `lastKnownGood` fingerprint in
  `openclaw/logs/config-health.json`, falling back to a fingerprint of the `.bak`.
- Narrowing the agent profile is that exact shape (1.2 MB → 12 KB), so every gateway
  restart used to restore the previous profile's `agents.list` and fail the count
  gate. See issue #30.
- `adopt-config-baseline.sh` is the answer: after a deliberate config rewrite it
  copies the config over `.bak` and `.last-good` and drops the stale fingerprints,
  so the guard's baseline moves with the profile. `sync-machine-agents.sh` calls it.
- Keep the guard, do not defeat it. A truncation nobody adopted still trips it, and
  now restores the active profile rather than a stale one.
- Any new script that rewrites `openclaw/openclaw.json` must call
  `adopt-config-baseline.sh` afterwards, or its change will not survive a restart.

## Standing rules

The standing rules are in
`../../RealityEngine_CI/docs/ENGINEERING_CONTRACT.md`, which is
authoritative. They are not listed or summarised here, so this file never
falls behind the contract. Read it before changing anything here.
