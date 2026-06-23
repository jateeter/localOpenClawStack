#!/usr/bin/env python3
"""Corpus-wide sweep tests (all domains). Run: python3.13 tests/run_corpus_tests.py

Validates every binding and response mapping across the full corpus, global region
integrity within the registry-reserved band, and the PE source-mapping output.
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
from domain_sweep import sweep, summarize, pe_source_mappings, reserved_band  # noqa: E402

CFG = load_config()
SCHEMAS = _abs(CFG["schemasDir"])
_PASS = _FAIL = 0


def check(name, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ok   {name}")
    else:
        _FAIL += 1
        print(f"  FAIL {name}  {detail}")


result = sweep("*", CFG)
s = summarize(result)

# C1: full breadth — recount the corpus independently so the test tracks growth
import json as _json
_md = _abs(CFG["machinesDir"])
_files = [p for p in _md.rglob("*.json")]
_expect_machines = 0
_expect_ces = 0
for _p in _files:
    try:
        _d = _json.loads(_p.read_text())
    except _json.JSONDecodeError:
        continue
    _m = _d.get("machine")
    if isinstance(_m, dict):
        _expect_machines += 1
        _expect_ces += len(((_m.get("metadata") or {}).get("triggerConfig") or {}).get("rules") or [])
check(f"C1.1 every corpus machine swept (independent recount {_expect_machines})",
      s["machines"] == _expect_machines and s["machines"] >= 1174, str(s["machines"]))
check(f"C1.2 all CES outputs covered (independent recount {_expect_ces})",
      s["cesOutputs"] == _expect_ces, f"{s['cesOutputs']} vs {_expect_ces}")
check("C1.3 every machine yields >=1 agent",
      all(p["agents"] for p in result["plans"]),
      str([p["machine"]["code"] for p in result["plans"] if not p["agents"]][:3]))
check("C1.4 multiple domains covered (>=12)", len(s["perDomain"]) >= 12, str(len(s["perDomain"])))
check("C1.5 energy subdir corpus included", "energy" in s["perDomain"] and
      s["perDomain"]["energy"]["machines"] == 160, str(s["perDomain"].get("energy")))

# C2: every binding + response mapping schema-valid (corpus-wide)
ab_schema = minischema.load_schema(SCHEMAS / "agent-binding.schema.json")
rm_schema = minischema.load_schema(ROOT / "schemas" / "response-mapping.schema.json")
ab_bad = rm_bad = 0
for p in result["plans"]:
    for a in p["agents"]:
        if minischema.validate(a["agentBinding"], ab_schema):
            ab_bad += 1
        if minischema.validate(a["responseMapping"], rm_schema):
            rm_bad += 1
check("C2.1 all agentBindings schema-valid corpus-wide", ab_bad == 0, f"{ab_bad} bad")
check("C2.2 all responseMappings schema-valid corpus-wide", rm_bad == 0, f"{rm_bad} bad")
check("C2.3 sweep reports zero validation errors", len(result["validationErrors"]) == 0,
      str(result["validationErrors"][:2]))

# C3: global region integrity within the reserved band
regions = sorted((a["realityVectorImpact"]["offset"],
                  a["realityVectorImpact"]["offset"] + a["realityVectorImpact"]["length"])
                 for p in result["plans"] for a in p["agents"] if a["realityVectorImpact"])
overlaps = [(regions[i - 1], regions[i]) for i in range(1, len(regions)) if regions[i][0] < regions[i - 1][1]]
check("C3.1 no completion region overlaps another (corpus-wide)", not overlaps, str(overlaps[:2]))
check("C3.2 zero region collisions reported", len(result["regionCollisions"]) == 0)
rb = reserved_band(CFG)
lo, hi = rb["offset"], rb["offset"] + rb["length"]
check("C3.3 all regions inside registry-reserved band", regions[0][0] >= lo and regions[-1][1] <= hi,
      f"alloc {regions[0][0]}..{regions[-1][1]} band [{lo}:{hi}]")
check("C3.4 no band overflow", result["bandOverflow"] is False)
check("C3.5 band sits above corpus max offset", rb["offset"] > result["corpusMaxEnd"],
      f"base={rb['offset']} max={result['corpusMaxEnd']}")

# C4: PE source mappings — one per write-back agent, pointers match positions
sm = pe_source_mappings(result)
writeback = sum(1 for p in result["plans"] for a in p["agents"] if a["realityVectorImpact"])
check("C4.1 one PE source mapping per write-back agent", sm["count"] == writeback,
      f"{sm['count']} vs {writeback}")
ids = [m["id"] for m in sm["sourceMappings"]]
check("C4.2 PE source-mapping ids are unique", len(ids) == len(set(ids)),
      f"{len(ids) - len(set(ids))} dupes")
check("C4.3 extract pointers count == region length for every mapping",
      all(len(m["extract"]["pointers"]) == m["region"]["length"] for m in sm["sourceMappings"]))
check("C4.4 every mapping region inside reserved band",
      all(lo <= m["region"]["offset"] and m["region"]["offset"] + m["region"]["length"] <= hi
          for m in sm["sourceMappings"]))

print(f"\ncoverage: {s['machines']} machines, {s['cesOutputs']} behaviors, "
      f"{s['agentBindings']} bindings, {writeback} PE source mappings, "
      f"band [{lo}:{hi}] used {regions[0][0]}..{regions[-1][1]}")
print(f"{_PASS} passed, {_FAIL} failed")
sys.exit(1 if _FAIL else 0)
