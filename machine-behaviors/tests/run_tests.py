#!/usr/bin/env python3
"""Dependency-free incremental test suite for the machine-behaviors prototype.

Run:  MB_DEBUG=0 python3.13 tests/run_tests.py
Validates derived artifacts against the *canonical* RealityEngine_Machines schemas.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import uuid4
import importlib.util

os.environ.setdefault("MB_DEBUG", "0")
HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import minischema  # noqa: E402
from derive_agents import derive, load_config, _abs  # noqa: E402
import dispatch_side  # noqa: E402
import openclaw_side  # noqa: E402
from behavior_log import (BehaviorLogger, SIDE_DISPATCH, SIDE_OPENCLAW,  # noqa: E402
                          read_log, clear_logs)

CFG = load_config()
SCHEMAS = _abs(CFG["schemasDir"])
MACHINE = _abs(CFG["machinesDir"]) / "HomeChronicPainMonitor.json"

_PASS = 0
_FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ok   {name}")
    else:
        _FAIL += 1
        print(f"  FAIL {name}  {detail}")


def schema_errors(instance, schema_file: str):
    schema = minischema.load_schema(SCHEMAS / schema_file)
    return minischema.validate(instance, schema)


# --- T1: derivation shape -----------------------------------------------------
plan = derive(MACHINE, CFG)
agents = {a["agent"]: a for a in plan["agents"]}

check("T1.1 four CES outputs", len(plan["outputs"]) == 4, str(len(plan["outputs"])))
check("T1.2 three unique agents", len(plan["agents"]) == 3, list(agents))
check("T1.3 crisis -> caregiver_support_agent",
      plan["outputs"][0]["agent"] == "caregiver_support_agent", plan["outputs"][0]["agent"])
check("T1.4 opioid -> medication_adherence_agent",
      plan["outputs"][1]["agent"] == "medication_adherence_agent", plan["outputs"][1]["agent"])
check("T1.5 managed output is observe",
      plan["outputs"][3]["autonomyMode"] == "observe", plan["outputs"][3]["autonomyMode"])
check("T1.6 no automated-act under health (life-safety)",
      all(o["autonomyMode"] != "automated-act" for o in plan["outputs"]))

# --- T2: derived agentBinding validates against canonical schema --------------
for name, a in agents.items():
    errs = schema_errors(a["agentBinding"], "agent-binding.schema.json")
    check(f"T2 agentBinding valid: {name}", not errs, "; ".join(errs[:3]))

# --- T3: reality-vector regions: distinct, length-4, non-overlapping with machine
regions = [(a["realityVectorImpact"]["offset"], a["realityVectorImpact"]["length"])
           for a in plan["agents"] if a["realityVectorImpact"]]
spans = [(off, off + ln) for off, ln in regions]
check("T3.1 region length tracks affected positions (4/5/6 by autonomy)",
      all(a["realityVectorImpact"]["length"] == len(a["affectedPositions"])
          for a in plan["agents"] if a["realityVectorImpact"]))
check("T3.2 regions distinct", len(spans) == len({(s, e) for s, e in spans}))


def overlap(a, b):
    return a[0] < b[1] and b[0] < a[1]


pairwise_ok = all(not overlap(spans[i], spans[j])
                  for i in range(len(spans)) for j in range(i + 1, len(spans)))
check("T3.3 regions mutually non-overlapping", pairwise_ok, str(spans))
mo = plan["machine"]["outputRegion"]
mi = plan["machine"]["inputRegion"]
machine_spans = [(mi["offset"], mi["offset"] + mi["length"]),
                 (mo["offset"], mo["offset"] + mo["length"])]
no_clash = all(not overlap(s, ms) for s in spans for ms in machine_spans)
check("T3.4 completion regions clear of machine in/out", no_clash)

# --- T4: dispatch envelope validates against canonical schema -----------------
machine_meta = dispatch_side.load_machine_meta(plan, CFG)
clear_logs()
crisis = plan["outputs"][0]
corr = str(uuid4())
env = dispatch_side.build_envelope(plan, machine_meta, crisis, agents[crisis["agent"]], CFG, corr)
errs = schema_errors(env, "ai-trigger-envelope.schema.json")
check("T4.1 envelope valid vs ai-trigger-envelope.schema", not errs, "; ".join(errs[:3]))
check("T4.2 envelope carries per-output autonomy",
      env["dispatch"]["autonomyMode"] == crisis["autonomyMode"])

# --- T5: OpenClaw completion validates + write-back region matches agent -------
os.environ["MB_DEBUG"] = "1"  # enable both-side logging for T5/T6 assertions
d_log = BehaviorLogger(SIDE_DISPATCH, corr)
o_log = BehaviorLogger(SIDE_OPENCLAW, corr)
dispatch_side.dispatch(env, agents[crisis["agent"]], CFG, d_log, live=False)
res = openclaw_side.run(env, agents[crisis["agent"]], CFG, o_log, live=False)
comp = res["completions"][0]
errs = schema_errors(comp, "localai-completion-writeback.schema.json")
check("T5.1 completion valid vs localai-completion-writeback.schema", not errs, "; ".join(errs[:3]))
check("T5.2 completion region == agent reality-vector impact",
      comp["region"] == agents[crisis["agent"]]["realityVectorImpact"])
ext = {f["semantic"]: f["value"] for f in res["extracted"]}
check("T5.3 RED crisis at supervised-act is blocked (failed=1 from text)", ext["failed"] == 1.0)
check("T5.4 completion values length == region length",
      len(comp["values"]) == comp["region"]["length"])

# --- T6: both-side debug logging, correlated ----------------------------------
d_records = read_log(SIDE_DISPATCH)
o_records = read_log(SIDE_OPENCLAW)
check("T6.1 dispatch side emitted events", len(d_records) >= 2, str(len(d_records)))
check("T6.2 openclaw side emitted events", len(o_records) >= 3, str(len(o_records)))
check("T6.3 correlationId threads both sides",
      all(r["correlationId"] == corr for r in d_records + o_records))
check("T6.4 dispatch logs envelope + accepted",
      {r["event"] for r in d_records} >= {"dispatch.envelope-built", "dispatch.accepted"})
check("T6.5 openclaw logs response + mapping + completion",
      {r["event"] for r in o_records} >= {"openclaw.session-spawn", "openclaw.agent-response",
                                          "openclaw.response-mapped", "openclaw.completion-built",
                                          "openclaw.completion-posted"})
check("T6.6 openclaw log captures raw response text (auditability)",
      any("responseText" in r for r in o_records))

# --- T7: determinism ----------------------------------------------------------
plan2 = derive(MACHINE, CFG)
import json
check("T7 derivation is deterministic",
      json.dumps(plan, sort_keys=True) == json.dumps(plan2, sort_keys=True))

# --- T8: observe agent (if any) has no write-back, others use pe-sensor --------
for a in plan["agents"]:
    wb = a["agentBinding"]["writeBack"]["type"]
    if a["autonomyMode"] == "observe":
        check(f"T8 observe agent no writeback: {a['agent']}", wb == "none", wb)
    else:
        check(f"T8 non-observe agent pe-sensor: {a['agent']}", wb == "pe-sensor", wb)

# --- T9: response mapping — schema validity, textual->value, multi-position ----
from openclaw_side import apply_response_mapping  # noqa: E402

RM_SCHEMA = ROOT / "schemas" / "response-mapping.schema.json"
rm_schema = minischema.load_schema(RM_SCHEMA)
for a in plan["agents"]:
    errs = minischema.validate(a["responseMapping"], rm_schema)
    check(f"T9 responseMapping valid: {a['agent']}", not errs, "; ".join(errs[:3]))

# supervised-act agent affects 5 positions (adds review_required)
sup = next(a for a in plan["agents"] if a["autonomyMode"] == "supervised-act")
check("T9.1 supervised-act affects 5 vector positions", len(sup["affectedPositions"]) == 5,
      str(sup["affectedPositions"]))
check("T9.2 region length tracks affected positions",
      sup["realityVectorImpact"]["length"] == len(sup["affectedPositions"]))

# textual -> value: a known structured-keys response must extract the right vector
mapping = sup["responseMapping"]
text_ok = "verdict: staged\ncompleted: yes\nfailed: no\nconfidence: high\nreview_required: yes"
targets, fields = apply_response_mapping(text_ok, mapping)
fv = {f["semantic"]: f["value"] for f in fields}
check("T9.3 text 'completed: yes' -> 1.0", fv["completed"] == 1.0, str(fv))
check("T9.4 text 'failed: no' -> 0.0", fv["failed"] == 0.0)
check("T9.5 text 'confidence: high' -> 0.9", fv["confidence"] == 0.9)
check("T9.6 text 'verdict: staged' -> actionClass 0.75", fv["actionClass"] == 0.75)
check("T9.7 text 'review_required: yes' -> 1.0", fv["review_required"] == 1.0)
check("T9.8 one target region, values length == positions",
      len(targets) == 1 and len(targets[0]["values"]) == 5)

# negative case: 'completed: no / failed: yes' (blocked) flips the vector
text_blocked = "verdict: blocked\ncompleted: no\nfailed: yes\nconfidence: low"
_, fields_b = apply_response_mapping(text_blocked, mapping)
fb = {f["semantic"]: f["value"] for f in fields_b}
check("T9.9 'completed: no' -> 0.0 and 'failed: yes' -> 1.0",
      fb["completed"] == 0.0 and fb["failed"] == 1.0, str(fb))
check("T9.10 absent contracted key -> default, not cross-contamination",
      fb["review_required"] == 0.0, str(fb))

# JSON response path: extraction prefers the JSON pointer
json_resp = {"completed": 1, "failed": 0, "confidence": 0.42, "verdict": "executed",
             "review_required": False}
_, fields_j = apply_response_mapping(json_resp, mapping)
fj = {f["semantic"]: f["value"] for f in fields_j}
check("T9.11 JSON pointer extraction (confidence 0.42)", fj["confidence"] == 0.42, str(fj))
check("T9.12 JSON bool false -> 0.0", fj["review_required"] == 0.0)

# multi-region fan-out capability: a field may target a different region
multi = {"schemaVersion": "1.0.0", "responseContract": "structured-keys-or-text",
         "mode": "supervised-act", "fields": [
             {"semantic": "completed", "valueType": "binary", "normalization": "binary",
              "extract": {"jsonPointer": "/completed", "responseKey": "completed",
                          "textFallback": {"type": "enum-keyword", "default": 0.0,
                                           "keywords": {"yes": 1.0, "no": 0.0}}},
              "target": {"sensorId": "s.a", "region": {"offset": 100, "length": 1}, "index": 0}},
             {"semantic": "escalation", "valueType": "binary", "normalization": "binary",
              "extract": {"jsonPointer": "/escalate", "responseKey": "escalate",
                          "textFallback": {"type": "enum-keyword", "default": 0.0,
                                           "keywords": {"yes": 1.0, "no": 0.0}}},
              "target": {"sensorId": "s.b", "region": {"offset": 900, "length": 1}, "index": 0}}]}
mt, _ = apply_response_mapping("completed: yes\nescalate: yes", multi)
check("T9.13 response fans out to multiple non-contiguous regions",
      len(mt) == 2 and {t["sensorId"] for t in mt} == {"s.a", "s.b"}, str([t["sensorId"] for t in mt]))

# --- T10: patient-safety transport OpenClaw proof-of-concept -----------------
health_agents = [
    "fall-sensor-motion-pre-aggregator.oc-agent.json",
    "fall-detection.oc-agent.json",
    "home-transportation-barrier-monitor.oc-agent.json",
    "home-social-isolation-monitor.oc-agent.json",
]
oc_schema = minischema.load_schema(ROOT / "templates" / "oc-agent.schema.json")
rm_schema = minischema.load_schema(ROOT / "schemas" / "response-mapping.schema.json")
for fname in health_agents:
    rec = json.loads((ROOT / "agents" / "health-personal" / fname).read_text())
    errs = minischema.validate(rec, oc_schema)
    check(f"T10 oc-agent valid: {fname}", not errs, "; ".join(errs[:3]))
    errs = minischema.validate(rec["responseMapping"], rm_schema)
    check(f"T10 responseMapping valid: {fname}", not errs, "; ".join(errs[:3]))

spec = importlib.util.spec_from_file_location(
    "patient_safety_transport_poc",
    ROOT / "pe-integration" / "patient_safety_transport_poc.py")
poc_mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(poc_mod)
poc = poc_mod.run(ROOT / "pe-integration" / "patient_safety_transport_openclaw_transform.json")
by_agent = {c["agent"]: c for c in poc["openclawCompletions"]}
check("T10.1 POC emits four OpenClaw PE completions", len(by_agent) == 4, list(by_agent))
check("T10.2 pre-aggregator preserves native count 6",
      by_agent["fall-sensor-motion-pre-aggregator"]["values"] == [6.0],
      str(by_agent["fall-sensor-motion-pre-aggregator"]["values"]))
check("T10.3 fall detection preserves native ordinal [3,3]",
      by_agent["fall-detection"]["values"] == [3.0, 3.0],
      str(by_agent["fall-detection"]["values"]))
check("T10.4 social isolation projects upstream binary bits",
      by_agent["home-social-isolation-monitor"]["values"] == [0.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 0.0],
      str(by_agent["home-social-isolation-monitor"]["values"]))
check("T10.5 PE-composed patient-safety bus vector matches narrative",
      poc["peComposition"]["inputValues"] == [1, 0, 1, 1, 0, 0, 0, 0, 0, 1],
      str(poc["peComposition"]["inputValues"]))
check("T10.6 final resolver handoff returns through PE source region [4320:4324]",
      poc["peResolutionCompletion"]["region"] == {"offset": 4320, "length": 4}
      and poc["peResolutionCompletion"]["values"] == [1, 1, 1, 1],
      str(poc["peResolutionCompletion"]))
check("T10.7 localAIStack trigger uses updateProcessState",
      poc["localAIStackGraphQLTrigger"]["operation"] == "updateProcessState")

completion_schema = "localai-completion-writeback.schema.json"
for name, completion in by_agent.items():
    errs = schema_errors(completion, completion_schema)
    check(f"T10 PE completion valid: {name}", not errs, "; ".join(errs[:3]))
errs = schema_errors(poc["peResolutionCompletion"], completion_schema)
check("T10 PE resolution completion valid", not errs, "; ".join(errs[:3]))

print(f"\n{_PASS} passed, {_FAIL} failed")
sys.exit(1 if _FAIL else 0)
