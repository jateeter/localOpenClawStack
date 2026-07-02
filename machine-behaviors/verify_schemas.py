#!/usr/bin/env python3
"""OpenClaw round-trip schema deployment gate.

Static, dependency-free (minischema) verification that the OpenClaw round-trip
artifacts conform to their schemas. Runs before the stack is brought up.

  (a) every materialized agents/**/*.oc-agent.json  vs templates/oc-agent.schema.json
  (b) a representative OpenClaw completion payload   vs the corpus
      localai-completion-writeback.schema.json
  (c) the PE input-bridge source-mapping artifact    (shape: id/sensorId/region/extract)

Exits non-zero on any violation. Mirrors the machine-behaviors test coverage
(corpus suite C4/C5) as a self-contained gate for the deployment path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import minischema  # noqa: E402
from derive_agents import derive, load_config, _abs  # noqa: E402

OC_SCHEMA = HERE / "templates" / "oc-agent.schema.json"
CFG = load_config()
COMPLETION_SCHEMA = _abs(CFG["schemasDir"]) / "localai-completion-writeback.schema.json"

fails = 0


def report(label: str, errors: list[str]) -> None:
    global fails
    if errors:
        fails += 1
        print(f"  FAIL {label}")
        for e in errors[:5]:
            print(f"       {e}")
    else:
        print(f"  ok   {label}")


# (a) every agent instance vs oc-agent schema ---------------------------------
oc_schema = minischema.load_schema(OC_SCHEMA)
agents = sorted((HERE / "agents").rglob("*.oc-agent.json"))
bad = []
for a in agents:
    errs = minischema.validate(json.loads(a.read_text()), oc_schema)
    if errs:
        bad.append(f"{a.relative_to(HERE)}: {errs[0]}")
report(f"{len(agents)} agent instances vs oc-agent.schema.json", bad[:5])

# (b) representative completion payload vs corpus completion schema -----------
comp_schema = minischema.load_schema(COMPLETION_SCHEMA)
plan = derive(_abs(CFG["machinesDir"]) / "HomeChronicPainMonitor.json", CFG)
agent = next(a for a in plan["agents"] if a["realityVectorImpact"])
wb = agent["agentBinding"]["writeBack"]
completion = {
    "provider": "acp",
    "agent": agent["agent"],
    "completionId": "verify-sample",
    "correlationId": "verify-sample",
    "envelopeId": "verify-sample",
    "sensorId": wb["sensorId"],
    "name": wb["name"],
    "region": wb["region"],
    "sourceMapping": wb["sourceMapping"],
    "values": [1.0, 0.0, 0.9, 0.75, 1.0][: wb["region"]["length"]],
    "ttlMs": wb["ttlMs"],
    "triggerPush": wb["ingest"]["triggerPush"],
    "compactPush": wb["ingest"]["compactPush"],
}
report("sample OpenClaw completion vs localai-completion-writeback.schema.json",
       minischema.validate(completion, comp_schema))

# (c) PE input-bridge source-mapping artifact shape ---------------------------
sm_path = HERE / "pe-integration" / "corpus.pe-source-mappings.json"
sm_errs = []
if sm_path.exists():
    doc = json.loads(sm_path.read_text())
    mappings = doc.get("sourceMappings", [])
    if not isinstance(mappings, list) or not mappings:
        sm_errs.append("sourceMappings missing or empty")
    for m in mappings[:5000]:
        for k in ("id", "sensorId", "region", "extract"):
            if k not in m:
                sm_errs.append(f"{m.get('id','?')}: missing '{k}'")
        r = m.get("region", {})
        if not (isinstance(r.get("offset"), int) and isinstance(r.get("length"), int) and r["length"] >= 1):
            sm_errs.append(f"{m.get('id','?')}: bad region {r}")
        ptrs = m.get("extract", {}).get("pointers", [])
        if len(ptrs) != r.get("length"):
            sm_errs.append(f"{m.get('id','?')}: pointers({len(ptrs)}) != region.length({r.get('length')})")
    report(f"{len(mappings)} PE source mappings shape", sm_errs[:5])
else:
    report("PE source-mappings artifact present", [f"missing {sm_path.name} (run domain_sweep.py --all --write)"])

print(f"\n  schema gate: {'PASS' if not fails else f'{fails} check(s) FAILED'}")
sys.exit(1 if fails else 0)
