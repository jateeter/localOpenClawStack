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

## MUST: never commit to main — branch, PR, verify, merge, clean up

**No change reaches `main` in any repo except through a branch and a pull
request.** Not documentation, not a one-line fix, not a "trivial" follow-up, and
not a hotfix for a gate that is currently red. There is no size or urgency
threshold below which this stops applying.

The full workflow, every time:

1. **Branch from `origin/main`** — `git fetch origin main && git checkout -B <branch> origin/main`.
   Branch from the remote, not from whatever the local `main` happens to be:
   a stale local ref is how a change gets built on a tree that no longer exists.
2. **Commit** with a message that says what changed and *why*, including the
   evidence that motivated it.
3. **Push** and **open a PR**.
4. **Verify** — see "MUST: verify a merge beyond the hosted checks". State in the
   PR which gates ran, what they returned, and what could not be exercised.
5. **Merge** — squash, and delete the remote branch.
6. **Clean up** — delete the local branch, `git worktree prune`, and remove any
   run directories the work created.

Two things about cleanup that are easy to get wrong:

- **Squash-merged branches are not ancestors of `main`.** `git merge-base
  --is-ancestor` and "empty diff against origin/main" both report *nothing to
  delete*, and a branch that is merely behind `main` shows a diff full of
  reversions. Ask the forge which PRs merged — `gh pr list --state merged
  --json headRefName` — and delete those heads.
- **Never delete a branch with an open PR.** Check state before pruning.

Why this is absolute: a direct commit to `main` has no diff anyone reviewed, no
place to record the verification, and nothing to revert cleanly if it is wrong.
It also breaks the only reliable cleanup signal — a merged PR — so the branch
inventory stops meaning anything.
