# Agent response → PE event-vector mapping

How an OpenClaw agent's reply is turned into perceptual-space (reality) vector
values on the write-back leg of the round trip. This is the contract the prior
review flagged as missing: an OpenClaw agent returns **natural language**, but PE
sources are **numeric vectors**, and one response usually has to drive **several
positions**. The mapping is now declared per agent in **`responseMapping`** and is
schema-validated (`schemas/response-mapping.schema.json`).

## Why it's needed

The dispatch leg is already well-defined (`ces.terminal.event` envelope). The
write-back leg was not: `openclaw_side` used to *fabricate* the numbers. Three
requirements drove this design:

1. **Textual → value.** The agent's words must map deterministically to `[0,1]`.
2. **Multiple positions.** A single completion writes N positions (e.g.
   `completed, failed, confidence, actionClass, review_required`), and may fan
   out to more than one non-contiguous region.
3. **Declared in the schema.** Each round trip carries its own mapping so it is
   self-describing and auditable — not hidden in code.

## The contract: `responseMapping`

```jsonc
"responseMapping": {
  "responseContract": "structured-keys-or-text",
  "mode": "supervised-act",
  "fields": [
    {
      "semantic": "actionClass",
      "valueType": "scalar",
      "normalization": "scalar-0-1",
      "extract": {
        "jsonPointer": "/verdict",          // tried first if the turn is JSON
        "responseKey": "verdict",            // the key in a structured-keys turn
        "textFallback": {                    // text → value rule
          "type": "enum-keyword",
          "default": 0.25,
          "keywords": { "observed": 0.25, "advised": 0.5, "staged": 0.75, "executed": 1.0 }
        }
      },
      "target": { "sensorId": "acp.openclaw.<m>.<agent>.completion",
                  "region": { "offset": 4403, "length": 5 }, "index": 3 }
    }
    // … one field per position …
  ]
}
```

Each **field** = (what to extract) + (how to turn text into a number) +
(**which vector position** it lands at: `target.sensorId` + `region` + `index`).

### Positions scale with autonomy

| mode | positions (region length) | semantics |
|---|---|---|
| `observe` | 0 (no write-back) | — |
| `advise` | 4 | completed, failed, confidence, actionClass |
| `supervised-act` | 5 | + review_required |
| `automated-act` | 6 | + executed, rollback_ok |

The `writeBack.region.length` and `writeBack.semantics` are kept in lock-step with
these fields (tested by `D5b.2`), so the corpus-side write-back stays consistent
with the response mapping.

## Extraction order (deterministic)

For each field, `openclaw_side.apply_response_mapping`:

1. **JSON pointer** — if the agent returned JSON, read `extract.jsonPointer`;
   booleans → 0/1, numbers → clamped (a value > 1 is treated as a percentage).
2. **Structured-keys text** — else read the `responseKey: value` line and apply
   the `textFallback` rule to **just that value** (so the field label can't match
   itself).
3. **Missing contracted key** in a structured turn → the rule's `default` (never a
   whole-text scan — that previously let `review_required` pick up `yes` from the
   `completed:` line).
4. **Pure free-text turn** (no `key:` lines) → best-effort whole-text scan.

`textFallback` types: `enum-keyword` (whole-token / phrase match → value) and
`scalar-phrase` (phrase map, e.g. `high→0.9`, plus a numeric regex). Whole-token
matching is used throughout (the `"ort"`-in-`"Short"` lesson from selection).

## Multiple vector positions, including non-contiguous

Fields are grouped by their `target` region, producing **one PE completion
payload per region**. Because each field names its own `sensorId`/`region`, a
single response can update several distinct, non-contiguous positions — e.g. the
agent's completion block **and** a separate downstream escalation bit. The default
deriver lays the standard semantics out contiguously in one region; the schema and
extractor support the fan-out (tested by `T9.13`).

## Worked example (Home Chronic Pain Monitor, PAIN_CRISIS, RED)

Agent turn (structured-keys text):

```
verdict: blocked
completed: no
failed: yes
confidence: high
review_required: yes (escalate to governance; human review required)
```

Mapped → completion at `[4400:4405]`:

```
completed=0.0  failed=1.0  confidence=0.9  actionClass=0.25  review_required=1.0
```

RED is blocked from direct action, so `failed=1` and `review_required=1` — RE
reads this on the next push and the governance-escalator path takes over. The raw
response text **and** the extracted values are both logged on the OpenClaw side
(`openclaw.agent-response`, `openclaw.response-mapped`) for audit.

## Tests

`tests/run_tests.py` T9.* covers schema validity, every text→value rule
(`yes→1`, `no→0`, `high→0.9`, `staged→0.75`), the missing-key default, the JSON
path, value/length agreement, and multi-region fan-out. `tests/run_domain_tests.py`
D5b.* asserts every agent in the domain has a valid mapping in lock-step with its
write-back region.

## Known limitation / next step

`responseMapping` lives in the **sidecar** agent record, not inside the corpus
`agentBinding` — the corpus `agent-binding.schema.json` sets
`additionalProperties: false`, so adding it there would require a corpus schema
change (an optional `responseMapping` property). That promotion is a reviewed
corpus change, deliberately out of scope here; the write-back `region`/`semantics`
that the corpus *does* model already reflect the affected positions.
