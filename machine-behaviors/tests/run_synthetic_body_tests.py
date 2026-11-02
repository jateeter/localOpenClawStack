#!/usr/bin/env python3
"""Tests for synthetic_body.py — every generated agent can always answer.

Run: MB_DEBUG=0 python3.13 tests/run_synthetic_body_tests.py
"""

from __future__ import annotations

import collections
import json
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("MB_DEBUG", "0")
HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import oc_agent_template as tmpl  # noqa: E402
import synthetic_body as sb  # noqa: E402
from derive_agents import load_config  # noqa: E402
from openclaw_side import apply_response_mapping  # noqa: E402

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
MDIR = (ROOT / CFG["machinesDir"]).resolve() if not Path(CFG["machinesDir"]).is_absolute() else Path(CFG["machinesDir"])
NO_ENV: dict[str, str] = {}


def agent_for(rel: str) -> dict:
    return tmpl.derive(MDIR / rel, CFG)


def failing_model(_agent):
    raise ConnectionError("gateway unreachable")


print("== declaration from the corpus ==")
IFRS = "domains/ifrs-accounting/IFRS_Sec01_GeneralPurposeFS_Verifier.json"
a = agent_for(IFRS)
body = a["syntheticBody"]
L = a["machine"]["inputRegion"]["length"]
check("every agent carries a syntheticBody, off by default", body["mode"] == "off")
check("default strategy is seeded-random", body["strategy"] == "seeded-random")
N = body["ordinality"]
seqs = [s["name"] for s in json.loads((MDIR / IFRS).read_text())["machine"]["inputSequences"]]
check("ordinality is the number of distinct corpus input vectors (IFRS Sec01: 4 sequences, one vector each)",
      N == 4 and len(seqs) == 4, (N, seqs))
check("round-robin holds the first vector of every input sequence", len(body["roundRobin"]) == len(seqs), body["roundRobin"])
check("every response is a full input vector in [0, 1]",
      all(len(r["values"]) == L and all(0 <= v <= 1 for v in r["values"]) for r in body["responses"]))
named = {r["expectedSequenceId"] for r in body["responses"]} - {None}
check("every CES in the machine's catalog is driven by some admissible response",
      named == {c["sequenceId"] for c in a["reasoning"]["sequenceCatalog"]}, named)
check("the corpus's no-trigger fixtures are kept as admissible answers",
      any(r["expectedSequenceId"] is None for r in body["responses"]))

print("== the synthetic answer survives the real extractor ==")
for r in body["responses"]:
    obj = sb.response_object(a, r["values"])
    targets, _ex, unresolved = apply_response_mapping(obj, a["responseMapping"])
    check(f"{r['id']} maps exactly to its declared values",
          not unresolved and len(targets) == 1 and targets[0]["values"] == r["values"] and all(targets[0]["present"]),
          (targets, unresolved))
check("the answer object says it is synthetic", sb.response_object(a, body["responses"][0]["values"])["synthetic"] is True)

print("== selection ==")
picks = [sb.select(a, strategy="seeded-random", seed_key=f"corr-{k}")["provenance"]["responseId"] for k in range(200)]
check("seeded-random is reproducible for a seed key",
      sb.select(a, strategy="seeded-random", seed_key="corr-7")["values"]
      == sb.select(a, strategy="seeded-random", seed_key="corr-7")["values"])
counts = collections.Counter(picks)
check("seeded-random reaches every admissible response over 200 keys", len(counts) == body["ordinality"], counts)
check("seeded-random is roughly uniform (each within half to one-and-a-half times 200/N)",
      all(0.5 * 200 / N <= c <= 1.5 * 200 / N for c in counts.values()), counts)
rr = [sb.select(a, strategy="round-robin", step=k)["provenance"]["responseId"] for k in range(2 * N)]
check("round-robin visits every input sequence once per cycle, then repeats",
      len(set(rr[:N])) == N and rr[:N] == rr[N:], rr)

