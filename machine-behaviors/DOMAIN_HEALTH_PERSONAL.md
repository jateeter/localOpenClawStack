# Personal Health domain — OpenClaw agent automation

Extends the single-machine prototype (`README.md`) to the **entire `health-personal`
domain** via `domain_sweep.py`. Read-only w.r.t. the corpus and PE loaders; every
derived binding is sidecar.

```bash
cd localOpenClawStack/machine-behaviors
MB_DEBUG=0 python3.13 domain_sweep.py                 # text coverage report
MB_DEBUG=0 python3.13 domain_sweep.py --md            # markdown report
MB_DEBUG=0 python3.13 domain_sweep.py --write         # out/health-personal.{agents.json,report.md}
bash tests/run_all.sh                                 # prototype + domain suites (41 checks)
```

## What the sweep adds over the single-machine deriver

1. **Domain discovery** by `metadata.tagging.primaryDomain` / `category`.
2. **Corpus-wide band reservation (the M5 fix):** it scans every machine's
   `perceptualMapping` and reserves the completion band *above* the highest
   offset used anywhere in the corpus (rounded to 100). The prototype's `4400`
   default is a fragile gap; the sweep auto-picks `5600` because the corpus
   already reaches offset **5507**.
3. **Global region allocation:** completion regions are handed out from a single
   monotonic cursor, so no two agents across the whole domain share a vector.
4. **Batch schema validation** of every binding + **collision/diagnostic report**.

## Coverage snapshot (regenerate with `--md`)

| metric | value |
|---|---|
| machines | **24** |
| CES outputs (behaviors) | **94** |
| agent bindings | **41** (write-back 37, observe 4) |
| reserved completion band | within the registry-reserved band `[7300:12300]` (variable-length regions 4/5/6 per autonomy — see [`RESPONSE_MAPPING.md`](RESPONSE_MAPPING.md) and [`CORPUS_COVERAGE.md`](CORPUS_COVERAGE.md)) |
| schema validation errors | **0** |
| region collisions | **0** |
| low-confidence selections | **8 / 94 (8.5%)** |

Machine classes: signal-monitor ×15, risk-forecaster ×6, sensor-preaggregator ×1,
safety-compliance-checker ×1, outcome-stabilizer ×1.
Autonomy: supervised-act 69, observe 23, advise 2 — **no `automated-act`**
(life-safety domain ceiling, enforced and tested).
Agent families used: `wellness_coach_agent`, `caregiver_support_agent`,
`fall_risk_agent`, `medication_adherence_agent`.

The full per-machine → agent → reality-vector-region map is in
`out/health-personal.report.md`.

## Agent selection model (and how it was hardened here)

For each CES output the agent is chosen by whole-token scoring of the output's
action/label/description against each domain agent family's name tokens (expanded
through a generic lexicon). Two improvements were made while scaling to the domain:

- **Whole-token matching** replaced substring matching. Substring matching had
  `sleep-poor` selecting `medication_adherence_agent` because the lexicon cue
  `"ort"` (the ORT opioid tool) matched inside *"Short"*. Now fixed and tested.
- **Enriched fallback:** outputs with no per-output keyword signal are re-scored
  against machine-level context (`description`, `tags`, `sensorNormalization`
  keys, `populationFocus`, `outputSpace`) before defaulting. This cut
  no-signal selections from **24 → 8** (26% → 8.5%). Each output records
  `selectionScore`, `selectionBasis` (`per-output` | `machine-context`), and
  `lowConfidence` for auditability.

## Testing

`tests/run_domain_tests.py` (14 checks) covers: full 24-machine discovery, every
binding schema-valid, global region non-overlap, band-above-corpus-max,
band-clear-of-entire-corpus, autonomy ceiling per machine class, no `automated-act`,
selection diagnostics present, low-confidence under 15%, and determinism.
`tests/run_tests.py` (27 checks) still covers the single-machine prototype.

---

## Opportunities for improvement of the process

Found while scaling the automation across the domain. Ordered by value.

### Corpus / data quality (the automation doubles as a linter)
1. **Domain mis-tagging.** `FacilitiesMaintenance.json` carries
   `primaryDomain: health-personal` but `domain: "Facilities Management —
   Eldercare Integration"` and is plainly a facilities machine. The sweep
   faithfully assigns it caregiver/fall/wellness agents, which is nonsensical.
   → Add a domain-coherence check (name/description vs `primaryDomain`) and route
   such machines to triage. `newpatientinflow`/`dailypatientcare` show the same
   smell (generic "Decision"/"Event" labels — likely health-services, not
   health-personal).
2. **Empty `outputVectors[].metadata.action`.** Several machines (e.g.
   `SleepQualityMonitor`) ship no action text, which is the strongest selection
   signal. → Backfill action text, or treat its absence as a corpus-quality gate.

### Selection precision
3. **`caregiver_support_agent`/`wellness_coach_agent` over-assignment.** Together
   they take ~36/41 bindings. The generic lexicon + first-family default biases
   toward them. → Per-domain weighted lexicons, a minimum-score threshold before
   accepting a pick, or an LLM-assisted classifier (an OpenClaw agent could *self-
   propose* its trigger fit) with the keyword scorer as the cheap pre-filter.
4. **8 residual low-confidence outputs.** Mostly a raw motion preaggregator and a
   generic workflow machine. → For non-dispatch classes (`sensor-preaggregator`),
   prefer routing CES state to a *downstream* `agent-dispatcher` instead of
   assigning a direct acting agent at all (matches the agent-ready-class `emitsTo`
   contract).

### Region governance (M5 — done; durable)
5. **Done.** The band is now persisted as a reserved range in
   `domains/domain-registry.json → rangePolicy.reservedRanges`
   (`acp-openclaw-agent-completions`, `[7300:12300]`, `ownership: exclusive`,
   `provider: acp`). `scripts/audit-corpus.py` now warns (→ strict error) if any
   machine maps input/output into a reserved band, and the sweep allocates *within*
   the registry-declared band and fails on overflow — registry is the single
   source of truth. (This is the one change that intentionally edits the corpus
   repo — registry + audit script, **not** machine `.json` or PE loaders.)
   Remaining nicety: stable region keys (hash of `machineCode:agent`) so offsets
   don't shift when files are added/renamed.

### Liveness & promotion
6. **No live verification yet** (M6/M7). → Drive `--live` against a running PE,
   assert the dispatch lands in `/api/dispatch/ledger`, the completion updates the
   sensor, and a downstream RE transition fires; then replace the simulated turn
   with a real `openclaw acp` session.
7. **Promotion path.** Derived bindings are schema-identical to in-corpus
   `agentBinding`. → Offer an opt-in `--emit-binding` that prints a corpus-ready
   patch for review, so a stable machine can be promoted to `agent-dispatcher`
   *as an explicit, reviewed corpus change* — never silently.

### Scale-out
8. The deriver is already domain-agnostic (it handled a `health-services`
   dispatcher unchanged). → Run `domain_sweep.py --domain <other>` across the
   remaining accepted domains and aggregate region pressure centrally so bands
   never overlap *between* domains.
