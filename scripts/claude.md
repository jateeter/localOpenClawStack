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

