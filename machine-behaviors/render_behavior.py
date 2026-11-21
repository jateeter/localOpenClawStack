#!/usr/bin/env python3
"""Render runtime-neutral agent behaviors for a runtime.

    python3 render_behavior.py --runtime openclaw BEHAVIOR.agent.json...
    python3 render_behavior.py --runtime acp BEHAVIOR.agent.json... [--out DIR]
    python3 render_behavior.py --runtime openclaw --policy BEHAVIOR.agent.json   # OpenClaw tool grants
    python3 render_behavior.py --behavior MACHINE.json                           # a corpus machine's neutral behavior

A behavior follows RealityEngine_Machines schemas/agent-behavior.schema.json.
--behavior derives one from a corpus machine, the same derivation the committed
OpenClaw agents in agents/ are rendered from. See RUNTIMES.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from derive_agents import load_config
from runtimes import RenderError, acp, openclaw


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    ap.add_argument("--runtime", choices=["openclaw", "acp"])
    ap.add_argument("--policy", action="store_true", help="print OpenClaw's capability grants instead of the spec")
    ap.add_argument("--behavior", action="store_true", help="inputs are corpus machines; print their behaviors")
    ap.add_argument("--out", type=Path, help="write one file per input into this directory")
    ap.add_argument("inputs", nargs="+", type=Path)
    args = ap.parse_args()
    cfg = load_config()
    for path in args.inputs:
        try:
            if args.behavior:
                import oc_agent_template
                doc, suffix = oc_agent_template.derive_behavior(path, cfg), ".agent.json"
            else:
                behavior = json.loads(path.read_text())
                if args.runtime == "acp":
                    doc, suffix = acp.render(behavior, cfg), ".acp-session.json"
                elif args.policy:
                    doc, suffix = openclaw.tool_policy(behavior), ".openclaw-policy.json"
                elif args.runtime == "openclaw":
                    doc, suffix = openclaw.render(behavior, cfg), ".oc-agent.json"
                else:
                    ap.error("--runtime is required unless --behavior")
        except RenderError as exc:
            print(f"render_behavior: {exc}", file=sys.stderr)
            return 1
        text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
        if args.out:
            args.out.mkdir(parents=True, exist_ok=True)
            stem = path.name.removesuffix(".agent.json").removesuffix(".json")
            (args.out / f"{stem}{suffix}").write_text(text)
        else:
            sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
