#!/usr/bin/env python3
"""Tests for synthetic_body.py — a synthetic action answer on the action path.

Run: MB_DEBUG=0 python3.13 tests/run_synthetic_body_tests.py
"""

from __future__ import annotations

import collections
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("MB_DEBUG", "0")
HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import minischema  # noqa: E402
import dispatch_side  # noqa: E402
import openclaw_side  # noqa: E402
import synthetic_body as sb  # noqa: E402
from behavior_log import SIDE_OPENCLAW, BehaviorLogger  # noqa: E402
from derive_agents import _abs, derive, load_config, resolve_machine_file  # noqa: E402

_PASS = _FAIL = 0


def check(name, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ok   {name}")
    else:
        _FAIL += 1
        print(f"  FAIL {name}" + (f"\n       {detail}" if detail else ""))


CFG = load_config()
NO_ENV: dict[str, str] = {}
SCHEMAS = _abs(CFG["schemasDir"])

print("== the admissible answers are the action contract's ==")
check("observe writes no vector, so it admits no synthetic answer", sb.admissible("observe", "GREEN") == [])
for mode, verdict, action_class in (("advise", "advised", 0.5), ("supervised-act", "staged", 0.75),
                                    ("automated-act", "executed", 1.0)):
    answers = sb.admissible(mode, "GREEN")
    check(f"{mode}: three answers, one per confidence anchor, all '{verdict}'",
          len(answers) == 3 and {a["verdict"] for a in answers} == {verdict}
          and [a["confidence"] for a in answers] == list(sb.CONFIDENCE_ANCHORS))
    values = [sb.lane_values(a, sb.action_mapping(mode)) for a in answers]
    check(f"{mode}: lane values are [completed 1, failed 0, confidence, actionClass {action_class}]",
          values == [[1.0, 0.0, c, action_class] for c in (0.3, 0.6, 0.9)], values)
check("supervised-act answers carry review_required", all(a.get("review_required") == "yes"
                                                           for a in sb.admissible("supervised-act", "GREEN")))
check("automated-act answers carry executed and rollback_ok",
      all(a.get("executed") == "yes" and a.get("rollback_ok") == "yes" for a in sb.admissible("automated-act", "GREEN")))

print("== blocking follows the mode's riskControls ==")
check("advise is never blocked", sb.admissible("advise", "RED")[0]["verdict"] == "advised")
check("supervised-act is blocked at RED, not AMBER",
      sb.admissible("supervised-act", "RED")[0]["verdict"] == "blocked"
      and sb.admissible("supervised-act", "AMBER")[0]["verdict"] == "staged")
check("automated-act is blocked at AMBER and RED",
      all(sb.admissible("automated-act", r)[0]["verdict"] == "blocked" for r in ("AMBER", "RED")))
blocked = sb.admissible("supervised-act", "RED")
check("a blocked answer is completed 0, failed 1, actionClass 0 (unresolved, posted as 0 like a real one)",
      [sb.lane_values(a, sb.action_mapping("supervised-act")) for a in blocked]
      == [[0.0, 1.0, c, 0.0] for c in (0.3, 0.6, 0.9)])
check("an agent's own riskControls override the mode default",
      sb.admissible("advise", "AMBER", {"blockedWhenRag": ["AMBER"]})[0]["verdict"] == "blocked")

print("== selection ==")
answers = sb.admissible("advise", "GREEN")
picks = collections.Counter(sb.select(answers, agent="a1", strategy="seeded-random", seed_key=f"c{k}")[0] for k in range(300))
check("seeded-random reaches every answer over 300 seed keys", len(picks) == 3, picks)
check("seeded-random is roughly uniform", all(50 <= c <= 150 for c in picks.values()), picks)
check("seeded-random is reproducible",
      sb.select(answers, agent="a1", strategy="seeded-random", seed_key="c7")
      == sb.select(answers, agent="a1", strategy="seeded-random", seed_key="c7"))
check("round-robin cycles in contract order",
      [sb.select(answers, agent="a1", strategy="round-robin", step=s)[0] for s in range(6)] == [0, 1, 2, 0, 1, 2])
check("an empty seed key falls back to the step",
      sb.select(answers, agent="a1", strategy="seeded-random", seed_key="", step=4)
      == sb.select(answers, agent="a1", strategy="seeded-random", seed_key="4", step=4))
code = ("import sys; sys.path.insert(0, %r); import synthetic_body as s, json;"
        "a = s.admissible('advise', 'GREEN');"
        "print(json.dumps([s.select(a, agent='agent-x', strategy='seeded-random', seed_key='k%%d' %% i)[0] for i in range(30)]))"
        % str(ROOT))
outs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT,
                       env={**os.environ, "PYTHONHASHSEED": str(h)}).stdout for h in (1, 2, 3)}
check("identical selections across processes and hash seeds (engines in parity get one value)",
      len(outs) == 1 and next(iter(outs)).startswith("["), outs)

