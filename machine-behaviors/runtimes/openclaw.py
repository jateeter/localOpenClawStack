"""Render a runtime-neutral agent behavior as an OpenClaw agent spec.

The output is the OC agent spec this repository has always committed
(templates/oc-agent.schema.json): for a behavior the corpus deriver produced,
render(derive_behavior(machine)) is byte-identical to the agent in agents/.
Everything OpenClaw-specific lives here: the xACP transport block, the agent id
OpenClaw keys on, the legacy machine code, and the "OpenClaw" framing of the
prompt. A behavior names none of it.
"""
from __future__ import annotations

from typing import Any

from . import resolve_capabilities

RUNTIME = "openclaw"
# The corpus deriver's neutral prompt opens with this; OpenClaw's spec has always
# said "the OpenClaw input-analyst agent".
NEUTRAL_ANALYST_OPENING = "You are the input-analyst agent"
OPENCLAW_ANALYST_OPENING = "You are the OpenClaw input-analyst agent"


def system_prompt(instructions: str) -> str:
    if instructions.startswith(NEUTRAL_ANALYST_OPENING):
        return OPENCLAW_ANALYST_OPENING + instructions[len(NEUTRAL_ANALYST_OPENING):]
    return instructions


def transport(cfg: dict[str, Any], agent_id: str, rb: dict[str, Any]) -> dict[str, Any]:
    oc = cfg.get("openclaw") or {}
    return {
        "id": oc.get("id"), "kind": oc.get("kind"), "platform": oc.get("platform"),
        "surface": oc.get("surface"), "command": oc.get("command"),
        "gatewayUrl": oc.get("gatewayUrl"), "sessionKey": oc.get("sessionKey"),
        "targetAgent": agent_id, "dispatchMode": oc.get("dispatchMode"),
        "completionMode": oc.get("completionMode"),
        "completionSourceMappingId": rb.get("completionSourceMappingId", f"acp-{agent_id}-input-assessment"),
    }


def tool_policy(behavior: dict[str, Any]) -> dict[str, Any]:
    """The capabilities as OpenClaw can grant them, with its MCP deficiency stated."""
    grants = []
    for cap, spec in resolve_capabilities(behavior):
        grants.append({"capability": cap, "server": spec["server"], "tools": spec["tools"],
                       "scope": spec["scope"], "implemented": spec["implemented"],
                       **({"deficiency": "gateway-wide MCP server shared by every agent (RealityEngine_CI#714)"}
                          if spec["scope"] == "frame" else {})})
    return {"runtime": RUNTIME, "agent": behavior["agentId"], "grants": grants}


def render(behavior: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    resolve_capabilities(behavior)
    rb = (behavior.get("runtimeBindings") or {}).get(RUNTIME, {})
    m = behavior["machine"]
    r = behavior["reasoning"]
    out: dict[str, Any] = {}
    for key in ("documentAssetWorkflow", "documentWorkflowBinding"):
        if key in behavior:
            out[key] = behavior[key]
    out.update({
        "schemaVersion": "1.0.0",
        "templateId": rb.get("templateId", "oc-agent-template"),
        "role": behavior["role"],
        "agentId": behavior["agentId"],
        "displayName": behavior["displayName"],
        "machine": {
            "id": m["id"], "name": m.get("name", behavior["displayName"]),
            "code": rb.get("machineCode", behavior["agentId"]), "machineClass": m["machineClass"],
            "domain": behavior.get("domain"), "inputRegion": m["inputRegion"], "outputRegion": m["outputRegion"],
        },
        "openclaw": transport(cfg, behavior["agentId"], rb),
    })
    reasoning = {"objective": r["objective"], "systemPrompt": system_prompt(r["instructions"]),
                 "outputContract": r["outputContract"]}
    for key in ("inputAxes", "sequenceCatalog"):
        reasoning[key] = r.get(key, [])
    out["reasoning"] = reasoning
    out["agentBinding"] = behavior["agentBinding"]
    out["responseMapping"] = behavior["responseMapping"]
    diagnostics = (behavior.get("provenance") or {}).get("diagnostics")
    if diagnostics is not None:
        out["diagnostics"] = diagnostics
    return out
