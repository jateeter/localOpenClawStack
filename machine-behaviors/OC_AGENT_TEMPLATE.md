# OC-Agent-Template — machine-bound OpenClaw agents (input-analyst)

The reusable template through which **any** machine in the RealityEngine corpus
gets a dedicated OpenClaw agent that achieves all or part of the machine's stated
objective. The prototype is the Personal Health machine **Home Chronic Pain
Monitor**; the same derivation produces a valid agent for all 1014 machines (see
[§6 Validation](#6-validation)).

> **Naming note.** The request named the agent *"Home Chronic Care Pain
> Monitor"*. The canonical corpus machine is **"Home Chronic Pain Monitor"**
> (`RealityEngine_Machines/machines/HomeChronicPainMonitor.json`). The agent is
> named after the machine so the round-trip keys line up, so its `agentId` is
> `home-chronic-pain-monitor`. Rename the machine first if the "Care" variant is
> intended — the template regenerates the agent automatically.

---

## 1. Two complementary patterns, one loop

`derive_agents.py` (the prior prototype) builds **output-side actor** agents: one
per CES terminal output, dispatched *after* a CES fires, performing the
recommended action and writing a downstream completion sensor in a reserved band.

This template, `oc_agent_template.py`, builds the missing **input-side analyst**:
one agent, **named after the machine**, that reasons over current observations and
writes the machine's **normalized input vector back to the PE input region**, so
RE's deterministic CES logic fires *on agent-supplied inputs*. The analyst's
reasoned analysis is therefore "acted upon in one of the CESs of the machine."

They compose into the full perception→action loop:

```
 observations ─▶ [INPUT-ANALYST agent] ─▶ PE input region [1991:1995]
              ▶ RE runs CES ─▶ CES terminal output (e.g. PAIN_CRISIS)
              ▶ [OUTPUT-ACTOR agent] ─▶ action + completion sensor ─▶ RE
```

The analyst is the intelligent **front end** (what should the inputs be?); the
actor is the governed **back end** (what should we do about the fired output?).
Both ride the same ACP transport and the same PE write-back contract.

---

## 2. Prototype specification — Home Chronic Pain Monitor input-analyst

Generated artifact: [`agents/homechronicpainmonitor.oc-agent.json`](agents/homechronicpainmonitor.oc-agent.json)
(schema-valid; regenerate with `python3 oc_agent_template.py HomeChronicPainMonitor.json --write`).

| Field | Value |
|---|---|
| `agentId` | `home-chronic-pain-monitor` |
| `role` | `input-analyst` |
| Reasoning objective | Produce the normalized input vector for the machine from current observations |
| **Writes to** | PE **input** region `[1991:1995]` (the machine's own `perceptualMapping.input`) |
| Write-back sensor | `acp.openclaw.homechronicpainmonitor.input-analyst.assessment` |
| Source mapping id | `acp-homechronicpainmonitor-input-assessment` |
| Autonomy | `advise` (writes a PE sensor; stages/executes nothing) |
| Transport | `openclaw acp` via gateway `ws://127.0.0.1:18789`, `accepted-no-wait` |

### 2.1 Reasoned analysis of current pain levels

The agent reasons over four axes, each grounded in the machine's own
`sensorNormalization` anchors (so the model and the engine share one scale):

| idx | axis | 0.0 | 0.5 | 1.0 |
|---|---|---|---|---|
| 0 | `pain_scale_norm` | pain 9–10/10, unable to function | pain 4–6/10 manageable | pain 0–2/10 minimal |
| 1 | `functional_impairment_norm` | bedbound, no ADLs | limited, managing key ADLs with help | fully functional |
| 2 | `opioid_risk_norm` | multiple risk flags | one risk factor | no risk indicators |
| 3 | `physical_activity_norm` | zero activity, deconditioned | ADL-level only | therapeutic activity / PT adherent |

The full system prompt (in the artifact's `reasoning.systemPrompt`) also lists
**what each input pattern triggers** — PAIN_CRISIS (RED), OPIOID_RISK_ELEVATED,
FUNCTIONAL_IMPAIRMENT, PAIN_MANAGED — with each CES's action text, so the agent
reasons with the downstream consequence in view. **The agent asserts an expected
sequence; RE re-evaluates and is authoritative** (the AI side never re-derives
governance or CES semantics).

### 2.2 Result fed back to the RE → CES

The agent returns structured text; `responseMapping` maps it deterministically to
the four input positions. Worked demo (`--demo`, scripted deteriorating state):

```
pain_scale_norm: 0.2        ─┐
functional_impairment_norm: 0.35   │  → PE input region [1991:1995]
opioid_risk_norm: 0.5              │     = [0.2, 0.35, 0.5, 0.65]
physical_activity_norm: 0.65 ─┘
asserted_sequence: chronic-pain-crisis   (RE re-evaluates, decides which CES fires)
```

RE consumes the updated input source on the next push cycle and runs the machine's
CES sequences against it — the analyst has supplied the reality the machine
evaluates.

---

## 3. The generalized template

`oc_agent_template.derive(machine)` populates every field **from machine data
alone** — it is domain-agnostic and ran unchanged across 11 domains:

| Template field | Sourced from |
|---|---|
| `agentId`, `displayName` | `machine.name` (slugified) |
| `machine.inputRegion` / `outputRegion` | `perceptualMapping.input` / `.output` |
| `reasoning.inputAxes` | `sensorNormalization` keys + 0/0.5/1.0 anchors (fallback: `inputSemantics`) |
| `reasoning.sequenceCatalog` | `triggerConfig.rules` + firing-vector `metadata.action` |
| `reasoning.systemPrompt` | assembled from `description`, axes anchors, CES catalog, `populationFocus` |
| `agentBinding.writeBack.region` | **the machine's own input region** |
| `agentBinding.writeBack.semantics` | the input axis keys |
| `openclaw.*` | `config.json → openclaw` (ACP gateway, session key, source mapping) |
| `responseMapping.fields` | one scalar-0-1 field per input axis |

**Autonomy is fixed at `advise` by construction**: an input-analyst writes a PE
sensor and stages/executes nothing, so it sits cleanly below every life-safety
autonomy ceiling and never needs RED-blocking (RED is the *output* side's
concern). This is the key safety property that makes the template universally
applicable without per-domain autonomy review.

---

## 4. The round trip (ACP)

1. **Analysis request** (scheduler/PE tick) → `openclaw acp` session for the
   machine's agent (the analyst is observation-driven, not terminal-event-driven,
   so it does not require a `ces.terminal.event` envelope).
2. **Agent turn** → structured-keys text (the four axes + assertion + rationale).
3. **`responseMapping`** (reusing `openclaw_side.apply_response_mapping`) → the
   normalized input vector.
4. **Completion write-back** → `POST /api/integrations/completions` with
   `provider: "acp"` and source mapping `acp-<code>-input-assessment`
   (the canonical PE completion contract).
5. **PE** updates the input sensor source; **RE** runs the CES on the next push.

The completion leg is identical to the existing actor path — only the **target
region is the input band, not the reserved completion band** — so no PE-loader or
corpus change is required.

---

## 5. Files

| File | Role |
|---|---|
| `oc_agent_template.py` | deriver: machine JSON → input-analyst instance (`--write`, `--demo`) |
| `templates/oc-agent.schema.json` | instance schema; `$ref`s the canonical `agent-binding.schema.json` |
| `agents/homechronicpainmonitor.oc-agent.json` | the prototype instance |
| `validate_oc_agents.py` | per-machine validation + domain/corpus coverage report |
| `out/oc-agents.*.report.md` | generated coverage reports |

Reuses the existing `config.json`, `minischema.py`, `openclaw_side.py`, and
`derive_agents.py` helpers — this template is additive, not a fork.

---

## 6. Validation

`python3 validate_oc_agents.py [--domain D | --all]` derives every machine's
instance and checks the schema **plus** invariants the schema can't express:
input length ≥ 1; one reasoning axis **and** one response field per input
position; **write-back region == the machine's input region**; response targets
cover every input index exactly once; non-empty CES catalog.

| scope | machines | valid | failed |
|---|---|---|---|
| `health-personal` | 24 | **24** | 0 |
| **whole corpus** (`--all`) | 1014 | **1014** | 0 |

Every machine produces a schema-valid, contract-valid input-analyst agent.

### Corpus-quality warnings (the validator doubles as a linter)

`valid-with-warnings` means the agent is usable but the machine could ground it
better. The dominant warning is **`input axes derived from inputSemantics`**: the
machine lacks a length-matched `sensorNormalization`, so axes carry no 0/0.5/1.0
anchors and the agent reasons with a weaker scale. Counts by domain are in
`out/oc-agents.all-domains.report.md`. Notably `health-services` (200 machines)
has full `sensorNormalization` coverage (0 warnings), while `built-space`,
`legal-services`, `life-balance`, and `transportation` are fully un-anchored —
backfilling `sensorNormalization` there is the highest-leverage corpus
improvement for analyst quality.

---

## 7. Roadmap

- [x] Input-analyst template + schema + prototype instance.
- [x] Domain + whole-corpus structural validation (1014/1014).
- [ ] **Live PE loop** — post the completion to a running PE, confirm the input
  sensor updates and a CES transition fires (reuse `--live` plumbing).
- [ ] **Real OpenClaw turn** — replace the scripted demo with an `openclaw acp`
  session through the gateway (shared with actor-side M7).
- [ ] **Anchor backfill** — drive `sensorNormalization` coverage from the linter
  output so every analyst reasons on an anchored scale.
- [ ] **Promotion** — an analyst binding is schema-identical to a corpus
  `agentBinding`; offer an opt-in patch to promote stable ones into the machine
  file as an explicit, reviewed corpus change (never silently).
