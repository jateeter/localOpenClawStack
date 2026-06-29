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
| machines | **1320** (all `machines/**`, incl. `machines/domains/energy/`) |
| CES behaviors | **5114** |
| agent bindings | **1782** (write-back **1216**, observe **566**) |
| PE source mappings | **1216** (`out/corpus.pe-source-mappings.json`, committed copy in `pe-integration/`) |
| reserved completion band | **`[17000:25000]`**, used `17000..22311` (corpus max offset = 16920) |
| schema validation errors | **0** (every binding vs `agent-binding.schema.json`, every mapping vs `response-mapping.schema.json`) |
| region collisions | **0** |
| low-confidence selections | **187** (3.7%) |

### Per-domain

| domain | machines | behaviors | bindings |
|---|--:|--:|--:|
| health-services | 220 | 680 | 302 |
| energy | 187 | 748 | 187 |
| built-space | 165 | 810 | 177 |
| transportation | 165 | 660 | 317 |
| community-services | 113 | 438 | 190 |
| legal-services | 110 | 440 | 137 |
| life-balance | 110 | 440 | 112 |
| agriculture | 71 | 277 | 146 |
| data-center | 65 | 298 | 70 |
| digital-logic | 63 | 88 | 63 |
| health-personal | 42 | 169 | 71 |
| ai-services | 9 | 66 | 10 |

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
2. **Reserved band tracks corpus growth (durable).** M5 originally sized the band
   from the top-level max (5507); the corpus has since grown (max offset now
   **16920**), and the reserved range was widened/relocated to **`[17000:25000]`**
   in `domain-registry.json` — always above the corpus max with headroom. The
   sweep allocates within it (currently `17000..22311`) and fails on overflow; the
   audit blocks any machine from mapping into the band.
3. **Sidecar vs curated bindings — by design, needs a reconciliation decision.**
   1056 machines are already `agent-dispatcher` with a *curated* in-corpus
   `agentBinding`. The sweep derives an **independent sidecar** binding from
   `triggerConfig` + domain families; it does not read or overwrite the curated
   one. Promoting/merging sidecar ↔ curated for those machines is a separate,
   reviewed corpus change (out of scope here).
4. **Observe-heavy corpus.** 566/1782 agents resolve to `observe` (positive/OK CES
   states) and write no PE vector; 1216 produce PE sources. Expected, but it means
   "agent bindings" ≠ "PE sources".
5. **Region offsets depend on file sort order.** Allocation is deterministic but
   shifts if machines are added/removed. Follow-up: stable keys
   (`hash(machineCode:agent)`) so a machine's region is invariant.
6. **Generic default mapping still present.** `integrations.example.json` keeps the
   single `acp-openclaw-completion` mapping (offset 4210); the per-agent mappings
   here supersede it. PE should load the generated array.
7. **187 low-confidence selections (3.7%, up from 8).** As the corpus grew (+145
   machines, incl. 136 new `bridge`-class machines), more outputs lack strong
   per-output keyword signal and fall back to machine-context or the first family.
   → The selection-precision follow-ups in `DOMAIN_HEALTH_PERSONAL.md` (per-domain
   lexicons, min-score threshold, LLM-assisted classifier) matter more at scale.