# a machine with several vectors per sequence, for sequence-walk
multi = None
for f in sorted(MDIR.rglob("*.json")):
    try:
        m = json.loads(f.read_text())["machine"]
    except Exception:
        continue
    seqs = m.get("inputSequences") or []
    if seqs and max(len(s.get("events") or []) for s in seqs) >= 3:
        multi = str(f.relative_to(MDIR))
        break
check("found a corpus machine with a multi-step input sequence", multi is not None)
if multi:
    b = agent_for(multi)
    n = b["syntheticBody"]["ordinality"]
    walk = [sb.select(b, strategy="sequence-walk", step=k)["provenance"]["responseId"] for k in range(n)]
    check(f"sequence-walk visits all {n} responses in corpus order ({Path(multi).stem})",
          walk == [r["id"] for r in b["syntheticBody"]["responses"]], walk)
    check("sequence-walk wraps after the last response",
          sb.select(b, strategy="sequence-walk", step=n)["provenance"]["responseId"] == walk[0])

print("== same answer in every process (engines in parity get one value) ==")
code = ("import sys; sys.path.insert(0, %r); import oc_agent_template as t, synthetic_body as s, json;"
        "from derive_agents import load_config; c=load_config();"
        "a=t.derive(%r, c); print(json.dumps([s.select(a, strategy='seeded-random', seed_key='k%%d'%%i)['values'] for i in range(20)]))"
        % (str(ROOT), str(MDIR / IFRS)))
outs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env={**os.environ, "PYTHONHASHSEED": str(h)}, cwd=ROOT).stdout for h in (1, 2, 3)}
check("seeded-random gives identical answers across processes and hash seeds", len(outs) == 1 and "[" in next(iter(outs)), outs)

print("== modes ==")
r = sb.turn(a, model_turn=failing_model, env=NO_ENV)
check("off: an unreachable model leaves no answer and no synthetic", r["source"] == "none" and not r["targets"])
r = sb.turn(a, cfg={"syntheticBody": {"mode": "fallback"}}, model_turn=failing_model, seed_key="c1", env=NO_ENV)
check("fallback: an unreachable model is replaced by a synthetic answer",
      r["source"] == "synthetic" and r["targets"] and r["provenance"]["replaced"].startswith("model turn failed"))
check("fallback: the synthetic targets are marked synthetic", all(t.get("synthetic") for t in r["targets"]))
good = {k: v for k, v in sb.response_object(a, body["responses"][1]["values"]).items() if k != "synthetic"}
r = sb.turn(a, cfg={"syntheticBody": {"mode": "fallback"}}, model_turn=lambda _a: good, env=NO_ENV)
check("fallback: a mappable model answer is used, not replaced", r["source"] == "model" and r["provenance"]["synthetic"] is False)
r = sb.turn(a, cfg={"syntheticBody": {"mode": "fallback"}}, model_turn=lambda _a: "i cannot tell", env=NO_ENV)
check("fallback: an unmappable model answer is replaced",
      r["source"] == "synthetic" and "did not resolve" in r["provenance"]["replaced"])
r = sb.turn(a, cfg={"syntheticBody": {"mode": "off"}}, model_turn=lambda _a: "i cannot tell", env=NO_ENV)
check("off: an unmappable model answer stays unresolved (no substitution)", r["source"] == "model" and r["unresolved"])
called = []
r = sb.turn(a, cfg={"syntheticBody": {"mode": "always"}}, model_turn=lambda _a: called.append(1), env=NO_ENV)
check("always: the model is never called", r["source"] == "synthetic" and not called)

print("== resolution: env > domain > global > agent ==")
dom = a["machine"]["domain"]
cfg = {"syntheticBody": {"mode": "fallback", "strategy": "round-robin",
                         "domains": {dom: {"mode": "always"}}}}
