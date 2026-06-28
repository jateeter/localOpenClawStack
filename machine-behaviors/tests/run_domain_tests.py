#!/usr/bin/env python3
"""Incremental tests for the Personal Health domain sweep (domain_sweep.py).

Run:  MB_DEBUG=0 python3.13 tests/run_domain_tests.py
"""

from __future__ import annotations

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
check("D1.1 discovered the full domain (42 machines)", len(discovered) == 42, str(len(discovered)))
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

# D3: global region governance
regions = [(a["realityVectorImpact"]["offset"],
            a["realityVectorImpact"]["offset"] + a["realityVectorImpact"]["length"])
           for p in result["plans"] for a in p["agents"] if a["realityVectorImpact"]]
ordered = sorted(regions)
overlaps = [(ordered[i - 1], ordered[i]) for i in range(1, len(ordered))
            if ordered[i][0] < ordered[i - 1][1]]
check("D3.1 no two agents share/overlap a completion region", not overlaps, str(overlaps[:3]))
check("D3.2 sweep reports zero region collisions", len(result["regionCollisions"]) == 0)
check("D3.3 completion band sits above corpus max offset",
      result["bandBase"] > result["corpusMaxEnd"],
      f"base={result['bandBase']} max={result['corpusMaxEnd']}")
# band must not overlap any machine's input/output anywhere in the corpus
band_lo, band_hi = result["bandSpan"]
corpus_clash = 0
import json
for pth in machines_dir.rglob("*.json"):
    try:
        pm = (json.loads(pth.read_text()).get("machine", {}) or {}).get("perceptualMapping", {}) or {}
    except json.JSONDecodeError:
        continue
    for key in ("input", "output"):
        r = pm.get(key) or {}
        if "offset" in r and "length" in r:
            if r["offset"] < band_hi and band_lo < r["offset"] + r["length"]:
                corpus_clash += 1
check("D3.4 reserved band clear of every machine in the corpus", corpus_clash == 0, str(corpus_clash))

# D3.5/6: allocation is bound to the registry-declared reserved band (M5)
from domain_sweep import reserved_band  # noqa: E402
rb = reserved_band(CFG)
check("D3.5 registry declares the ACP completion reserved range", rb is not None,
      "rangePolicy.reservedRanges missing")
check("D3.6 sweep uses the registry-reserved band as source",
      result["bandSource"].startswith("registry-reserved:"), result["bandSource"])
check("D3.7 allocation stays inside the reserved band (no overflow)",
      result["bandOverflow"] is False and result["bandSpan"][1] <= result["bandLimit"],
      f"span={result['bandSpan']} limit={result['bandLimit']}")
if rb:
    lo, hi = rb["offset"], rb["offset"] + rb["length"]
    inside = all(lo <= off and off + length <= hi for off, length in
                 [(a["realityVectorImpact"]["offset"], a["realityVectorImpact"]["length"])
                  for p in result["plans"] for a in p["agents"] if a["realityVectorImpact"]])
    check("D3.8 every completion region lies within the reserved band", inside)

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
