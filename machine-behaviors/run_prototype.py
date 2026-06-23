#!/usr/bin/env python3
"""End-to-end prototype: machine JSON -> OpenClaw agents -> completion write-back.

Default target is the Personal Health "Home Chronic Pain Monitor".  Runs fully
offline (dispatch/completion POSTs are dry-run) unless --live is passed and a PE
is reachable.  Prints the agent set, the reality-vector impact table, and the
both-side debug log summary.

    python3 run_prototype.py                       # Home Chronic Pain Monitor
    python3 run_prototype.py --machine X.json       # any machine
    python3 run_prototype.py --live                 # also POST to a running PE
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4

from behavior_log import BehaviorLogger, SIDE_DISPATCH, SIDE_OPENCLAW, read_log, clear_logs
from derive_agents import derive, load_config, _abs
import dispatch_side
import openclaw_side


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine", default="HomeChronicPainMonitor.json")
    parser.add_argument("--live", action="store_true", help="POST to a running PE")
    args = parser.parse_args()

    cfg = load_config()
    path = _abs(cfg["machinesDir"]) / args.machine
    plan = derive(path, cfg)
    machine_meta = dispatch_side.load_machine_meta(plan, cfg)
    clear_logs()

    m = plan["machine"]
    print(f"\n=== {m['name']}  ({m['code']}) ===")
    print(f"class={m['machineClass']}  domain={plan['domain']['displayName']}  "
          f"output-region=[{m['outputRegion']['offset']}:"
          f"{m['outputRegion']['offset'] + m['outputRegion']['length']}]")
    print(f"agent families (domain default): {', '.join(plan['domain']['agentFamilies'])}\n")

    agent_index = {a["agent"]: a for a in plan["agents"]}
    completions = []
    for out in plan["outputs"]:
        agent_rec = agent_index[out["agent"]]
        corr = str(uuid4())
        d_log = BehaviorLogger(SIDE_DISPATCH, corr)
        o_log = BehaviorLogger(SIDE_OPENCLAW, corr)
        envelope = dispatch_side.build_envelope(plan, machine_meta, out, agent_rec, cfg, corr)
        dispatch_side.dispatch(envelope, agent_rec, cfg, d_log, live=args.live)
        result = openclaw_side.run(envelope, agent_rec, cfg, o_log, live=args.live)
        completions.append((out, agent_rec, result))

    # ---- agent set + reality-vector impact table -----------------------------
    print("OpenClaw agents associated with this machine:")
    for a in plan["agents"]:
        rv = a["realityVectorImpact"]
        rv_str = (f"[{rv['offset']}:{rv['offset'] + rv['length']}]" if rv else "none (observe)")
        outs = ", ".join(f"#{i}" for i in a["handlesOutputs"])
        print(f"  • {a['agent']:<28} mode={a['autonomyMode']:<14} "
              f"outputs={outs:<10} reality-vector-impact={rv_str}")
        if rv:
            print(f"      └─ completion sensor: {a['completionSensorId']}  "
                  f"positions={a['affectedPositions']}")

    print("\nPer-output round trip (agent textual response → PE vector positions):")
    for out, agent_rec, result in completions:
        mode = out["autonomyMode"]
        ext = {f["semantic"]: f["value"] for f in result["extracted"]}
        blocked = ext.get("failed", 0.0) >= 1.0
        verdict = "blocked→governance" if blocked else mode
        print(f"  {out['label']} ({out['rag']}) → {out['agent']} [{verdict}]")
        # show the raw textual response (verdict line) and the mapped vector
        first_line = result["responseText"].splitlines()[0]
        print(f"      response: \"{first_line}…\"  → extracted {ext}")
        for c in result["completions"]:
            region = c["region"]
            pairs = ", ".join(f"{s}={v}" for s, v in zip(c["metadata"]["semantics"], c["values"]))
            print(f"      writes [{region['offset']}:{region['offset'] + region['length']}] "
                  f"({len(c['values'])} positions): {pairs}")

    d_records = read_log(SIDE_DISPATCH)
    o_records = read_log(SIDE_OPENCLAW)
    print(f"\nDebug logs: dispatch side {len(d_records)} events "
          f"({BehaviorLogger(SIDE_DISPATCH).path.name}), "
          f"openclaw side {len(o_records)} events "
          f"({BehaviorLogger(SIDE_OPENCLAW).path.name})")
    print("Both sides share correlationId — grep one id end-to-end across the boundary.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
