#!/usr/bin/env python3
"""OC-Agent-Template — derive an input-analyst OpenClaw agent from a machine.

This is the generalized, reusable template the prototype "Home Chronic Pain
Monitor" agent instantiates.  Where `derive_agents.py` builds *output-side
actor* agents (one per CES terminal output, firing AFTER a CES and writing a
downstream completion sensor), this builds the complementary *input-side
analyst*:

  one OpenClaw agent, named after the machine, that reasons over current domain
  observations, produces the machine's NORMALIZED INPUT VECTOR, and writes it
  back to the PE INPUT region.  RE then runs the machine's deterministic CES
  logic on those agent-supplied inputs, so the agent's reasoned analysis is
  "acted upon in one of the CESs."

The two patterns compose into a full loop:

    observations -> [input-analyst] -> machine input region -> RE runs CES
                 -> CES terminal output -> [output-actor] -> action + completion

READ-ONLY with respect to the corpus.  The instance is a sidecar; it embeds a
canonical, promotion-ready `agentBinding` (validated against the corpus
`agent-binding.schema.json`) whose write-back targets the machine's own input
region instead of the reserved completion band.

Usage:
    python3 oc_agent_template.py HomeChronicPainMonitor.json            # print instance
    python3 oc_agent_template.py HomeChronicPainMonitor.json --write    # -> agents/<code>.oc-agent.json
    python3 oc_agent_template.py HomeChronicPainMonitor.json --demo     # simulate one analysis turn
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from derive_agents import (
    HERE, as_object, as_list, load_config, _abs, primary_domain,
    machine_code, _action_for_sequence, _output_index,
)

# Input-analyst is a sensing/advisory role: it writes a PE sensor (the input
# vector) and stages no action, so it is `advise` by construction.  This keeps
# it cleanly distinct from the output-side actor agents (supervised-act) and
# from the autonomy ceilings of life-safety domains.
_ANALYST_MODE = "advise"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _split_anchors(spec: str) -> dict[str, str]:
    """Parse a sensorNormalization string ("0.0=...,0.5=...,1.0=...") into anchors."""
    out = {"low": "", "mid": "", "high": ""}
    if not spec:
        return out
    # split on the 0.0= / 0.5= / 1.0= markers, keep the text after each
    parts = re.split(r"(0\.0=|0\.5=|1\.0=)", spec)
    bucket = {"0.0=": "low", "0.5=": "mid", "1.0=": "high"}
    i = 1
    while i < len(parts) - 0:
        marker = parts[i] if i < len(parts) else ""
        if marker in bucket:
            text = parts[i + 1] if i + 1 < len(parts) else ""
            out[bucket[marker]] = text.strip().strip(",").strip()
            i += 2
        else:
            i += 1
    return out


def _input_axes(metadata: dict[str, Any], length: int) -> tuple[list[dict[str, Any]], str]:
    """Derive one reasoning axis per input position.

    Preferred source is `sensorNormalization` (carries 0/0.5/1.0 anchors that
    ground the agent's reasoning).  Falls back to `inputSemantics`, then generic
    positional keys.  Returns (axes, basis).
    """
    norm = as_object(metadata.get("sensorNormalization"))
    keys = list(norm.keys())
    if len(keys) == length:
        axes = [{"index": i, "key": keys[i], "anchors": _split_anchors(str(norm[keys[i]]))}
                for i in range(length)]
        return axes, "sensorNormalization"
    sem = [str(s) for s in as_list(metadata.get("inputSemantics"))]
    if len(sem) == length:
        axes = [{"index": i, "key": _slug(sem[i]) or f"input_{i}",
                 "anchors": {"low": "", "mid": "", "high": ""}} for i in range(length)]
        return axes, "inputSemantics"
    axes = [{"index": i, "key": f"input_{i}", "anchors": {"low": "", "mid": "", "high": ""}}
            for i in range(length)]
    return axes, "positional-fallback"


def _sequence_catalog(machine: dict[str, Any], metadata: dict[str, Any]) -> list[dict[str, Any]]:
    trig = as_object(metadata.get("triggerConfig"))
    catalog: list[dict[str, Any]] = []
    for rule in as_list(trig.get("rules")):
        rule = as_object(rule)
        seq_id = str(rule.get("sequenceId"))
        desc = str(rule.get("description", ""))
        label = desc.split(":")[0].strip() if ":" in desc else seq_id
        catalog.append({
            "sequenceId": seq_id,
            "label": label,
            "rag": str(rule.get("ragStatusCode", "")),
            "outputIndex": _output_index(as_list(rule.get("outputMatches"))),
            "action": _action_for_sequence(machine, seq_id),
        })
    return catalog


def _system_prompt(name: str, description: str, axes: list[dict[str, Any]],
                   catalog: list[dict[str, Any]], population: str) -> str:
    lines = [
        f"You are the OpenClaw input-analyst agent for the RealityEngine machine "
        f"\"{name}\".",
        "",
        "OBJECTIVE",
        f"Reason over the currently available observations and produce a "
        f"normalized assessment of the machine's input axes. {description}".strip(),
    ]
    if population:
        lines += ["", f"POPULATION: {population}"]
    lines += ["", "INPUT AXES (return one value in [0.0, 1.0] for each; anchors define the scale):"]
    for ax in axes:
        a = ax["anchors"]
        anchor = ""
        if a.get("low") or a.get("mid") or a.get("high"):
            anchor = f"  0.0 = {a['low']} | 0.5 = {a['mid']} | 1.0 = {a['high']}"
        lines.append(f"- {ax['key']} (index {ax['index']}):{anchor}")
    lines += ["", "WHAT YOUR INPUTS TRIGGER (RE evaluates the CES deterministically; "
              "you assert your expectation, RE decides):"]
    for c in catalog:
        act = f" -> action: {c['action']}" if c["action"] else ""
        lines.append(f"- {c['label']} ({c['rag'] or 'n/a'}){act}")
    return "\n".join(lines)


def _autonomy_policy() -> dict[str, Any]:
    return {
        "mode": _ANALYST_MODE, "stage": 1, "writeBackType": "pe-sensor",
        "canWriteBack": True, "canStageActions": False, "canExecuteActions": False,
        "requiresHumanApproval": False, "requiresRunbook": False, "blockedWhenRag": [],
    }


def _risk_controls() -> dict[str, Any]:
    return {"requiresHumanApproval": False, "requiresRunbook": False,
            "maxAutonomy": _ANALYST_MODE, "blockedWhenRag": []}


def _write_back(code: str, region: dict[str, int], ttl: int,
                semantics: list[str]) -> tuple[dict[str, Any], str]:
    sensor_id = f"acp.openclaw.{code}.input-analyst.assessment"
    name = f"OpenClaw {code} input-analyst assessment"
    src_id = f"acp-{code}-input-assessment"
    return {
        "type": "pe-sensor",
        "provider": "acp",
        "sensorId": sensor_id,
        "name": name,
        "region": dict(region),
        "semantics": list(semantics),
        "ttlMs": ttl,
        "normalization": "already-normalized-0-1",
        "ingest": {"endpoint": "/api/integrations/completions", "method": "POST",
                   "triggerPush": False, "compactPush": True},
        "sourceMapping": {"id": src_id, "sensorId": sensor_id, "name": name,
                          "region": dict(region), "ttlMs": ttl},
    }, sensor_id


# value extraction rule shared by every axis: the agent is contracted to return
# an already-normalized number; the phrase map is a deterministic fallback for a
# free-text turn.  Phrases are scale-direction-agnostic severity words; the
# numeric path (the contract) is authoritative when present.
_AXIS_PHRASES = {
    "crisis": 0.05, "severe": 0.15, "very high": 0.15, "high": 0.3, "elevated": 0.3,
    "poor": 0.3, "significant": 0.35, "moderate": 0.5, "borderline": 0.5,
    "manageable": 0.6, "fair": 0.6, "mild": 0.7, "low": 0.8, "stable": 0.8,
    "controlled": 0.8, "good": 0.85, "minimal": 0.9, "none": 1.0, "absent": 1.0,
}


def _response_mapping(axes: list[dict[str, Any]], sensor_id: str,
                      region: dict[str, int]) -> dict[str, Any]:
    fields = []
    for ax in axes:
        fields.append({
            "semantic": ax["key"], "valueType": "scalar", "normalization": "scalar-0-1",
            "extract": {
                "jsonPointer": f"/{ax['key']}",
                "responseKey": ax["key"],
                "textFallback": {"type": "scalar-phrase", "default": 0.5,
                                 "phrases": _AXIS_PHRASES,
                                 "numberRegex": r"(\d+(?:\.\d+)?)"},
            },
            "target": {"sensorId": sensor_id, "region": dict(region), "index": ax["index"]},
        })
    return {"schemaVersion": "1.0.0", "responseContract": "structured-keys-or-text",
            "mode": _ANALYST_MODE, "fields": fields}


def _openclaw_template(cfg: dict[str, Any], agent_id: str) -> dict[str, Any]:
    oc = as_object(cfg.get("openclaw"))
    return {
        "id": oc.get("id"), "kind": oc.get("kind"), "platform": oc.get("platform"),
        "surface": oc.get("surface"), "command": oc.get("command"),
        "gatewayUrl": oc.get("gatewayUrl"), "sessionKey": oc.get("sessionKey"),
        "targetAgent": agent_id, "dispatchMode": oc.get("dispatchMode"),
        "completionMode": oc.get("completionMode"),
        "completionSourceMappingId": f"acp-{agent_id}-input-assessment",
    }


def derive(machine_path: Path, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or load_config()
    data = json.loads(Path(machine_path).read_text())
    machine = as_object(data.get("machine"))
    metadata = as_object(machine.get("metadata"))
    mapping = as_object(machine.get("perceptualMapping"))
    in_region = as_object(mapping.get("input"))
    out_region = as_object(mapping.get("output"))
    length = int(in_region.get("length", 0))

    code = machine_code(machine, metadata, Path(machine_path))
    name = str(machine.get("name") or code)
    agent_id = _slug(name)
    domain = primary_domain(metadata)
    machine_class = str(metadata.get("machineClass", "signal-monitor"))

    warnings: list[str] = []
    if length < 1:
        warnings.append("machine has no usable perceptualMapping.input region")
        length = max(length, 0)
    axes, axis_basis = _input_axes(metadata, length)
    if axis_basis != "sensorNormalization":
        warnings.append(f"input axes derived from {axis_basis} "
                        "(no length-matched sensorNormalization)")
    catalog = _sequence_catalog(machine, metadata)
    if not catalog:
        warnings.append("machine has no triggerConfig.rules (no CES catalog)")

    ttl = int(as_object(cfg.get("completionRegions")).get("ttlMs", 300000))
    semantics = [ax["key"] for ax in axes]
    write_back, sensor_id = _write_back(code, in_region, ttl, semantics)
    region = dict(in_region)

    objective = (f"Produce the normalized input vector for \"{name}\" from current "
                 f"observations so RE can evaluate its CES sequences.")
    population = str(metadata.get("populationFocus", ""))
    system_prompt = _system_prompt(name, str(machine.get("description", "")),
                                   axes, catalog, population)
    output_contract = (
        "Return one structured line per input axis as `<axis_key>: <0.0-1.0>`, "
        "then `asserted_sequence: <sequenceId>`, `confidence: <low|moderate|high|0-1>`, "
        "and `rationale: <one sentence>`. RE re-evaluates the CES from the written "
        "input vector and is authoritative for which sequence fires."
    )

    allowed_action = (f"Assess current \"{name}\" state from available observations and "
                      f"write the normalized input vector to the PE input region "
                      f"[{region.get('offset')}:{region.get('offset', 0) + region.get('length', 0)}] "
                      f"for RE to evaluate.")
    agent_binding = {
        "agent": agent_id,
        "mode": _ANALYST_MODE,
        "trigger": f"{code}-input-assessment",
        "allowedActions": [allowed_action],
        "writeBack": write_back,
        "autonomyPolicy": _autonomy_policy(),
        "riskControls": _risk_controls(),
    }
    response_mapping = _response_mapping(axes, sensor_id, region)

    return {
        "schemaVersion": "1.0.0",
        "templateId": "oc-agent-template",
        "role": "input-analyst",
        "agentId": agent_id,
        "displayName": name,
        "machine": {
            "id": str(machine.get("id") or Path(machine_path).stem),
            "name": name, "code": code, "machineClass": machine_class,
            "domain": domain, "inputRegion": in_region, "outputRegion": out_region,
        },
        "openclaw": _openclaw_template(cfg, agent_id),
        "reasoning": {
            "objective": objective, "systemPrompt": system_prompt,
            "outputContract": output_contract, "inputAxes": axes,
            "sequenceCatalog": catalog,
        },
        "agentBinding": agent_binding,
        "responseMapping": response_mapping,
        "diagnostics": {"axisBasis": axis_basis, "warnings": warnings,
                        "inputLength": length, "sequenceCount": len(catalog)},
    }


# --- simulated analysis turn (offline demo) ----------------------------------

def demo_turn(instance: dict[str, Any]) -> dict[str, Any]:
    """Simulate one OpenClaw analysis turn and map it to the input vector.

    Stands in for an `openclaw acp` turn (M7).  Emits a structured-keys response
    for a deteriorating-pain scenario, then reuses the real openclaw_side
    extractor to turn that text into the PE input vector that RE will evaluate.
    """
    import openclaw_side
    axes = instance["reasoning"]["inputAxes"]
    catalog = instance["reasoning"]["sequenceCatalog"]
    # deteriorating scenario: low scores on the first axes (e.g. worsening pain)
    scripted = {ax["key"]: round(0.2 + 0.15 * i, 2) for i, ax in enumerate(axes)}
    asserted = catalog[0]["sequenceId"] if catalog else ""
    lines = [f"{k}: {v}" for k, v in scripted.items()]
    lines += [f"asserted_sequence: {asserted}", "confidence: high",
              "rationale: scripted deteriorating-state demo; values trend below threshold."]
    response_text = "\n".join(lines)
    targets, extracted = openclaw_side.apply_response_mapping(
        response_text, instance["responseMapping"])
    return {"responseText": response_text, "targets": targets, "extracted": extracted,
            "assertedSequence": asserted}


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Derive an OC-Agent-Template instance from a machine.")
    parser.add_argument("machine_file")
    parser.add_argument("--write", action="store_true", help="write agents/<code>.oc-agent.json")
    parser.add_argument("--demo", action="store_true", help="simulate one analysis turn")
    args = parser.parse_args()
    cfg = load_config()
    path = Path(args.machine_file)
    if not path.is_absolute() and not path.exists():
        path = _abs(cfg["machinesDir"]) / args.machine_file
    instance = derive(path, cfg)
    if args.write:
        dom = re.sub(r"[^a-z0-9]+", "-", str(instance["machine"]["domain"]).lower()).strip("-") or "uncategorized"
        out = HERE / "agents" / dom / f"{instance['agentId']}.oc-agent.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(instance, indent=2) + "\n")
        print(f"wrote {out.relative_to(HERE)}")
    if args.demo:
        result = demo_turn(instance)
        print("--- simulated agent response ---")
        print(result["responseText"])
        print("\n--- mapped input vector (-> PE input region) ---")
        for t in result["targets"]:
            print(f"  sensor {t['sensorId']} region {t['region']}")
            for sem, val in zip(t["semantics"], t["values"]):
                print(f"    {sem}: {val}")
        print(f"  agent asserts CES: {result['assertedSequence']} "
              "(RE re-evaluates and is authoritative)")
    if not args.write and not args.demo:
        print(json.dumps(instance, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
