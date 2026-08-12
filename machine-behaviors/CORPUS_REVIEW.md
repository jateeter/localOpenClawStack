# Machine corpus × agent corpus review

Reviewed 2026-08-10 against `RealityEngine_Machines` @ 1321 machines and
`machine-behaviors/agents` @ 1320 agents.

Every existing gate passes: 54 corpus contract tests, 1328 schema-valid
artifacts, `corpus-index --check`, `region-allocation --check`,
`generate-owl --manifest-check`, `verify_schemas.py`, and
`validate_oc_agents.py --all` (1321/1321). The findings below are all in the
space those gates do not reach — almost entirely the **cross-repo boundary**
between the machine corpus and the agent corpus, which no single repo's tests
can see.

## The four agent axes

The same idea — "an agent is bound to this machine" — is represented four
times, in four places, with four cardinalities and no check that they agree.

| axis | where | cardinality | identity | region |
|---|---|--:|---|---|
| curated binding | `machines/**.json` → `metadata.agentBinding` | 1058 | localAI agent id | machine input |
| corpus projection | `machines/**.json` → `metadata.openClawProjection` | 1184 | `openclaw-input-analyst` (role) | machine input |
| sidecar analyst | `agents/**.oc-agent.json` + `INDEX.json` | 1320 | per-machine `agentId` | machine input |
| completion mapping | `pe-integration/corpus.pe-source-mappings.json` | 1146 machines / 1216 rows | agent **family** | reserved band `[17000:22311]` |

Containment is clean in one direction only: every machine with a curated
binding also has a projection (1058 ⊂ 1184), and every machine with a
projection has a sidecar (1184 ⊂ 1320). The completion axis is not nested in
any of them.

## Consistency

### C1 — regions agree, meanings do not (1127 of 1184)

`openClawProjection.writeBackRegion` and the sidecar's `writeBack.region` agree
on **1184 of 1184**. The *names of the elements in that region* disagree on
**1127 of 1184 (95%)**:

| class | count | example |
|---|--:|---|
| cosmetic — snake vs kebab | 834 | `ag_harvest_readiness_assessor_input_1` / `ag-harvest-readiness-assessor-input-1` |
| suffix-only — `_norm` / `_status` / `_bit` | 260 | `process_stability` / `process_stability_norm` |
| identical | 57 | — |
| corpus generic, sidecar descriptive | 16 | `ag_atmospheric_controller_input_1` / `co2_ppm_norm` |
| sidecar generic, corpus descriptive | 8 | `clinical_criterion_met_bit` / `caretransitionworkflow-input-1` |
| **genuinely different meanings** | **9** | see below |

The 9 are the ones that matter. An agent is told to produce quantities the
machine does not expect at those positions:

| machine | corpus says the inputs are | sidecar tells the agent to produce |
|---|---|---|
| `AGX054_yuma-co2-safety-compliance-officer` | `co2_in_enrichment_band_600_1500_ppm`, … | `process_stability_norm`, … |
| `AGX051_yuma-aqua-maintenance-forecaster` | `water_ph_in_band`, `water_ec_in_band`, … | `process_stability_norm`, … |
| `AGX052_yuma-do-probe-reliability-tracker` | `do_level_in_nominal_band`, … | `process_stability_norm`, … |
| `AGX053_yuma-vpd-hvac-service-planner` | `ambient_temp_in_band`, … | `process_stability_norm`, … |
| `ChildDevelopmentMonitor` | `milestone_progress_norm`, `social_emotional_engagement_norm`, … | `milestone_completion_norm`, `peer_interaction_norm`, … |
| `HomeEnvironmentSafetyMonitor` | `housing_condition_norm`, `utility_stability_norm`, … | `temp_stability_norm`, `air_quality_norm`, … |

**Root cause.** `validate_oc_agents.py --all` reports 1031 of 1321 machines
warning `input axes derived from inputSemantics (no length-matched
sensorNormalization)`. `sensorNormalization` is present on only 460 machines.
Where it exists the sidecar derives real axis names; where it does not the
sidecar falls back to `inputSemantics` and kebab-cases it, while
`openClawProjection.semantics` was backfilled snake-cased from a different
source. The formatting classes are that fallback; the 9 are machines where the
two sources genuinely describe different quantities.

### C2 — the completion artifact uses two machine-key conventions

`machine` in `corpus.pe-source-mappings.json` resolves as: **996** file stem,
**3** display name, **211** ambiguous (both resolve), **6** neither. The six
that resolve to nothing — `activity-monitor`, `health_infrastructure_support`
(×2), `medication-adherence` (×2), `sleep-quality` — look like trigger process
ids rather than machine keys.

Any consumer joining this artifact to the corpus must try both conventions.
A join on one convention silently under-reports; that is how the regression
corpus was first read as 4 mapped machines when it is 5.

## Redundancy

### R1 — the sidecar corpus has no abstraction

