# Agent runtimes: one behavior, rendered per runtime

OpenClaw is sunset in v0.0.3 (`RealityEngine_CI/docs/AGENT_GOVERNANCE_REVIEW.md`).
So that the move to ACP-hosted agents, or whatever replaces OpenClaw, changes an
adapter rather than every agent, agent generation is split into two steps:

1. **Behavior.** What an agent bound to a machine does. It is runtime-neutral
   and follows `RealityEngine_Machines/schemas/agent-behavior.schema.json`:
   - role and dispatch mode;
   - the machine and its regions;
   - abstract capabilities;
   - instructions and output contract;
   - the `agentBinding`, the machine's completion Source, unchanged;
   - the response mapping.

   It names no runtime, gateway, session key or tool implementation.
2. **Render.** A runtime adapter turns a behavior into that runtime's artifact.

```
machine ──derive_behavior()──► behavior ──runtimes.openclaw.render()──► OC agent spec (agents/)
                                 ▲     └───runtimes.acp.render()──────► ACP session plan
factory domain (authored) ───────┘
```

## Where behaviors come from

- **The corpus.** `oc_agent_template.derive_behavior(machine)` derives the
  input-analyst behavior of every corpus machine. `oc_agent_template.derive()`
  is now `openclaw.render(derive_behavior(machine))`, and it reproduces every
  committed agent in `agents/` byte for byte. `materialize_agents.py --check`
  and `tests/run_runtime_tests.py` hold that.
- **Factory domains.** A domain authors its behaviors directly, beside its
  machines. The first is `RealityEngine_Machines/machine_factory/domains/ai-governance-regime/agent-candidates/`:
  - 16 agents across seven workflows;
  - roles `step-agent`, `watchdog-agent` and `deterministic-tool`;
  - dispatch `frame-step` (a workflow step on a clone) or `ces-trigger` (a
    fixture's CES).

## Capabilities

A behavior grants abstract capabilities such as `frame.read`, `pod.write`,
`kline.read` and `lifecycle.frame.open`. `runtimes/capabilities.json` maps each
one to:
- its scope: `frame`, `instance` or `external`;
- the existing endpoint it wraps;
- the MCP server and tools that provide it;
- whether that tool exists yet.

A capability with no mapping is a `RenderError`, never a silent drop.

| | OpenClaw (today) | ACP (v0.0.3 target) |
|---|---|---|
| Transport | xACP over the gateway (`openclaw` block: gateway URL, session key) | Agent Client Protocol over stdio: `initialize`, `session/new`, `session/prompt` |
| Agent identity | OpenClaw agent id (64 characters at most) | the behavior's `agentId` |
| MCP servers | gateway-wide: one server shared by every agent, an accepted deficiency (RealityEngine_CI#714) | per session: `session/new` carries the session's own `mcpServers`, so a frame-scoped server holds only that frame's capability |
| Frame isolation | by policy | by construction |
| Result | completion through the `agentBinding` writeBack | the same |

The ACP plan writes the values known only at dispatch as placeholders, which the
dispatcher substitutes:
- `${frameId}`, `${continuationId}` and `${capability}` for frame-scoped servers;
- `${registryUrl}` and `${peUrl}` for instance-scoped ones;
- `${workdir}` and `${sessionId}` for the session.

`pendingCapabilities` lists every capability whose tool isn't implemented yet, or
has no server command configured (`cfg["acp"]["mcpServerCommands"]`).

## Commands

```bash
python3 render_behavior.py --behavior ../../RealityEngine_Machines/machines/domains/agriculture/AGX001_*.json
python3 render_behavior.py --runtime openclaw <behavior>.agent.json
python3 render_behavior.py --runtime openclaw --policy <behavior>.agent.json
python3 render_behavior.py --runtime acp <behavior>.agent.json --out /tmp/acp-plans
MB_DEBUG=0 python3.13 tests/run_runtime_tests.py
```

## What stays to do for the transition

- **Choose the ACP runtime** and pin its protocol version.
- **Write an ACP `AgentDriver`** in RealityEngine_Manager's instantiation
  component. The `AgentDriver` interface is already runtime-neutral
  (`agents.ts`), so this is a third driver beside `simulated` and `openclaw`. It
  starts the agent, sends the rendered plan with the placeholders substituted,
  and posts the completion.
- **Implement the MCP servers** behind the capabilities marked
  `implemented: false`. The frame-scoped lifecycle server comes first; it is
  RealityEngine_CI#714's frame-scoped tools.
- **Corpus sensor ids.** The corpus agents' writeBack sensor ids keep their
  historical `acp.openclaw.*` names, because the PE source mappings are
  registered under them. Renaming them is a coordinated PE-mapping change, not
  part of this split. New domains use neutral ids (`aigov.*`).
