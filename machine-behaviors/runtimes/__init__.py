"""Runtime adapters for runtime-neutral agent behaviors.

A behavior (RealityEngine_Machines schemas/agent-behavior.schema.json) says what
an agent bound to a machine does; an adapter renders it for one runtime:

    runtimes.openclaw.render(behavior, cfg)  -> OC agent spec (oc-agent.schema.json)
    runtimes.acp.render(behavior, cfg)       -> ACP session plan

Capabilities are mapped through capabilities.json; a capability an adapter cannot
provide is an error, never a silent drop. See RUNTIMES.md.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent


class RenderError(ValueError):
    """A behavior cannot be rendered for a runtime."""


def capability_map() -> dict:
    return json.loads((HERE / "capabilities.json").read_text())


def resolve_capabilities(behavior: dict) -> list[tuple[str, dict]]:
    caps = capability_map()["capabilities"]
    unknown = [c for c in behavior.get("capabilities", []) if c not in caps]
    if unknown:
        raise RenderError(f"{behavior.get('behaviorId')}: capabilities no runtime maps: {unknown}")
    return [(c, caps[c]) for c in behavior.get("capabilities", [])]
