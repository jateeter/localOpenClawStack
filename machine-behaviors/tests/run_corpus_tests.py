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
_subdir_files = list((_md / "domains").rglob("*.json")) if (_md / "domains").exists() else []
check("C1.5 subdirectory corpus included (machines/domains/** via rglob)",
      len(_subdir_files) > 0 and "energy" in s["perDomain"]
      and s["perDomain"]["energy"]["machines"] > 0,
      f"subdir_files={len(_subdir_files)} energy={s['perDomain'].get('energy')}")

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

# C3: the shared completion lane, corpus-wide (per-agent regions retired, #26)
lane = result["completionLane"]
check("C3.1 no agent claims a per-agent completion region",
      all(a["realityVectorImpact"] is None for p in result["plans"] for a in p["agents"]),
      str([a["agent"] for p in result["plans"] for a in p["agents"]
           if a["realityVectorImpact"] is not None][:3]))
check("C3.2 zero region collisions reported", len(result["regionCollisions"]) == 0)
check("C3.3 sweep declares per-agent regions retired", result["perAgentRegions"] is False)

_wb = [a["agentBinding"]["writeBack"] for p in result["plans"] for a in p["agents"]
       if a["agentBinding"]["writeBack"].get("type") == "pe-sensor"]
check("C3.4 every write-back agent corpus-wide targets the one lane",
      {w["sourceMapping"]["id"] for w in _wb} <= {lane["id"]},
      str({w["sourceMapping"]["id"] for w in _wb} - {lane["id"]}))
check("C3.5 the lane sits inside the corpus footprint, not above it",
      lane["offset"] + lane["length"] <= result["corpusMaxEnd"],
      f"lane={lane} corpusMaxEnd={result['corpusMaxEnd']}")

# C4: PE source mappings — one per write-back agent, pointers match positions
sm = pe_source_mappings(result)
# One mapping TOTAL now, not one per agent: every write-back agent shares the
# acp-openclaw-completion lane, so emitting per-agent records would repeat the
# same one — which is how 1,216 stray mappings survived the band retirement.
check("C4.1 the corpus emits exactly one completion source mapping",
      sm["count"] == 1, f"count={sm['count']} ids={[m['id'] for m in sm['sourceMappings']][:3]}")
ids = [m["id"] for m in sm["sourceMappings"]]
check("C4.2 PE source-mapping ids are unique", len(ids) == len(set(ids)),
      f"{len(ids) - len(set(ids))} dupes")
check("C4.3 the mapping is the configured completion lane id",
      ids == [lane["id"]], str(ids))
check("C4.4 extract pointers count == region length for every mapping",
      all(len(m["extract"]["pointers"]) == m["region"]["length"] for m in sm["sourceMappings"]))
check("C4.5 every mapping region is the declared lane",
      all(m["region"] == {"offset": lane["offset"], "length": lane["length"]}
          for m in sm["sourceMappings"]),
      str([m["region"] for m in sm["sourceMappings"]]))

# C5: input-analyst coverage — one schema-valid agent per machine, materialized on disk
import oc_agent_template as _oct  # noqa: E402
_oc_schema = minischema.load_schema(ROOT / "templates" / "oc-agent.schema.json")
_ia_ok = _ia_bad = _ia_err = 0
_ia_ids = set()
for _p in _files:
    try:
        _dd = _json.loads(_p.read_text())
    except _json.JSONDecodeError:
        continue
    if not isinstance(_dd.get("machine"), dict):
        continue
    try:
        _inst = _oct.derive(_p, CFG)
    except Exception:
        _ia_err += 1
        continue
    if minischema.validate(_inst, _oc_schema):
        _ia_bad += 1
    else:
        _ia_ok += 1
    _ia_ids.add((_inst["machine"]["domain"], _inst["agentId"]))
check("C5.1 one schema-valid input-analyst agent per machine",
      _ia_ok == s["machines"] and _ia_bad == 0, f"ok={_ia_ok} bad={_ia_bad} machines={s['machines']}")