check("domain overrides global mode", sb.resolve(a, cfg, NO_ENV)["mode"] == "always")
check("global strategy applies when the domain names none", sb.resolve(a, cfg, NO_ENV)["strategy"] == "round-robin")
check("environment overrides both",
      sb.resolve(a, cfg, {"OC_SYNTHETIC_MODE": "off", "OC_SYNTHETIC_STRATEGY": "sequence-walk"})
      == {"mode": "off", "strategy": "sequence-walk"})
check("with nothing configured, the agent's own declaration applies", sb.resolve(a, {}, NO_ENV) == {"mode": "off", "strategy": "seeded-random"})
try:
    sb.resolve(a, {"syntheticBody": {"mode": "sometimes"}}, NO_ENV)
    check("an unknown mode is refused", False)
except ValueError:
    check("an unknown mode is refused", True)

print("== completion labelling ==")
r = sb.turn(a, cfg={"syntheticBody": {"mode": "always"}}, seed_key="c9", env=NO_ENV)
meta = sb.completion_metadata(r["provenance"])
check("a synthetic completion carries metadata.synthetic=true and its selection",
      meta.get("synthetic") is True and meta["syntheticBody"]["responseId"] and meta["syntheticBody"]["ordinality"] == N)
check("a model completion carries no synthetic label", sb.completion_metadata({"synthetic": False}) == {})

print("== ordinality 1 ==")
one = None
for f in sorted(MDIR.rglob("*.json")):
    try:
        d = json.loads(f.read_text())
        m = d["machine"]
    except Exception:
        continue
    vecs = {tuple(v) for s in (m.get("inputSequences") or d.get("inputSequences") or []) for v in (s.get("events") or [])}
    if len(vecs) == 1:
        one = str(f.relative_to(MDIR))
        break
check("found an ordinality-1 machine", one is not None)
if one:
    o = agent_for(one)
    answers = {json.dumps(sb.select(o, strategy=s, step=k, seed_key=f"x{k}")["values"])
               for s in sb.STRATEGIES for k in range(5)}
    check(f"ordinality 1 gives the one admissible answer under every strategy ({Path(one).stem})",
          len(answers) == 1 and o["syntheticBody"]["ordinality"] == 1)
    check("and reports it as the only admissible answer",
          sb.select(o, strategy="seeded-random", seed_key="z")["provenance"]["strategy"] == "only-admissible")

print("== corpus-wide: every generated agent can answer ==")
stats = collections.Counter()
unavailable, bad = [], []
for f in sorted(MDIR.rglob("*.json")):
    try:
        d = json.loads(f.read_text())
    except Exception:
        continue
    if not isinstance(d, dict) or not isinstance(d.get("machine"), dict):
        continue
    if not ((d["machine"].get("perceptualMapping") or {}).get("input") or {}).get("length"):
        continue
    ag = tmpl.derive(f, CFG)
    sbody = ag["syntheticBody"]
    stats["agents"] += 1
    if sbody.get("unavailable"):
        unavailable.append(str(f.relative_to(MDIR)))
        continue
    stats["ordinality>1" if sbody["ordinality"] > 1 else "ordinality=1"] += 1
    stats["withExcluded"] += bool(sbody.get("excluded"))
    stats["withNormalizationShift"] += any("normalizationShift" in r for r in sbody["responses"])
    sel = sb.select(ag, strategy="seeded-random", seed_key="sweep")
    targets, _ex, unres = apply_response_mapping(sb.response_object(ag, sel["values"]), ag["responseMapping"])
    if unres or len(targets) != 1 or targets[0]["values"] != sel["values"]:
        bad.append(str(f.relative_to(MDIR)))
print("       ", dict(stats))
check("every agent with an input region can produce a synthetic answer", not unavailable, unavailable[:5])
check("every agent's synthetic answer maps through its own responseMapping exactly", not bad, bad[:5])

print(f"\n{_PASS} passed, {_FAIL} failed")
sys.exit(1 if _FAIL else 0)
