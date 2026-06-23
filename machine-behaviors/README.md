# machine-behaviors — Automating Machine Behaviors via OpenClaw agents

Prototype that **reads a RealityEngine machine definition and derives the set of
OpenClaw agents that should act on its behavior**, including the exact reality
(perceptual-space) vector each agent impacts when it completes.

It is the *external OpenClaw adapter* called for in
`RealityEngine_CPP/docs/INTEGRATION_ARCHITECTURE.md` → "ACP / OpenClaw Roadmap"
item 2. It therefore deliberately:

- **does not modify any machine `.json`** — bindings are derived into a sidecar;
- **does not modify any PE loader** — it speaks the PE's existing public contract
  (`/api/integrations/acp/dispatch`, `/api/integrations/completions`).

> Prototype target: Personal Health → **Home Chronic Pain Monitor**
> (`RealityEngine_Machines/machines/HomeChronicPainMonitor.json`).

---

## 1. Workflow analysis (what the prototype automates)

### 1.1 Read the machine definition
A machine declares everything needed to derive behavior **without code changes**:

| Source field | Used for |
|---|---|
| `metadata.triggerConfig.rules[]` | one terminal CES output per rule → one behavior |
| `sequences[].vectors[].outputVectors[].metadata.action` | the human action text that characterizes each output |
| `metadata.machineClass` | allowed autonomy modes & write-back types (via `domain-registry.json → agentReadyMachineClasses`) |
| `metadata.tagging.primaryDomain` / `category` | resolves the domain → `defaultAgentFamilies`, `defaultAutonomy` (`domain-manifest.json`) |
| `metadata.governance` | owner team, runbook, SLA, RAG → dispatch envelope governance |
| `perceptualMapping.input/output` | the reality vectors the machine reads/asserts |

### 1.2 Determine the appropriate OpenClaw agent set
For each CES output:
1. Extract its one-hot **output index**, **label**, **RAG**, and **action text**.
2. **Select an agent** from the domain's `defaultAgentFamilies` by scoring each
   family's name tokens (expanded through a generic lexicon) against the action
   text. (Domain-agnostic — no hard-coded health rules.)
3. **Resolve autonomy** = domain default, capped by the machine class's
   `allowedAutonomyModes`, then capped again by RAG/positive-state:
   - positive/OK terminal states (e.g. `PAIN_MANAGED`) → `observe`;
   - life-safety domains never reach `automated-act`;
   - `RED` outputs at `supervised-act` are **blocked from direct action** and
     route to governance (matches the autonomy gate in the Architecture Audit).
4. Outputs are **grouped by agent** → the unique agent set. An agent's binding
   `mode` is the **ceiling** across its outputs; each dispatch still carries the
   per-output mode.

### 1.3 Reality-vector mapping each agent impacts on completion
Each write-back agent is assigned a **distinct region** in a reserved band
(`completionRegions.baseOffset`, default `4400`; the domain sweep auto-reserves
above the corpus max). On completion the agent writes its semantics to that region
as a PE sensor `acp.openclaw.<machine>.<agent>.completion`; RE consumes it on the
next push cycle. Region length scales with autonomy — `advise` 4
(`completed, failed, confidence, actionClass`), `supervised-act` 5
(`+ review_required`), `automated-act` 6. Observe-only agents impact **no** vector.

The agent returns **text**, so how that text becomes those vector values is a
first-class, schema-validated contract (`responseMapping`) — see
[`RESPONSE_MAPPING.md`](RESPONSE_MAPPING.md). This is the round-trip's
textual→value + multi-position mapping.

For Home Chronic Pain Monitor (output region `[2023:2027]`):

| Output (RAG) | OpenClaw agent | Mode | Reality vector impacted on completion |
|---|---|---|---|
| `PAIN_CRISIS` (RED) | `caregiver_support_agent` | supervised-act* | `[4400:4404]` |
| `OPIOID_RISK_ELEVATED` (AMBER) | `medication_adherence_agent` | supervised-act | `[4404:4408]` |
| `FUNCTIONAL_IMPAIRMENT` (AMBER) | `wellness_coach_agent` | supervised-act | `[4408:4412]` |
| `PAIN_MANAGED` (AMBER) | `wellness_coach_agent` | observe | none |

\* RED is blocked from direct action → governance escalation.

### 1.4 Agent definition template values (from OpenClaw integration docs)
Two contracts are populated end-to-end:

- **RealityEngine agent binding** (`schemas/agent-binding.schema.json`):
  `agent, mode, trigger, allowedActions, writeBack, autonomyPolicy, riskControls`
  — every derived binding is validated against this canonical schema.
- **OpenClaw / xACP integration** (from `RealityEngine_CPP/config/integrations.example.json`
  `openclaw-xacp` + docker-compose): `command (openclaw acp)`, `gatewayUrl`,
  `sessionKey`, `targetAgent`, `dispatchMode (accepted-no-wait)`,
  `completionMode (pe-source-mapping)`, `completionSourceMappingId`. Edit these
  in `config.json`.

### 1.5 Debug logging on both sides
JSONL, gated by `MB_DEBUG` (`0` silent, `1` file, `2` file+stderr), sharing one
`correlationId` so a single trigger is greppable across the boundary:

- **dispatch side** (`logs/behavior.dispatch.jsonl`): `dispatch.envelope-built`,
  `dispatch.accepted` (PE 202 no-wait).
