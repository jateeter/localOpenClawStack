#!/usr/bin/env python3
"""Runtime-neutral behaviors and their runtime adapters (runtimes/, RUNTIMES.md).

Run: MB_DEBUG=0 python3.13 tests/run_runtime_tests.py

1. Every corpus machine's neutral behavior validates against
   RealityEngine_Machines schemas/agent-behavior.schema.json and never names a
   runtime in its instructions.
2. Rendering it for OpenClaw is the committed agent, byte for byte (the whole
   corpus; the same comparison materialize_agents.py --check makes).
3. A factory domain's behaviors (ai-governance-regime, authored in
   RealityEngine_Machines) render for OpenClaw as valid OC agent specs and for
   ACP as session plans: frame-scoped capabilities become session-scoped MCP
   servers carrying only that frame's placeholders; no gateway or session key.
4. A capability no runtime maps is a render error, never a silent drop.

Tests 1 and 3 skip, saying so, when the sibling RealityEngine_Machines checkout
is absent.
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
import oc_agent_template as tmpl  # noqa: E402
from derive_agents import load_config  # noqa: E402
from runtimes import RenderError, acp, openclaw  # noqa: E402

MACHINES_REPO = ROOT.parent.parent / "RealityEngine_Machines"
BEHAVIOR_SCHEMA = MACHINES_REPO / "schemas" / "agent-behavior.schema.json"
GOV_AGENTS = MACHINES_REPO / "machine_factory/domains/ai-governance-regime/agent-candidates/ai-governance-regime"
OC_SCHEMA = ROOT / "templates" / "oc-agent.schema.json"

_PASS = _FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if ok:
        _PASS += 1
        if os.environ.get("MB_DEBUG") != "0":
            print(f"  ok   {name}")
    else:
        _FAIL += 1
        print(f"  FAIL {name}  {detail}")


cfg = load_config()
# machine id -> corpus file, the id the deriver records (machine.id, else the file stem)
by_id: dict[str, Path] = {}
for mp in sorted((MACHINES_REPO / "machines").rglob("*.json")):
    try:
        doc = json.loads(mp.read_text())
    except ValueError:
        continue
    if isinstance(doc, dict) and isinstance(doc.get("machine"), dict):
        by_id[str(doc["machine"].get("id") or mp.stem)] = mp


# 1 + 2: the corpus
schema = minischema.load_schema(BEHAVIOR_SCHEMA) if BEHAVIOR_SCHEMA.is_file() else None
identical = invalid = named = checked = 0
for spec_path in sorted((ROOT / "agents").glob("*/*.oc-agent.json")):
    committed = spec_path.read_text()
    spec = json.loads(committed)
    mfile = by_id.get(spec["machine"]["id"])
    if mfile is None:
        continue
    behavior = tmpl.derive_behavior(mfile, cfg)
    checked += 1
    rendered = json.dumps(openclaw.render(behavior, cfg), indent=2) + "\n"  # materialize_agents.py writes ASCII
    identical += rendered == committed
    if schema is not None and minischema.validate(behavior, schema):
        invalid += 1
    named += behavior["reasoning"]["instructions"].startswith("You are the OpenClaw")
check("corpus behaviors were derived", checked > 1000, f"derived {checked}")
check("OpenClaw render of every corpus behavior is the committed agent", identical == checked,
      f"{checked - identical} differ")
if schema is None:
    print("  skip corpus behaviors vs agent-behavior.schema.json: RealityEngine_Machines not beside this checkout")
else:
    check("every corpus behavior validates against agent-behavior.schema.json", invalid == 0, f"{invalid} invalid")
check("no corpus behavior's instructions name the runtime", named == 0, f"{named} do")

# 3: the factory domain
if not GOV_AGENTS.is_dir() or schema is None:
    print("  skip ai-governance-regime behaviors: not present in RealityEngine_Machines")
else:
    oc_schema = minischema.load_schema(OC_SCHEMA)
    caps = json.loads((ROOT / "runtimes" / "capabilities.json").read_text())["capabilities"]
    behaviors = [json.loads(p.read_text()) for p in sorted(GOV_AGENTS.glob("*.agent.json"))]
    check("governance behaviors present", len(behaviors) >= 10, f"{len(behaviors)}")
    for b in behaviors:
        bid = b["behaviorId"]
        check(f"{bid}: valid behavior", not minischema.validate(b, schema), str(minischema.validate(b, schema)[:2]))
        oc = openclaw.render(b, cfg)
        errs = minischema.validate(oc, oc_schema, base_dir=str(OC_SCHEMA.parent))
        check(f"{bid}: OpenClaw render is a valid OC agent spec", not errs, str(errs[:2]))
        policy = openclaw.tool_policy(b)
        frame_grants = [g for g in policy["grants"] if g["scope"] == "frame"]
        check(f"{bid}: OpenClaw states the shared-MCP deficiency on frame capabilities",
              all("deficiency" in g for g in frame_grants))
        plan = acp.render(b, cfg)
        text = json.dumps(plan)
        check(f"{bid}: ACP plan names no gateway or session key",
              "gatewayUrl" not in text and "sessionKey" not in text and "openclaw" not in text.lower())
        frame_servers = [s for s in plan["sessionNew"]["mcpServers"] if s["_meta"]["scope"] == "frame"]
        frame_caps = [c for c in b["capabilities"] if caps[c]["scope"] == "frame"]
        check(f"{bid}: every frame capability is in a session-scoped server",
              sorted(c for s in frame_servers for c in s["_meta"]["capabilities"]) == sorted(frame_caps))
        check(f"{bid}: frame servers carry only frame placeholders",
              all({e["name"] for e in s["env"]} == {"RE_FRAME_ID", "RE_CONTINUATION_ID", "RE_FRAME_CAPABILITY"}
                  for s in frame_servers))
        check(f"{bid}: ACP completion is the behavior's writeBack",
              plan["completion"]["writeBack"] == b["agentBinding"]["writeBack"])
        unimplemented = [c for c in b["capabilities"] if not caps[c]["implemented"]]
        check(f"{bid}: pending capabilities are stated", sorted(plan["pendingCapabilities"]) == sorted(unimplemented))

# 4: an unmapped capability
probe = {"behaviorId": "probe", "capabilities": ["teleport"]}
for name, fn in (("openclaw", lambda: openclaw.render(probe, cfg)), ("acp", lambda: acp.render(probe, cfg))):
    try:
        fn()
        check(f"{name}: an unmapped capability is refused", False, "rendered")
    except RenderError:
        check(f"{name}: an unmapped capability is refused", True)

print(f"{_PASS} passed, {_FAIL} failed")
sys.exit(1 if _FAIL else 0)