check("C5.2 no input-analyst derive errors across the corpus", _ia_err == 0, str(_ia_err))
check("C5.3 input-analyst agent ids unique per domain", len(_ia_ids) == s["machines"], str(len(_ia_ids)))
# materialized on disk: agents/INDEX.json total == machine count (after materialize_agents.py)
_index_path = ROOT / "agents" / "INDEX.json"
if _index_path.exists():
    _idx = _json.loads(_index_path.read_text())
    # Not == machine count. materialize_agents.py deliberately skips the
    # arbitration fixtures: an agent is a `generated` contributor, which is the
    # non-determinism those fixtures exist to disprove. RealityEngine_Machines
    # corpus-exit-v1.0 §3.3 states a regeneration producing 1,328 specs rather
    # than 1,323 is wrong, so asserting equality asserts the wrong number.
    #
    # The exclusion is counted from the corpus, not hardcoded at 5, so adding a
    # fixture does not fail this.
    _fixtures = sum(
        1 for _f in _files
        if str(((_json.loads(_f.read_text()).get("machine") or {}).get("metadata") or {})
               .get("tagging", {}).get("family", "")) == "arbitration-fixture")
    check("C5.4 agents/INDEX.json total == corpus machines minus arbitration fixtures",
          _idx.get("total") == s["machines"] - _fixtures,
          f"index={_idx.get('total')} machines={s['machines']} fixtures={_fixtures}")

# C6: PE input bridges — leaf source mappings are clean and complete
import register_input_mappings as _reg  # noqa: E402
_br = _reg.collect("*", include_bridged=False)
_leafregs = sorted((m["region"]["offset"], m["region"]["offset"] + m["region"]["length"])
                   for m in _br["mappings"])
_brov = sum(1 for i in range(1, len(_leafregs)) if _leafregs[i][0] < _leafregs[i - 1][1])
check("C6.1 leaf bridge mappings are mutually non-overlapping", _brov == 0, str(_brov))
check("C6.2 every machine classified (selected+skipped == corpus)",
      len(_br["selected"]) + len(_br["skipped"]) == s["machines"],
      f"{len(_br['selected'])}+{len(_br['skipped'])} vs {s['machines']}")
_energy_leaf = sum(1 for r in _br["leaf"] if r["instance"]["machine"]["domain"] == "energy")
check("C6.3 energy domain fully bridged as leaf (rglob fix unlocked subdir)",
      _energy_leaf == s["perDomain"]["energy"]["machines"],
      f"{_energy_leaf} vs {s['perDomain']['energy']['machines']}")
check("C6.4 no leaf bridge overlaps the sensor-integration band [4200:4320]",
      not any(o < 4320 and 4200 < e for o, e in _leafregs), "leaf in sensor band")

# C7: machine-native count/ordinal axes pass through unscaled (no 0-1 coercion)
import openclaw_side as _ocs  # noqa: E402


def _passthrough(norm, val):
    rm = {"schemaVersion": "1.0.0", "responseContract": "structured-keys-or-text",
          "mode": "advise", "fields": [{
              "semantic": "x", "valueType": "count", "normalization": norm,
              "extract": {"jsonPointer": "/x", "responseKey": "x",
                          "textFallback": {"type": "scalar-phrase", "default": 0.5,
                                           "phrases": {}, "numberRegex": r"(\d+(?:\.\d+)?)"}},
              "target": {"sensorId": "s", "region": {"offset": 0, "length": 1}, "index": 0}}]}
    _targets, _, _ = _ocs.apply_response_mapping(f"x: {val}", rm)
    return _targets[0]["values"][0]


check("C7.1 machine-native-count 6 passes through as 6 (not 0.06)",
      _passthrough("machine-native-count", 6) == 6.0, str(_passthrough("machine-native-count", 6)))
check("C7.2 machine-native-ordinal 3 passes through as 3",
      _passthrough("machine-native-ordinal", 3) == 3.0, str(_passthrough("machine-native-ordinal", 3)))
check("C7.3 scalar-0-1 axis still bounded to [0,1]",
      0.0 <= _passthrough("scalar-0-1", 6) <= 1.0, str(_passthrough("scalar-0-1", 6)))
# the deriver emits machine-native only for genuine count/ordinal-input machines
_native = 0
for _p in _files:
    try:
        _i = _oct.derive(_p, CFG)
    except Exception:
        continue
    if any(f["normalization"].startswith("machine-native") for f in _i["responseMapping"]["fields"]):
        _native += 1
check("C7.4 machine-native normalization is rare/targeted (<=10 machines)", _native <= 10, str(_native))

print(f"\ncoverage: {s['machines']} machines, {s['cesOutputs']} behaviors, "
      f"{s['agentBindings']} output-actor bindings ({s['writebackAgents']} write-back), "
      f"{sm['count']} PE completion mapping, "
      f"{_ia_ok} input-analyst agents ({_native} machine-native), {len(_br['mappings'])} PE input bridges, "
      f"completion lane {lane['id']}@[{lane['offset']}:{lane['offset'] + lane['length']}]")
print(f"{_PASS} passed, {_FAIL} failed")
sys.exit(1 if _FAIL else 0)