- **openclaw side** (`logs/behavior.openclaw.jsonl`): `openclaw.session-spawn`,
  `openclaw.agent-run`, `openclaw.completion-built`, `openclaw.completion-posted`.

---

## 2. Components

| File | Role |
|---|---|
| `derive_agents.py` | core: machine JSON → agent set + sidecar bindings + reality-vector impact |
| `domain_sweep.py` | batch: derive a whole domain at once, global region allocation, coverage report ([`DOMAIN_HEALTH_PERSONAL.md`](DOMAIN_HEALTH_PERSONAL.md)) |
| `dispatch_side.py` | RE/PE side: build `ces.terminal.event` envelope, record accepted-no-wait dispatch |
| `openclaw_side.py` | OpenClaw side: run (simulated) agent turn, build PE completion write-back |
| `run_prototype.py` | end-to-end orchestration + summary tables |
| `behavior_log.py` | correlated both-side JSONL debug logger |
| `schemas/response-mapping.schema.json` | the round-trip textual→value contract ([`RESPONSE_MAPPING.md`](RESPONSE_MAPPING.md)) |
| `minischema.py` | dependency-free validator against the corpus's real JSON schemas |
| `config.example.json` | OpenClaw template values + region-band policy |
| `tests/run_tests.py` | 27 incremental checks (derivation, schema-validity, regions, logging) |
| `tests/run_domain_tests.py` | 20 domain-sweep checks (coverage, global regions, autonomy ceilings, reserved band) |
| `tests/run_corpus_tests.py` | 17 corpus-wide checks (all corpus machines, schema-valid, PE source mappings) |
| `tests/run_all.sh` | runs all three suites (82 checks) |
| `pe-integration/corpus.pe-source-mappings.json` | materialized PE source mappings for all 1047 write-back agents ([`CORPUS_COVERAGE.md`](CORPUS_COVERAGE.md)) |

## 3. Usage

```bash
cd localOpenClawStack/machine-behaviors

# derive agents for a machine (read-only)
MB_DEBUG=0 python3.13 derive_agents.py HomeChronicPainMonitor.json

# end-to-end prototype (offline; dispatch/completion POSTs are dry-run)
MB_DEBUG=1 python3.13 run_prototype.py

# against a running PE (Scala 5000 / CPP 5300 / LSP 5600 — set pe.baseUrl)
MB_DEBUG=2 python3.13 run_prototype.py --live

# incremental test suite
python3.13 tests/run_tests.py
```

Requires Python 3.11+ (`python3.13` here). No third-party packages.

---

## 4. Implementation roadmap

- [x] **M0 Analysis** — workflow + reality-vector mapping model (this README §1).
- [x] **M1 Deriver** — machine → agent set, schema-valid bindings, region allocation.
- [x] **M2 Both-side debug logging** — correlated JSONL on dispatch + openclaw sides.
- [x] **M3 Prototype (Home Chronic Pain Monitor)** — end-to-end derive → dispatch
      → simulated OpenClaw turn → PE completion, all schema-validated offline.
- [x] **M4 Incremental tests** — 27 checks vs canonical schemas (`tests/run_tests.py`).
- [x] **M5 Region-band reservation** — persisted as
      `domains/domain-registry.json → rangePolicy.reservedRanges`
      (`acp-openclaw-agent-completions`, `[7300:12300]`, sized corpus-wide). `scripts/audit-corpus.py`
      enforces machines stay out of reserved bands (strict-error), and the sweep
      allocates *within* the registry-declared band and fails on overflow. This is
      the one milestone that edits the corpus repo (registry + audit script, not
      machine `.json` or PE loaders).
- [x] **M9 Domain sweep** — full `health-personal` automation (24 machines, 94
      behaviors, 41 bindings) with global region allocation, batch schema
      validation, selection diagnostics, and a 14-check suite. See
      [`DOMAIN_HEALTH_PERSONAL.md`](DOMAIN_HEALTH_PERSONAL.md).
- [ ] **M6 Live PE loop** — drive `--live` against a running PE: post the dispatch,
      confirm it lands in `/api/dispatch/ledger`, post the completion, confirm the
      sensor source and a downstream RE transition. (Architecture Roadmap item 6.)
- [ ] **M7 Real OpenClaw turn** — replace `_simulate_turn` with an `openclaw acp`
      session through the gateway at `ws://127.0.0.1:18789`.
- [ ] **M8 Session policy** — allowed gateway URLs, session-key prefixes, target
      agents, permission profiles (Architecture Roadmap item 3).
- [x] **M10 Corpus sweep (all domains)** — `domain_sweep.py --all` derives all
      1175 machines / 4531 behaviors / 1605 bindings across 12 domains, allocates
      globally inside the reserved band, emits PE source mappings, 0 schema errors.
      See [`CORPUS_COVERAGE.md`](CORPUS_COVERAGE.md). Promotion of derived bindings
      into `agent-dispatcher` machines remains a separate reviewed corpus change.

## 5. Constraints / non-goals

- **No machine `.json` or PE-loader edits.** Bindings stay external (sidecar). The
  only corpus-repo edits are the M5 reserved-range registration in
  `domain-registry.json` and its enforcement in `audit-corpus.py`.
- **Region band is reserved (M5 done).** The domain sweep allocates inside the
  registry-declared `[7300:12300]` band; the single-machine prototype default
  (`4400`) remains for offline demos only.
- **OpenClaw turn is simulated** until M7; the *payload and write-back path* are
  real and schema-valid.
