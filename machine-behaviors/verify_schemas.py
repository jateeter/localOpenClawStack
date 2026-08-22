#!/usr/bin/env python3
"""OpenClaw round-trip schema deployment gate.

Static, dependency-free (minischema) verification that the OpenClaw round-trip
artifacts conform to their schemas. Runs before the stack is brought up.

  (a) every materialized agents/**/*.oc-agent.json  vs templates/oc-agent.schema.json
  (b) a representative OpenClaw completion payload   vs the corpus
      localai-completion-writeback.schema.json
  (c) pe-integration/corpus.pe-source-mappings.json   vs schemas/pe-source-mappings.schema.json
      plus the cross-field invariants a schema cannot state (pointer count vs
      region length, regions inside the reserved band, declared count vs actual)

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
from derive_agents import derive, load_config, _abs, resolve_machine_file  # noqa: E402

OC_SCHEMA = HERE / "templates" / "oc-agent.schema.json"
SOURCE_MAPPINGS_SCHEMA = HERE / "schemas" / "pe-source-mappings.schema.json"
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
plan = derive(resolve_machine_file(_abs(CFG["machinesDir"]), "HomeChronicPainMonitor.json"), CFG)
# Pick a write-back agent by its binding, not by realityVectorImpact: per-agent
# regions were retired (#26) and that field is None for every agent now, which
# made this a StopIteration rather than a wrong answer.
agent = next(a for a in plan["agents"]
             if a["agentBinding"]["writeBack"].get("type") == "pe-sensor")
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

# (c) PE input-bridge source-mapping artifact ---------------------------------
#
# Shape is enforced by schema now; what stays here are the cross-field
# invariants a schema cannot state: pointer count against region length,
# every region inside the declared reserved band, and the declared count
# against the actual one.
# Every mapped region must be one the corpus actually allocates.
#
# The per-machine completion band this artifact targets — 17000-22307 — was
# retired in RealityEngine_Machines#63/#64. region-allocation.json now declares
# reservedBands: [] and a vectorBudget.maxCellExclusive of 16944, and completions
# reach the vector through a single `acp-openclaw-completion` service lane at
# [4210:4214] instead. RealityEngine_CI retired its half in #120: its
# integrations.json went from 1,624 mappings to 408, and 0 now target 17000+.
#
# So a mapping above the corpus footprint writes cells no allocation declares and
# nothing reads. That was true before this check existed and nothing said so,
# which is how 1,216 of them survived a band retirement. See #16, #18, #23.
def _allocated_cells(machines_root: Path) -> tuple[set, int]:
    allocation = machines_root / "domains" / "region-allocation.json"
    if not allocation.exists():
        return set(), 0
    doc = json.loads(allocation.read_text())
    cells = set()
    for lane in (doc.get("serviceLanes") or []) + (doc.get("reservedBands") or []):
        offset, length = lane.get("offset"), lane.get("length")
        if isinstance(offset, int) and isinstance(length, int):
            cells |= set(range(offset, offset + length))
    budget = (doc.get("vectorBudget") or {}).get("maxCellExclusive") or 0
    return cells, budget


sm_path = HERE / "pe-integration" / "corpus.pe-source-mappings.json"
sm_errs = []
if sm_path.exists():
    doc = json.loads(sm_path.read_text())
    sm_errs.extend(minischema.validate(doc, minischema.load_schema(SOURCE_MAPPINGS_SCHEMA)))
    mappings = doc.get("sourceMappings", [])
    if isinstance(mappings, list):
        declared = doc.get("count")
        if isinstance(declared, int) and declared != len(mappings):
            sm_errs.append(f"count({declared}) != len(sourceMappings)({len(mappings)})")
        machines_root = Path(__file__).resolve().parents[2] / "RealityEngine_Machines"
        allocated, budget = _allocated_cells(machines_root)
        if budget:
            stray = []
            for m in mappings:
                r = m.get("region") or {}
                offset, length = r.get("offset"), r.get("length")
                if not isinstance(offset, int) or not isinstance(length, int):
                    continue
                span = set(range(offset, offset + length))
                if offset >= budget and not (span & allocated):
                    stray.append(f"{m.get('id', '?')}@{offset}")
            if stray:
                sm_errs.append(
                    f"{len(stray)} mapping(s) target cells no allocation declares, above "
                    f"vectorBudget.maxCellExclusive={budget}: {', '.join(stray[:3])}"
                    + (" ..." if len(stray) > 3 else "")
                    + " — the per-machine completion band was retired; completions now"
                    " use the acp-openclaw-completion service lane"
                )
        band = doc.get("reservedBand") or []
        low, high = (band + [None, None])[:2]
        for m in mappings:
            r = m.get("region") or {}
            off, ln = r.get("offset"), r.get("length")
            ptrs = (m.get("extract") or {}).get("pointers") or []
            if isinstance(ln, int) and len(ptrs) != ln:
                sm_errs.append(f"{m.get('id','?')}: pointers({len(ptrs)}) != region.length({ln})")
            if isinstance(low, int) and isinstance(high, int) and isinstance(off, int) and isinstance(ln, int):
                if off < low or off + ln - 1 > high:
                    sm_errs.append(f"{m.get('id','?')}: region [{off}:{off + ln}] outside reservedBand [{low}:{high}]")
    report(f"{len(mappings)} PE source mappings vs pe-source-mappings.schema.json", sm_errs[:5])
else:
    # Absent is now correct. The per-machine completion band this artifact
    # targeted was retired in RealityEngine_Machines#63/#64; completions use the
    # acp-openclaw-completion service lane at [4210:4214]. The check that used to
    # demand the file is inverted: if it reappears, the allocation guard above
    # will fail it. See #16, #18, #23.
    report("PE source-mappings artifact retired (completion band withdrawn)", [])

print(f"\n  schema gate: {'PASS' if not fails else f'{fails} check(s) FAILED'}")
sys.exit(1 if fails else 0)