print("== resolution: env > domain > global ==")
cfg = {"syntheticBody": {"mode": "fallback", "strategy": "round-robin", "domains": {"health-personal": {"mode": "always"}}}}
check("domain overrides global", sb.resolve(cfg, "health-personal", NO_ENV) == {"mode": "always", "strategy": "round-robin"})
check("global applies to another domain", sb.resolve(cfg, "transportation", NO_ENV)["mode"] == "fallback")
check("environment overrides both", sb.resolve(cfg, "health-personal", {"OC_SYNTHETIC_MODE": "off"})["mode"] == "off")
check("nothing configured: off, seeded-random", sb.resolve({}, "", NO_ENV) == {"mode": "off", "strategy": "seeded-random"})
try:
    sb.resolve({"syntheticBody": {"strategy": "sequence-walk"}}, "", NO_ENV)
    check("an unknown strategy is refused", False)
except ValueError:
    check("an unknown strategy is refused", True)

print("== on the action path (openclaw_side.run) ==")
plan = derive(resolve_machine_file(_abs(CFG["machinesDir"]), "HomeChronicPainMonitor.json"), CFG)
agents = {a["agent"]: a for a in plan["agents"]}
meta = dispatch_side.load_machine_meta(plan, CFG)
out = next(o for o in plan["outputs"] if o["autonomyMode"] != "observe")
rec = agents[out["agent"]]
env = dispatch_side.build_envelope(plan, meta, out, rec, CFG, str(uuid4()))
log = BehaviorLogger(SIDE_OPENCLAW, env["correlationId"])
lane = CFG["completionLane"]


def run(cfg, model_turn=None):
    os.environ.pop("OC_SYNTHETIC_MODE", None)
    return openclaw_side.run(env, rec, cfg, log, live=False, model_turn=model_turn)


r = run(CFG)
comp = r["completions"][0]
check("with no model, the action's answer is the synthetic body's, labelled",
      r["provenance"]["synthetic"] and comp["metadata"]["synthetic"] is True)
check("it travels the current completion path: acp-openclaw-completion at [4210:4214]",
      comp["sourceMapping"]["id"] == lane["sourceMappingId"]
      and comp["region"] == {"offset": lane["offset"], "length": lane["length"]}, comp["region"])
check("its values are one of the admissible answers' lane values",
      comp["values"] in [sb.lane_values(a, sb.action_mapping(env["dispatch"]["autonomyMode"]))
                         for a in sb.admissible(env["dispatch"]["autonomyMode"], env["governance"]["ragStatusCode"],
                                                rec["agentBinding"].get("riskControls"))], comp["values"])
errs = minischema.validate(comp, minischema.load_schema(SCHEMAS / "localai-completion-writeback.schema.json"))
check("the labelled completion is valid against localai-completion-writeback.schema", not errs, errs[:3])


def unreachable(_e, _r):
    raise ConnectionError("gateway unreachable")


fallback = {**CFG, "syntheticBody": {"mode": "fallback"}}
r = run(fallback, unreachable)
check("fallback: an unreachable model is answered synthetically, saying what it replaced",
      r["provenance"]["synthetic"] and r["provenance"]["replaced"].startswith("model turn failed")
      and r["completions"])
mode = env["dispatch"]["autonomyMode"]
real = {**sb.admissible(mode, env["governance"]["ragStatusCode"], rec["agentBinding"].get("riskControls"))[0],
        "confidence": "0.42"}
good = "\n".join(f"{k}: {v}" for k, v in real.items())
print(f"        (action under test: {rec['agent']}, {mode})")
r = run(fallback, lambda _e, _r: good)
check("fallback: a resolving model answer is used as given",
      not r["provenance"]["synthetic"] and r["completions"][0]["values"][2] == 0.42
      and "synthetic" not in r["completions"][0]["metadata"])
blocked_real = "verdict: blocked\ncompleted: no\nfailed: yes\nconfidence: high\nreview_required: yes"
r = run({**CFG, "syntheticBody": {"mode": "fallback"}}, lambda _e, _r: blocked_real)
check("fallback: a real blocked answer (no actionClass, by contract) is used, not replaced",
      not r["provenance"]["synthetic"] and r["completions"][0]["values"][:2] == [0.0, 1.0])
r = run(fallback, lambda _e, _r: "no idea")
check("fallback: an unresolving model answer is replaced",
      r["provenance"]["synthetic"] and "did not resolve" in r["provenance"]["replaced"])
try:
    run({**CFG, "syntheticBody": {"mode": "off"}}, unreachable)
    check("off: an unreachable model is not substituted (the failure surfaces)", False)
except ConnectionError:
    check("off: an unreachable model is not substituted (the failure surfaces)", True)
called = []
r = run({**CFG, "syntheticBody": {"mode": "always"}}, lambda _e, _r: called.append(1))
check("always: the model is never called", not called and r["provenance"]["synthetic"])

print("== vectors pinned in RealityEngine_CI's adapter tests ==")
vec = {mode: {rag: [sb.select(sb.admissible(mode, rag), agent="vector-agent", strategy="seeded-random",
                              seed_key=k)[0] for k in ("corr-0", "corr-1", "corr-2", "dispatch-abc", "")]
              for rag in ("GREEN", "RED")} for mode in ("advise", "supervised-act")}
print("       ", json.dumps(vec))
check("vectors computed", bool(vec))

print(f"\n{_PASS} passed, {_FAIL} failed")
sys.exit(1 if _FAIL else 0)
