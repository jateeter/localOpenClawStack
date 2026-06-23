# Corpus-wide OpenClaw agent automation (all domains)

The Personal Health template (`README.md` + `RESPONSE_MAPPING.md` +
`DOMAIN_HEALTH_PERSONAL.md`) applied to **every machine in every domain**.
Read-only w.r.t. machine `.json` and PE loaders; the only corpus-repo edits are
the reserved-range registration in `domain-registry.json` and its enforcement in
`audit-corpus.py`.

```bash
cd localOpenClawStack/machine-behaviors
MB_DEBUG=0 python3.13 domain_sweep.py --all            # text report
MB_DEBUG=0 python3.13 domain_sweep.py --all --write    # out/corpus.{agents.json,report.md,pe-source-mappings.json}
bash tests/run_all.sh                                  # 82 checks (prototype + domain + corpus)
```

## Coverage

| metric | value |
|---|---|
| machines | **1175** (all `machines/**`, incl. `machines/domains/energy/`) |
| CES behaviors | **4531** |
| agent bindings | **1605** (write-back **1047**, observe **558**) |
| PE source mappings | **1047** (`out/corpus.pe-source-mappings.json`, committed copy in `pe-integration/`) |
| reserved completion band | **`[7300:12300]`**, used `7300..11932` (corpus max offset = 7280) |
| schema validation errors | **0** (every binding vs `agent-binding.schema.json`, every mapping vs `response-mapping.schema.json`) |
| region collisions | **0** |
| low-confidence selections | **8** (0.18%) |

### Per-domain

| domain | machines | behaviors | bindings |
|---|--:|--:|--:|
| health-services | 200 | 600 | 262 |
| energy | 160 | 640 | 160 |
| built-space | 150 | 750 | 162 |
| transportation | 150 | 600 | 302 |
| community-services | 103 | 398 | 180 |
| legal-services | 100 | 400 | 127 |
| life-balance | 100 | 400 | 102 |
| agriculture | 64 | 249 | 139 |
| data-center | 59 | 274 | 64 |
| digital-logic | 57 | 64 | 57 |
| health-personal | 24 | 94 | 41 |
| ai-services | 8 | 62 | 9 |

Each write-back agent has the full OC workflow: derived `agentBinding` (agent,
mode, trigger, allowedActions, autonomyPolicy, riskControls, writeBack), OpenClaw
template values, a `responseMapping` (textual→value, N positions), and a **PE
source mapping** registering its completion sensor so PE can ingest the response
and RE can read it.

## PE source mappings (the response → PE wiring)

`out/corpus.pe-source-mappings.json` (committed at
`pe-integration/corpus.pe-source-mappings.json`) holds one entry per write-back
agent, shaped like `RealityEngine_CPP/config/integrations.example.json`
`sourceMappings`: `id`, `sensorId` (`acp.openclaw.<machine>.<agent>.completion`),
`region` (in the reserved band), `extract.pointers` (one JSON pointer per
position, matching the response mapping), `normalize`, `ttlMs`, `pushMode`. Drop
the array into the PE `INTEGRATIONS_CONFIG` so completions land via
`POST /api/integrations/completions`.

## Issues / roadblocks found (and resolution)

1. **Subdirectory corpus was invisible (fixed).** `glob` missed
   `machines/domains/energy/` (160 machines). Discovery and the corpus-max scan
   now use `rglob`; a test asserts energy is covered.
2. **Reserved band was mis-sized and mis-placed (fixed).** M5 sized the band from
   the top-level max (5507); the true corpus max is **7280** and the band needed
   ~4632 positions. The reserved range was moved to **`[7300:12300]`** (above the
   corpus max, headroom to 12300) in `domain-registry.json`; the sweep allocates
   within it and fails on overflow; the audit blocks machine intrusion.
3. **Sidecar vs curated bindings — by design, needs a reconciliation decision.**
   1056 machines are already `agent-dispatcher` with a *curated* in-corpus
   `agentBinding`. The sweep derives an **independent sidecar** binding from
   `triggerConfig` + domain families; it does not read or overwrite the curated
   one. Promoting/merging sidecar ↔ curated for those machines is a separate,
   reviewed corpus change (out of scope here).
4. **Observe-heavy corpus.** 558/1605 agents resolve to `observe` (positive/OK CES
   states) and write no PE vector; 1047 produce PE sources. Expected, but it means
   "agent bindings" ≠ "PE sources".
5. **Region offsets depend on file sort order.** Allocation is deterministic but
   shifts if machines are added/removed. Follow-up: stable keys
   (`hash(machineCode:agent)`) so a machine's region is invariant.
6. **Generic default mapping still present.** `integrations.example.json` keeps the
   single `acp-openclaw-completion` mapping (offset 4210); the per-agent mappings
   here supersede it. PE should load the generated array.
7. **8 residual low-confidence selections** (all Personal Health sparse-text
   machines) — see `DOMAIN_HEALTH_PERSONAL.md` improvement list.
