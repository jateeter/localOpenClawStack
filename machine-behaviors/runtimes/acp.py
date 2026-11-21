"""Render a runtime-neutral agent behavior as an ACP session plan.

The plan is what a dispatcher (RealityEngine_Manager's instantiation component,
or the PE for a CES-triggered agent) sends an Agent Client Protocol agent:
initialize, then session/new with the session's own MCP servers, then
session/prompt. Frame-scoped capabilities become MCP servers started for this
session only, each holding just this frame's step capability, which is the
frame isolation OpenClaw's gateway-wide MCP cannot give (RealityEngine_CI#714).

Values known only at dispatch are placeholders the dispatcher substitutes:
${frameId}, ${continuationId}, ${capability}, ${workdir}, ${registryUrl},
${peUrl}. The agent's result still arrives through the behavior's agentBinding
writeBack; the plan carries the response mapping that turns its answer into
completion values. No runtime has been chosen yet (OpenClaw is sunset in
v0.0.3), so the agent command comes from cfg["acp"]["agentCommand"] or is null.
"""
from __future__ import annotations

from typing import Any

from . import resolve_capabilities

RUNTIME = "acp"
PROTOCOL_VERSION = 1
SCOPE_ENV = {
    "frame": [("RE_FRAME_ID", "${frameId}"), ("RE_CONTINUATION_ID", "${continuationId}"),
              ("RE_FRAME_CAPABILITY", "${capability}")],
    "instance": [("RE_REGISTRY_URL", "${registryUrl}"), ("RE_PE_URL", "${peUrl}")],
    "external": [],
}


def mcp_servers(behavior: dict[str, Any], cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """One stdio MCP server per server name the capabilities need, scoped to this session."""
    commands = ((cfg.get("acp") or {}).get("mcpServerCommands") or {})
    servers: dict[str, dict[str, Any]] = {}
    pending = []
    for cap, spec in resolve_capabilities(behavior):
        if spec["server"] == "dispatcher":  # pe.completion.write: the dispatcher posts the completion
            continue
        s = servers.setdefault(spec["server"], {
            "name": spec["server"],
            "command": (commands.get(spec["server"]) or {}).get("command"),
            "args": list((commands.get(spec["server"]) or {}).get("args", [])),
            "env": [{"name": n, "value": v} for n, v in SCOPE_ENV[spec["scope"]]],
            "_meta": {"scope": spec["scope"], "capabilities": [], "tools": []},
        })
        s["_meta"]["capabilities"].append(cap)
        s["_meta"]["tools"] += [t for t in spec["tools"] if t not in s["_meta"]["tools"]]
        if not spec["implemented"] or s["command"] is None:
            pending.append(cap)
    return list(servers.values()), pending


def render(behavior: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    servers, pending = mcp_servers(behavior, cfg)
    r = behavior["reasoning"]
    prompt_text = "\n\n".join([r["instructions"], "OUTPUT CONTRACT", r["outputContract"]])
    return {
        "schemaVersion": "1.0.0",
        "runtime": RUNTIME,
        "behaviorId": behavior["behaviorId"],
        "agentId": behavior["agentId"],
        "agent": {"command": ((cfg.get("acp") or {}).get("agentCommand")), "role": behavior["role"]},
        "dispatch": behavior["dispatch"],
        "initialize": {"protocolVersion": PROTOCOL_VERSION,
                       "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}},
        "sessionNew": {"cwd": "${workdir}", "mcpServers": servers},
        "sessionPrompt": {"sessionId": "${sessionId}", "prompt": [{"type": "text", "text": prompt_text}]},
        "completion": {"responseMapping": behavior["responseMapping"],
                       "writeBack": behavior["agentBinding"]["writeBack"],
                       "autonomy": behavior["agentBinding"]["autonomyPolicy"]},
        "pendingCapabilities": pending,
    }
