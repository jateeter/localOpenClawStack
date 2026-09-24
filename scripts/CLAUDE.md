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
  `RealityEngine_CI/config/standard-deployment-corpus.txt`. Run `--check` after any
  corpus change; a stale profile is the failure the profile exists to prevent.
- Filtered indexes land in `machine-behaviors/agents/.generated/` and are not tracked.
- `sync-machine-agents.sh` prunes machine-behavior agents outside the active profile.
  It identifies them by their generated workspace path, so a hand-added agent survives.
- It reports the profile carried by the index it was actually handed, not the
  `OPENCLAW_AGENT_PROFILE` default, so an explicit `OPENCLAW_AGENT_INDEX_PATH` cannot
  make the log claim a profile it did not sync.

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

## Standing rules — authoritative in `../../RealityEngine_CI/docs/ENGINEERING_CONTRACT.md`

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
