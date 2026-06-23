#!/usr/bin/env python3
"""Derive OpenClaw agents (and their reality-vector impact) from a machine JSON.

READ-ONLY with respect to the corpus.  This never writes into a machine file and
never touches a PE loader.  It produces an *external* behavior plan:

  1. read the machine definition (triggerConfig.rules + sequences + perceptualMapping);
  2. determine the set of appropriate OpenClaw agents for the machine, using the
     machine's domain defaultAgentFamilies + the per-output action semantics;
  3. for each agent, compute the reality-vector mapping it impacts on completion
     (its PE completion sensor region) and a schema-valid agentBinding plus the
     OpenClaw integration template values.

The agentBinding shape matches RealityEngine_Machines/schemas/agent-binding.schema.json
exactly, so a derived binding could later be promoted into a machine file (an
agent-dispatcher) without rework — but here it stays a sidecar.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent

_AUTONOMY_RANK = {"observe": 0, "advise": 1, "supervised-act": 2, "automated-act": 3}

# Generic family-token -> related-word lexicon.  Keeps agent selection
# domain-agnostic: a family is scored by how well its name tokens (expanded
# through this lexicon) match the output's action/label/description text.
_LEXICON: dict[str, list[str]] = {
    "medication": ["medication", "opioid", "rx", "dose", "refill", "pdmp", "drug",
                   "prescription", "pharmacy", "dispense", "ort", "dire", "naloxone"],
    "adherence": ["adherence", "compliance", "regimen", "missed", "schedule"],
    "caregiver": ["caregiver", "nurse", "visit", "family", "consult", "physician",
                  "escalat", "urgent", "crisis", "outreach", "home"],
    "support": ["support", "assist", "help", "coordination"],
    "wellness": ["wellness", "activity", "exercise", "therapy", "pt", "walking",
                 "lifestyle", "occupational", "function", "screening", "deconditioning"],
    "coach": ["coach", "coaching", "encourage", "habit", "goal"],
    "fall": ["fall", "balance", "gait", "mobility", "trip"],
    "risk": ["risk", "hazard", "forecast", "predict"],
    "benefits": ["benefit", "eligibility", "enroll", "coverage", "prior auth", "insurance"],
    "navigation": ["navigation", "navigate", "referral", "route", "access"],
    "care": ["care", "plan", "referral", "coordination"],
    "coordinator": ["coordinator", "coordination", "schedule", "plan"],
}


def as_object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def load_config() -> dict[str, Any]:
    for name in ("config.json", "config.example.json"):
        path = HERE / name
        if path.exists():
            cfg = json.loads(path.read_text())
            break
    else:
        raise SystemExit("no config.json or config.example.json found")
    # env overrides
    if os.environ.get("MACHINES_DIR"):
        cfg["machinesDir"] = os.environ["MACHINES_DIR"]
    return cfg


def _abs(cfg_path: str) -> Path:
    p = Path(cfg_path)
    return p if p.is_absolute() else (HERE / p).resolve()


_JSON_CACHE: dict[str, Any] = {}


def _load_json(path: Path) -> Any:
    """Cache registry/manifest reads — derive() is called per-machine (1174x)."""
    key = str(path)
    if key not in _JSON_CACHE:
        _JSON_CACHE[key] = json.loads(Path(path).read_text())
    return _JSON_CACHE[key]


def primary_domain(metadata: dict[str, Any]) -> str:
    tagging = as_object(metadata.get("tagging"))
    return str(tagging.get("primaryDomain") or metadata.get("category")
               or metadata.get("domain") or "missing")


def machine_code(machine: dict[str, Any], metadata: dict[str, Any], path: Path) -> str:
    trig = as_object(metadata.get("triggerConfig"))
    if trig.get("processId"):
        return str(trig["processId"]).lower()
    match = re.match(r"([A-Za-z]+[-_]?\d+)", path.stem)
    return (match.group(1) if match else path.stem).replace("_", "-").lower()


def _short_agent(agent: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", agent.lower()).strip("-")


def _output_index(matches: list[Any]) -> int:
    for idx, value in enumerate(matches):
        if value:
            return idx
    return 0


def _action_for_sequence(machine: dict[str, Any], sequence_id: str) -> str:
    """Pull the human action text declared on the firing output vector."""
    for seq in as_list(machine.get("sequences")):
        seq = as_object(seq)
        if seq.get("id") != sequence_id:
            continue
        for vec in as_list(seq.get("vectors")):
            for out in as_list(as_object(vec).get("outputVectors")):
                action = as_object(as_object(out).get("metadata")).get("action")
                if action:
                    return str(action)
    return ""


def select_agent(haystack: str, families: list[str]) -> tuple[str, int]:
    """Pick the best-fitting agent family for an output's action/label text.

    Returns (agent, score).  Matching is whole-token, not substring: a substring
    match falsely fires single-word cues like "ort" (the ORT opioid tool) inside
    "Short".  Multiword cues (e.g. "prior auth") still match as a phrase.
    """
    haystack = haystack.lower()
    htokens = set(re.findall(r"[a-z0-9]+", haystack))
    best, best_score = (families[0] if families else "support_agent"), -1
    for family in families:
        tokens = [t for t in family.lower().replace("_", " ").split() if t != "agent"]
        words = set(tokens)
        for tok in tokens:
            words.update(_LEXICON.get(tok, []))
        score = 0
        for w in words:
            if (" " in w and w in haystack) or (" " not in w and w in htokens):
                score += 1
        if score > best_score:
            best, best_score = family, score
    return best, best_score


def resolve_autonomy(domain_default: str, allowed_modes: list[str],
                     rag: str, process_status: str, label: str) -> str:
    """Cap the domain default by the machine class and by RAG/positive-state."""
    default = domain_default if domain_default in _AUTONOMY_RANK else "advise"
    capped_rank = _AUTONOMY_RANK[default]
    if allowed_modes:
        capped_rank = min(capped_rank, max(_AUTONOMY_RANK[m] for m in allowed_modes))
    # positive / OK terminal states only need observation
    positive = (rag == "GREEN" or process_status in {"ok", "info"}
                or any(k in label.upper() for k in ("MANAGED", "_OK", "STABLE", "CONTROLLED")))
    if positive:
        capped_rank = min(capped_rank, _AUTONOMY_RANK["observe"])
    for mode, rank in _AUTONOMY_RANK.items():
        if rank == capped_rank:
            return mode
    return "advise"


def _autonomy_policy(mode: str) -> dict[str, Any]:
    table = {
        "observe": dict(stage=0, writeBackType="none", canWriteBack=False,
                        canStageActions=False, canExecuteActions=False,
                        requiresHumanApproval=False, requiresRunbook=False, blockedWhenRag=[]),
        "advise": dict(stage=1, writeBackType="pe-sensor", canWriteBack=True,
                       canStageActions=False, canExecuteActions=False,
                       requiresHumanApproval=False, requiresRunbook=False, blockedWhenRag=[]),
        "supervised-act": dict(stage=2, writeBackType="pe-sensor", canWriteBack=True,
                               canStageActions=True, canExecuteActions=False,
                               requiresHumanApproval=True, requiresRunbook=True,
                               blockedWhenRag=["RED"]),
        "automated-act": dict(stage=3, writeBackType="pe-sensor", canWriteBack=True,
                              canStageActions=True, canExecuteActions=True,
                              requiresHumanApproval=False, requiresRunbook=True,
                              blockedWhenRag=["AMBER", "RED"], rollbackRequired=True),
    }
    return {"mode": mode, **table[mode]}


def _risk_controls(mode: str) -> dict[str, Any]:
    elevated = mode in {"supervised-act", "automated-act"}
    return {
        "requiresHumanApproval": elevated,
        "requiresRunbook": elevated,
        "maxAutonomy": mode,
        "blockedWhenRag": ["RED"] if mode == "supervised-act" else (
            ["AMBER", "RED"] if mode == "automated-act" else []),
    }


def _write_back(mode: str, code: str, agent: str, region: dict[str, int],
                ttl: int, semantics: list[str]) -> dict[str, Any]:
    if mode == "observe":
        return {"type": "none"}
    sensor_id = f"acp.openclaw.{code}.{_short_agent(agent)}.completion"
    name = f"OpenClaw {code} {agent} completion"
    return {
        "type": "pe-sensor",
        "provider": "acp",
        "sensorId": sensor_id,
        "name": name,
        "region": dict(region),
        "semantics": list(semantics),
        "ttlMs": ttl,
        "normalization": "already-normalized-0-1",
        "ingest": {
            "endpoint": "/api/integrations/completions",
            "method": "POST",
            "triggerPush": False,
            "compactPush": True,
        },
        "sourceMapping": {
            "id": f"acp-{code}-{_short_agent(agent)}-completion",
            "sensorId": sensor_id,
            "name": name,
            "region": dict(region),
            "ttlMs": ttl,
        },
    }


# --- agent response -> PE vector mapping -------------------------------------
# The round-trip contract.  Each field declares (a) how to extract one value from
# the agent's response — JSON pointer for a structured turn, with a text rule as
# fallback so a free-text OpenClaw turn still maps deterministically — (b) the
# textual->value mapping, and (c) the perceptual-space position it lands at.  The
# number of fields (hence affected positions) grows with autonomy: advise writes
# 4, supervised-act adds review_required (5), automated-act adds executed +
# rollback_ok (6).  Fields may target different regions, so a single response can
# fan out to multiple, non-contiguous vector positions.

_VERDICT_CLASS = {  # actionClass scalar: what the agent actually did
    "observe": 0.25, "observed": 0.25, "monitor": 0.25, "monitored": 0.25,
    "advise": 0.5, "advised": 0.5, "recommend": 0.5, "recommended": 0.5,
    "stage": 0.75, "staged": 0.75, "draft": 0.75, "drafted": 0.75, "prepared": 0.75,
    "execute": 1.0, "executed": 1.0, "performed": 1.0,
}
_CONFIDENCE_PHRASE = {"very low": 0.1, "low": 0.3, "moderate": 0.6, "medium": 0.6,
                      "high": 0.9, "very high": 0.97}
_YES = {"yes": 1.0, "true": 1.0, "y": 1.0}
_NO = {"no": 0.0, "false": 0.0, "n": 0.0}


def _field(semantic: str, response_key: str, value_type: str, normalization: str,
           text_rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "semantic": semantic,
        "valueType": value_type,
        "normalization": normalization,
        "extract": {
            "jsonPointer": f"/{response_key}",
            "responseKey": response_key,
            "textFallback": text_rule,
        },
    }


def _response_field_spec(mode: str) -> list[dict[str, Any]]:
    base = [
        _field("completed", "completed", "binary", "binary",
               {"type": "enum-keyword", "default": 0.0,
                "keywords": {**_YES, "completed": 1.0, "staged": 1.0, "acknowledged": 1.0,
                             "done": 1.0, "resolved": 1.0,
                             **_NO, "unable": 0.0, "blocked": 0.0, "failed": 0.0, "deferred": 0.0}}),
        _field("failed", "failed", "binary", "binary",
               {"type": "enum-keyword", "default": 0.0,
                "keywords": {**_YES, "failed": 1.0, "blocked": 1.0, "unable": 1.0, "error": 1.0, **_NO}}),
        _field("confidence", "confidence", "scalar", "scalar-0-1",
               {"type": "scalar-phrase", "default": 0.5, "phrases": _CONFIDENCE_PHRASE,
                "numberRegex": r"(\d+(?:\.\d+)?)"}),
        # actionClass is fed by the "verdict" response key
        _field("actionClass", "verdict", "scalar", "scalar-0-1",
               {"type": "enum-keyword", "default": 0.25, "keywords": _VERDICT_CLASS}),
    ]
    if mode == "supervised-act":
        base.append(_field("review_required", "review_required", "binary", "binary",
                    {"type": "enum-keyword", "default": 0.0,
                     "keywords": {**_YES, "required": 1.0, "escalate": 1.0, "governance": 1.0,
                                  "approval required": 1.0, "human review": 1.0, **_NO}}))
    if mode == "automated-act":
        base.append(_field("executed", "executed", "binary", "binary",
                    {"type": "enum-keyword", "default": 0.0,
                     "keywords": {**_YES, "executed": 1.0, "performed": 1.0, **_NO}}))
        base.append(_field("rollback_ok", "rollback_ok", "binary", "binary",
                    {"type": "enum-keyword", "default": 0.0,
                     "keywords": {**_YES, "ready": 1.0, "available": 1.0, "reversible": 1.0, **_NO}}))
    return base


def _build_response_mapping(mode: str, sensor_id: str | None,
                            region: dict[str, int] | None,
                            fields: list[dict[str, Any]]) -> dict[str, Any]:
    if mode == "observe":
        return {
            "schemaVersion": "1.0.0",
            "responseContract": "structured-keys-or-text",
            "mode": "observe",
            "fields": [{
                "semantic": "observed",
                "valueType": "binary",
                "normalization": "binary",
                "extract": {"jsonPointer": "/observed", "responseKey": "observed",
                            "textFallback": {"type": "enum-keyword", "default": 1.0,
                                             "keywords": {**_YES, "observed": 1.0, "noted": 1.0,
                                                          "recorded": 1.0, **_NO}}},
                "target": None,   # observe writes no PE vector
            }],
        }
    return {
        "schemaVersion": "1.0.0",
        "responseContract": "structured-keys-or-text",
        "mode": mode,
        "fields": [
            {"semantic": f["semantic"], "valueType": f["valueType"],
             "normalization": f["normalization"], "extract": f["extract"],
             "target": {"sensorId": sensor_id, "region": dict(region), "index": i}}
            for i, f in enumerate(fields)
        ],
    }


def derive(machine_path: Path, cfg: dict[str, Any] | None = None,
           region_base: int | None = None) -> dict[str, Any]:
    cfg = cfg or load_config()
    data = json.loads(Path(machine_path).read_text())
    machine = as_object(data.get("machine"))
    metadata = as_object(machine.get("metadata"))
    mapping = as_object(machine.get("perceptualMapping"))
    trigger_config = as_object(metadata.get("triggerConfig"))
    rules = [as_object(r) for r in as_list(trigger_config.get("rules"))]

    domain = primary_domain(metadata)
    registry = _load_json(_abs(cfg["registryPath"]))
    manifest = _load_json(_abs(cfg["manifestPath"]))
    domain_entry = as_object(as_object(manifest.get("domains")).get(domain))
    families = [str(x) for x in as_list(domain_entry.get("defaultAgentFamilies"))] or ["support_agent"]
    default_autonomy = str(domain_entry.get("defaultAutonomy", "advise"))
    machine_class = str(metadata.get("machineClass", "signal-monitor"))
    class_def = as_object(as_object(registry.get("agentReadyMachineClasses")).get(machine_class))
    allowed_modes = [str(x) for x in as_list(class_def.get("allowedAutonomyModes"))]

    code = machine_code(machine, metadata, Path(machine_path))
    warnings: list[str] = []

    # machine-level context: used only to rescue outputs with no per-output signal
    machine_context = " ".join([
        str(machine.get("description", "")),
        " ".join(str(t) for t in as_list(metadata.get("tags"))),
        " ".join(as_object(metadata.get("sensorNormalization")).keys()),
        str(metadata.get("populationFocus", "")),
        str(metadata.get("outputSpace", "")),
    ])

    # one record per CES output cell
    outputs: list[dict[str, Any]] = []
    for rule in rules:
        seq_id = str(rule.get("sequenceId"))
        idx = _output_index(as_list(rule.get("outputMatches")))
        action = _action_for_sequence(machine, seq_id)
        desc = str(rule.get("description", ""))
        label = desc.split(":")[0].strip() if ":" in desc else seq_id
        rag = str(rule.get("ragStatusCode", ""))
        status = str(rule.get("processStatus", ""))
        haystack = " ".join([action, desc, label, seq_id])
        agent, score = select_agent(haystack, families)
        selection_basis = "per-output"
        # rescue no-signal outputs with machine-level context before defaulting
        if score <= 0 and machine_context.strip():
            agent2, score2 = select_agent(haystack + " " + machine_context, families)
            if score2 > 0:
                agent, score, selection_basis = agent2, score2, "machine-context"
        mode = resolve_autonomy(default_autonomy, allowed_modes, rag, status, label)
        low_conf = score <= 0
        if low_conf:
            warnings.append(
                f"low-confidence agent selection for output '{label}' "
                f"({seq_id}): no keyword signal, defaulted to {agent}")
        outputs.append({
            "index": idx, "label": label, "rag": rag, "processStatus": status,
            "sequenceId": seq_id, "action": action, "agent": agent,
            "autonomyMode": mode, "selectionScore": score,
            "selectionBasis": selection_basis, "lowConfidence": low_conf,
        })

    # group outputs by agent; allocate a completion region per write-back agent
    region_cfg = as_object(cfg.get("completionRegions"))
    base = region_base if region_base is not None else int(region_cfg.get("baseOffset", 4400))
    length = int(region_cfg.get("length", 4))
    ttl = int(region_cfg.get("ttlMs", 300000))
    semantics = [str(x) for x in as_list(region_cfg.get("semantics"))] or \
        ["completed", "failed", "confidence", "actionClass"]

    by_agent: dict[str, list[dict[str, Any]]] = {}
    for out in outputs:
        by_agent.setdefault(out["agent"], []).append(out)

    agents: list[dict[str, Any]] = []
    cursor = base  # next free perceptual-space offset for a completion region
    for agent in sorted(by_agent):
        handled = by_agent[agent]
        # the agent's effective autonomy is the most-permissive across its outputs
        mode = max((h["autonomyMode"] for h in handled), key=lambda m: _AUTONOMY_RANK[m])
        actions = []
        for h in handled:
            if h["action"] and h["action"] not in actions:
                actions.append(h["action"])
        if not actions:
            actions = ["Review CES output and recommend next action."]

        # the response field spec determines how many vector positions this agent
        # affects on completion, hence the region length (variable per autonomy).
        fields = _response_field_spec(mode) if mode != "observe" else []
        region = None
        if mode != "observe":
            region = {"offset": cursor, "length": len(fields)}
            cursor += len(fields)
        field_semantics = [f["semantic"] for f in fields] or list(semantics)

        trigger_hook = f"{code}-{'-'.join(sorted({h['sequenceId'] for h in handled}))}"[:120]
        binding = {
            "agent": agent,
            "mode": mode,
            "trigger": trigger_hook,
            "allowedActions": actions,
            "writeBack": _write_back(mode, code, agent, region or {}, ttl, field_semantics),
            "autonomyPolicy": _autonomy_policy(mode),
            "riskControls": _risk_controls(mode),
        }
        response_mapping = _build_response_mapping(
            mode, binding["writeBack"].get("sensorId"), region, fields)
        agents.append({
            "agent": agent,
            "autonomyMode": mode,
            "handlesOutputs": [h["index"] for h in handled],
            "handlesSequences": [h["sequenceId"] for h in handled],
            "realityVectorImpact": region,           # the PE region written on completion
            "affectedPositions": field_semantics if region else [],
            "completionSensorId": binding["writeBack"].get("sensorId"),
            "openclaw": _openclaw_template(cfg, agent),
            "agentBinding": binding,
            "responseMapping": response_mapping,      # textual->value round-trip contract
        })

    return {
        "machine": {
            "id": str(machine.get("id") or Path(machine_path).stem),
            "name": str(machine.get("name")),
            "code": code,
            "machineClass": machine_class,
            "domain": domain,
            "inputRegion": as_object(mapping.get("input")),
            "outputRegion": as_object(mapping.get("output")),
        },
        "domain": {
            "name": domain,
            "displayName": str(domain_entry.get("displayName", domain)),
            "defaultAutonomy": default_autonomy,
            "agentFamilies": families,
            "allowedAutonomyModes": allowed_modes,
        },
        "outputs": outputs,
        "agents": agents,
        "warnings": warnings,
    }


def _openclaw_template(cfg: dict[str, Any], agent: str) -> dict[str, Any]:
    oc = as_object(cfg.get("openclaw"))
    return {
        "id": oc.get("id"),
        "kind": oc.get("kind"),
        "platform": oc.get("platform"),
        "surface": oc.get("surface"),
        "command": oc.get("command"),
        "gatewayUrl": oc.get("gatewayUrl"),
        "sessionKey": oc.get("sessionKey"),
        "targetAgent": agent,
        "dispatchMode": oc.get("dispatchMode"),
        "completionMode": oc.get("completionMode"),
        "completionSourceMappingId": oc.get("completionSourceMappingId"),
    }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Derive OpenClaw agents from a machine JSON.")
    parser.add_argument("machine_file")
    args = parser.parse_args()
    cfg = load_config()
    path = Path(args.machine_file)
    if not path.is_absolute() and not path.exists():
        path = _abs(cfg["machinesDir"]) / args.machine_file
    print(json.dumps(derive(path, cfg), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