All 1320 sidecars share one `role` (`input-analyst`), one `mode` (`advise`),
one `templateId`, and **one** `outputContract`. Only the system prompt and the
response mapping vary. By bytes, across a 400-spec sample: system prompt 14%,
response mapping 43%, **boilerplate 42%**. 1136 of 1320 response mappings have
exactly 4 fields.

That 42% is the material cost behind #15 — 27 MB of specs and 32 MB of
generated workspaces, most of it the same text repeated 1320 times.

### R2 — two abstraction levels for one relationship

The assessment axis expands to one agent per machine (1320). The completion
axis collapses to 31 role families over 1150 machines. These are not competing
implementations of one design; they are two different designs, and no document
states which is intended to survive.

## Omissions

| id | finding |
|---|---|
| O1 | **`HealthKitVitalsMonitor` is the only machine with no agent** — and it is also isolated in the interconnection graph. It is the HealthKit bridge's RE target. |
| O2 | **175 machines have no completion mapping**: legal-services 99, digital-logic **63 (the entire domain)**, health-personal 5, community-services 4, agriculture 3, data-center 1. |
| O3 | **4 declared agent families are never realized**: `logic_validation_agent` (digital-logic), `ip_docketing_agent` and `claims_review_agent` (legal-services), `transit_cleaning_agent` (transportation). O2 and O3 are the same hole seen from both ends. |
| O4 | **The ontology had no agent representation.** `re:OpenClawProjection` modelled the write-back slot; there was no `re:Agent`, family, autonomy mode, completion mapping, or axis naming. Addressed in this change — see below. |
| O5 | **ABox coverage is 43/1321 (3.3%)**, health-personal only; 24 of the 43 carry a projection. `abox-manifest.json` tracks all 1321 by sha256, but that is drift tracking, not semantic coverage. |
| O6 | **5 machines are fully isolated**, two of them named `…Interconnect` with `machineClass: bridge` and no interconnections: `HomeCaregiverSupportResponseInterconnect`, `HomeFoodSecurityResponseInterconnect`. Also `FallSensorMotionPreaggregator`, `HealthKitVitalsMonitor`, `CommunityCommandAgent`. |
| O7 | 137 machines carry no `interconnections` block at all. |

O2 explains the `OpenClawCompletionE2E` gap in issue #16 directly: that fixture
is a digital-logic machine, and no digital-logic machine has a completion
mapping because every one of them resolves to `observe` under
`logic_validation_agent`, and observe agents write nothing.

## Interconnections

The graph itself is sound: **1252 edges, 0 dangling references**, and all 1239
producer-declared `targetInputRegion` values match the consumer's real input
region exactly.

Its *shape* is worth stating plainly, because it is not what "interconnected
corpus" suggests:

| | count |
|---|--:|
| pure producers (out-edges only) | 1173 |
| pure consumers (in-edges only) | 132 |
| both producer and consumer | **11** |
| isolated | 5 |

Edges are 1242 `published-domain-bus-producer` and 10
`published-domain-bus-consumer`. The corpus is a two-layer star: leaf machines
publish to domain interconnect hubs, and the hubs terminate. Multi-hop
composition exists in exactly two domains — health-personal (8 machines) and
health-services (3). The other ten domains have no machine that both consumes
and produces.

## What this change updates

**OWL** — `RealityEngine_Machines/semantics/ontology/re-core.ttl` → `0.2.0`,
additive:

- classes `re:Agent`, `re:AgentFamily`, `re:AgentBinding`, `re:AutonomyMode`,
  `re:SemanticAxis`, `re:CompletionMapping`, `re:ResponseMapping`
- object properties for binding, family membership, write-back target,
  completion ownership, and family declaration
- datatype properties for agent id, family, trigger, allowed actions, axis
  index/name/provenance, sensor id, and completion region
- canonical individuals for the four autonomy modes, pairwise `owl:differentFrom`
- audit axioms: `re:UnrealizedAgentFamily` (O3 becomes a derived class rather
  than a manual query); `re:ObserveBinding` may hold no semantic axis (the O2
  invariant); every binding names exactly one agent

`re:axisName` is deliberately `owl:FunctionalProperty`: one position of one
region carries one meaning, so C1's 1127 disagreements are an *inconsistency a
reasoner reports*, not a spelling variant nothing compares.

**JSON Schema** — `machine-behaviors/schemas/pe-source-mappings.schema.json`,
new. `verify_schemas.py` check (c) previously hand-checked four keys; it now
validates the artifact against the schema and keeps only the cross-field
invariants a schema cannot state (pointer count vs region length, regions
inside the reserved band, declared count vs actual).

## Not done here

- No corpus data was changed. Every finding above is reported, not repaired;
  C1's 9 genuine conflicts and O1 need a decision about which side is
  authoritative before anything is rewritten.
- The new OWL axioms are **not reasoner-verified**: ROBOT is not installed on
  this host, so `npm run owl:reason` skips. The structural gates
  (`owl_semantics_test.py`, `generate-owl --check`) pass, and `rdflib` is
  likewise unavailable, so the TTL has not been machine-parsed.
- `generate-owl.py` does not yet emit the new agent individuals; the vocabulary
  exists, the ABoxes do not use it.
