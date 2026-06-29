# PE ⇄ OpenClaw agent bridges

The OpenClaw agents were already materialized (`agents/`, one input-analyst per
machine). This documents the **bridges from PE** that feed them, plus the corpus
additions/corrections that surfaced while wiring them.

Two bridge directions close the loop:

| direction | who writes | PE source mapping | target region |
|---|---|---|---|
| **in from PE → agent** | input-analyst agent writes the machine's normalized **input** vector | `acp-<code>-input-assessment` | the machine's **own input** region |
| **agent → back to PE** | output-actor agent writes a **completion** vector | `acp-<machine>-<agent>-completion` | reserved band `[17000:25000]` |

This file covers the **input** bridges (`register_input_mappings.py`). The
completion bridges are in `corpus.pe-source-mappings.json`.

## Coverage — 400 input bridges registered corpus-wide

`register_input_mappings.py --all --write` merged **400** leaf input mappings into
`RealityEngine_CI/config/integrations.json` and `integrations.example.json`
(+387 added, 13 updated, 0 pruned — it did not disturb prior registrations). The
400 leaf regions are mutually non-overlapping and clear of the sensor-integration
band `[4200:4320]` (tested: corpus suite C6).

Each machine's input region is classified before it gets a bridge:

| class | meaning | bridged? |
|---|---|---|
| **leaf** | input region is the machine's own, agent-suppliable | **yes — 400** |
| **bridge-fed** | input region *is another machine's output* (fed by composition / event-bus) | no — 583, fed upstream |
| **collide** | input region overlaps another machine's input | no — 332, needs deconfliction |
| **skip** | input region overlaps the real sensor-integration band | no — 5 |

### Per-domain

| domain | leaf (bridged) | bridge-fed | collide | skip |
|---|--:|--:|--:|--:|
| energy | **187** | 0 | 0 | 0 |
| agriculture | 27 | 16 | 28 | 0 |
| health-personal | 28 | 8 | 3 | 3 |
| digital-logic | 30 | 12 | 19 | 2 |
| community-services | 26 | 70 | 17 | 0 |
| health-services | 20 | 0 | 200 | 0 |
| transportation | 20 | 132 | 13 | 0 |
| legal-services | 17 | 93 | 0 | 0 |
| built-space | 16 | 140 | 9 | 0 |
| life-balance | 14 | 90 | 6 | 0 |
| data-center | 12 | 22 | 31 | 0 |
| ai-services | 3 | 0 | 6 | 0 |

## Corrections / additions to the corpus

### Addition (done)
- **`rglob` fix in `register_input_mappings.py`** (and `materialize_agents.py`,
  `validate_oc_agents.py`). The tools discovered machines with `glob` (top-level
  only), so the entire **energy domain (187 machines** in
  `machines/domains/energy/`) was invisible — it got **0** input bridges. With
  `rglob` energy is now fully bridged (187 leaf, no collisions). Added `--all`
  corpus scope so collision detection is global, not per-domain.

### Corrections needed (surfaced, not auto-applied — corpus-owner decisions)
- **332 input-region collisions** (586 intra-domain + 160 cross-domain edges):
  two machines map their *input* to the same perceptual-space window. An
  input-analyst can't author a shared region, so these machines have **no direct
  PE bridge** until the allocation is deconflicted (or the overlap is declared
  `overlay`/`bridge`/`deprecated` per `rangePolicy`).
  - **Cross-domain collisions** are the strongest signal (unrelated domains on the
    same offsets): community-services↔health-services (48), health-services↔
    life-balance (24), data-center↔digital-logic (22), agriculture↔digital-logic
    (18), community-services↔transportation (14), agriculture↔ai-services (10).
    The agriculture *indoor-grow-house* machines (`agx038–050`) overlapping
    digital-logic (`dlx002–014`) around offsets `3975–4035` are a clear example.
  - **health-services has 200 intra-domain collisions** — a large cluster of
    machines sharing input windows; worth a range-allocation review.
- **583 bridge-fed machines** are *not* an error — their input is a downstream of
  another machine's output, so RE composition feeds them (no agent needed). They
  are excluded by design; `--include-bridged` can register them if a machine
  should be agent-driven instead of composition-driven (a per-machine decision).

A machine-readable list of every collision/bridge/skip is in
`out/corpus.input-mappings.json` + the dry-run report
(`register_input_mappings.py --all`).

## Verify against a live PE (next)

```bash
# with a PE running (Scala 5000 / CPP 5300 / LSP 5600 — set pe.baseUrl)
python3.13 register_input_mappings.py --all --verify
```

`--verify` posts a deterministic firing input per leaf machine, pushes PE once,
and reports which machines transitioned (output region non-zero) — proving the
in-from-PE bridge drives a real CES, and that bridge-fed aggregators fire from
their upstream leaf outputs. Not run here (no live PE).
