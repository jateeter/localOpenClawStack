#!/usr/bin/env python3
"""Incremental tests for the Personal Health domain sweep (domain_sweep.py).

Run:  MB_DEBUG=0 python3.13 tests/run_domain_tests.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("MB_DEBUG", "0")
HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import minischema  # noqa: E402
from derive_agents import load_config, _abs  # noqa: E402
from domain_sweep import sweep, summarize, discover, corpus_max_end  # noqa: E402

CFG = load_config()
SCHEMAS = _abs(CFG["schemasDir"])
DOMAIN = "health-personal"

_PASS = _FAIL = 0


def check(name, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ok   {name}")
    else:
        _FAIL += 1
        print(f"  FAIL {name}  {detail}")


result = sweep(DOMAIN, CFG)
s = summarize(result)

# D1: discovery / coverage
machines_dir = _abs(CFG["machinesDir"])
discovered = discover(machines_dir, DOMAIN)
# The count comes from domain-manifest.json, the authoritative domain inventory,
# rather than a literal. It was 42 here and 43 in the corpus: HealthKitVitalsMonitor
# landed in RealityEngine_Machines on 2026-07-14 and this expectation was never
# updated. A hand-maintained count drifts silently every time the corpus grows.
_manifest = json.loads((_abs(CFG["machinesDir"]).parent / "domains" / "domain-manifest.json").read_text())
_expected = (_manifest.get("domains") or {}).get(DOMAIN, {}).get("currentMachineCount")
check(f"D1.1 discovered the full domain ({_expected} machines, per domain-manifest)",
      _expected is not None and len(discovered) == _expected,
      f"discovered={len(discovered)} manifest={_expected}")
check("D1.2 every discovered machine produced a plan", len(result["plans"]) == len(discovered))
check("D1.3 every machine yields >=1 agent",
      all(len(p["agents"]) >= 1 for p in result["plans"]),
      str([p["machine"]["code"] for p in result["plans"] if not p["agents"]]))

# D2: every derived agentBinding validates against the canonical schema
schema = minischema.load_schema(SCHEMAS / "agent-binding.schema.json")
bad = []
for p in result["plans"]:
    for a in p["agents"]:
        errs = minischema.validate(a["agentBinding"], schema)
        if errs:
            bad.append((p["machine"]["code"], a["agent"], errs[0]))
check("D2 all agentBindings schema-valid", not bad, str(bad[:3]))
check("D2b sweep reports zero validation errors", len(result["validationErrors"]) == 0)

# D3: completion lane governance
#
# The per-agent completion band was retired (RealityEngine_Machines#63/#64) and
# this repo's #26 settled the replacement: agents get NO region. Completions reach
# the vector through the single arbitrated acp-openclaw-completion lane at
# [4210:4214]. These checks assert the absence of allocation rather than being
# deleted — the old band assertions are replaced, not dropped, so a regression
# back to per-agent regions fails here.
lane = result["completionLane"]

check("D3.1 no agent claims a per-agent completion region",
      all(a["realityVectorImpact"] is None for p in result["plans"] for a in p["agents"]),
      str([a["agent"] for p in result["plans"] for a in p["agents"]
           if a["realityVectorImpact"] is not None][:3]))
check("D3.2 sweep reports zero region collisions", len(result["regionCollisions"]) == 0)
check("D3.3 sweep declares per-agent regions retired", result["perAgentRegions"] is False,
      str(result["perAgentRegions"]))

# D3.4: the lane is a declared service lane in region-allocation.json, and it is
# the one the config names as ACP_COMPLETION_SOURCE_MAPPING_ID.
_alloc = json.loads((_abs(CFG["machinesDir"]).parent / "domains" / "region-allocation.json").read_text())
_lanes = {sl["id"]: sl for sl in (_alloc.get("serviceLanes") or [])}
_declared = _lanes.get(lane["id"])
check("D3.4 completion lane is a declared service lane", _declared is not None, lane["id"])
check("D3.5 lane matches the registry's declared offset/length",
      bool(_declared) and _declared["offset"] == lane["offset"]
      and _declared["length"] == lane["length"],
      f"sweep={lane} registry={_declared}")
check("D3.6 lane is the configured completion source mapping id",
      lane["id"] == CFG["openclaw"]["completionSourceMappingId"],
      f"{lane['id']} != {CFG['openclaw']['completionSourceMappingId']}")

# D3.7: every write-back agent targets that lane and nothing else.
_wb = [(p["machine"]["code"], a["agent"], a["agentBinding"]["writeBack"])
       for p in result["plans"] for a in p["agents"]
       if a["agentBinding"]["writeBack"].get("type") == "pe-sensor"]
check("D3.7 every write-back agent targets the shared lane",
      all(w["sourceMapping"]["id"] == lane["id"]
          and w["region"] == {"offset": lane["offset"], "length": lane["length"]}
          for _, _, w in _wb),
      str([(c, a) for c, a, w in _wb if w["sourceMapping"]["id"] != lane["id"]][:3]))

# D3.8: nothing is emitted above the corpus footprint any more. This is the
# condition that stranded 1,216 mappings when the band was retired.
_budget = (_alloc.get("vectorBudget") or {}).get("maxCellExclusive", 0)
check("D3.8 no completion target sits above the corpus footprint",
      lane["offset"] + lane["length"] <= _budget,
      f"lane={lane} budget={_budget}")

# D4: autonomy safety — health domain must never reach automated-act
modes = [o["autonomyMode"] for p in result["plans"] for o in p["outputs"]]
check("D4.1 no automated-act anywhere in Personal Health", "automated-act" not in modes)
# machine-class autonomy ceilings are respected
registry = json.loads((_abs(CFG["registryPath"])).read_text())
arc = registry["agentReadyMachineClasses"]
violations = []
rank = {"observe": 0, "advise": 1, "supervised-act": 2, "automated-act": 3}
for p in result["plans"]:
    allowed = arc.get(p["machine"]["machineClass"], {}).get("allowedAutonomyModes", [])
    ceiling = max((rank[m] for m in allowed), default=3)
    for o in p["outputs"]:
        if rank[o["autonomyMode"]] > ceiling:
            violations.append((p["machine"]["code"], o["autonomyMode"], allowed))
check("D4.2 every output respects its machine-class autonomy ceiling", not violations, str(violations[:3]))

# D5: selection diagnostics present and improved
total_outputs = sum(len(p["outputs"]) for p in result["plans"])
low_conf = len(s["lowConfidenceSelections"])
check("D5.1 selection diagnostics attached", all(
    "selectionScore" in o and "selectionBasis" in o
    for p in result["plans"] for o in p["outputs"]))
check("D5.2 low-confidence rate under 15% (enriched fallback working)",
      low_conf / total_outputs < 0.15, f"{low_conf}/{total_outputs}")

# D5b: every agent across the domain carries a schema-valid responseMapping, and
# its targeted positions stay in lock-step with the writeBack region length.
rm_schema = minischema.load_schema(ROOT / "schemas" / "response-mapping.schema.json")
rm_bad = []
mismatch = []
for p in result["plans"]:
    for a in p["agents"]:
        errs = minischema.validate(a["responseMapping"], rm_schema)
        if errs:
            rm_bad.append((p["machine"]["code"], a["agent"], errs[0]))
        rv = a["realityVectorImpact"]
        if rv:
            positions = [f for f in a["responseMapping"]["fields"] if f["target"]]
            if not (rv["length"] == len(positions) == len(a["affectedPositions"])):
                mismatch.append((p["machine"]["code"], a["agent"]))
check("D5b.1 all responseMappings schema-valid", not rm_bad, str(rm_bad[:3]))
check("D5b.2 region length == targeted positions == affectedPositions everywhere",
      not mismatch, str(mismatch[:3]))

# D6: determinism
result2 = sweep(DOMAIN, CFG)
check("D6 sweep is deterministic",
      json.dumps(result, sort_keys=True) == json.dumps(result2, sort_keys=True))

print(f"\ncoverage: {s['machines']} machines, {s['cesOutputs']} behaviors, "
      f"{s['agentBindings']} bindings, {low_conf} low-confidence")
print(f"{_PASS} passed, {_FAIL} failed")
sys.exit(1 if _FAIL else 0)
