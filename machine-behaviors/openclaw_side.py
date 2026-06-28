#!/usr/bin/env python3
"""OpenClaw side: run the agent turn, map its TEXTUAL response into PE vectors.

In the real loop this is the external OpenClaw runner
(RealityEngine_CPP/docs/INTEGRATION_ARCHITECTURE.md → "External adapter"): it
reads an accepted ACP dispatch, drives `openclaw acp` through the gateway, and
posts the result back to PE through `/api/integrations/completions`.

An OpenClaw agent returns *natural language*, not numbers.  This module makes the
text -> value step explicit and well-defined:

  1. `_simulate_textual_response` stands in for the agent turn, emitting a
     structured-keys text block (what the agent is contracted to return).
  2. `apply_response_mapping` parses that response against the binding's
     `responseMapping` (JSON pointer first, text rule as fallback) and produces
     one normalized value per declared vector position — fanning out to one or
     more PE regions.
  3. one PE completion payload per target region
     (schemas/localai-completion-writeback.schema.json) is written back.

Debug logging on the OpenClaw side records the raw response text AND the extracted
per-position values, so the textual->value step is fully auditable.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib import request, error
from uuid import uuid4

from behavior_log import BehaviorLogger


def _as_object(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else (1.0 if x > 1 else x)


def _round_value(x: float) -> float:
    return round(float(x), 4)


# --- textual -> value extraction ---------------------------------------------

def _json_pointer(obj: Any, pointer: str) -> Any:
    if not pointer or not pointer.startswith("/"):
        return None
    node = obj
    for part in pointer.strip("/").split("/"):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None
    return node


def _structured_value(response: str, key: str) -> str | None:
    """Pull the value text for a `key: value` line from a structured-keys turn."""
    for line in response.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            if k.strip().lower() == key.lower():
                return v.strip()
    return None


def _match_rule(value_text: str, rule: dict[str, Any], *, preserve_number: bool = False) -> float:
    """Apply a textFallback rule to a (short) value string -> normalized value."""
    vt = value_text.lower()
    vtokens = set(re.findall(r"[a-z0-9]+", vt))

    def hit(needle: str) -> bool:
        return (needle in vt) if " " in needle else (needle in vtokens)

    kind = rule.get("type")
    if kind == "enum-keyword":
        for kw, val in rule["keywords"].items():
            if hit(kw):
                return float(val)
        m = re.search(rule.get("numberRegex", r"\b([01](?:\.0+)?)\b"), vt)
        if m:
            return float(m.group(1))
        return float(rule.get("default", 0.0))
    if kind == "scalar-phrase":
        for ph, val in rule["phrases"].items():
            if hit(ph):
                return float(val)
        m = re.search(rule.get("numberRegex", r"(\d+(?:\.\d+)?)"), vt)
        if m:
            num = float(m.group(1))
            if preserve_number:
                return num
            return _clamp01(num / 100.0 if num > 1 else num)
        return float(rule.get("default", 0.5))
    return float(rule.get("default", 0.0))


def _coerce_number(v: Any, rule: dict[str, Any], *, preserve_number: bool = False) -> float:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        x = float(v)
        if preserve_number:
            return x
        return _clamp01(x / 100.0 if x > 1 else x)
    return _match_rule(str(v), rule, preserve_number=preserve_number)


def _looks_structured(text: str) -> bool:
    return bool(re.search(r"(?m)^\s*[A-Za-z_][\w ]*:\s", text))


def _field_preserves_number(field: dict[str, Any]) -> bool:
    return str(field.get("normalization", "")).startswith("machine-native-")


def _normalize_field_value(value: float, field: dict[str, Any]) -> float:
    normalization = field.get("normalization")
    if normalization in {"binary", "one-hot", "machine-native-binary"}:
        return 1.0 if value >= 0.5 else 0.0
    if normalization in {"scalar-0-1", "enum-scalar"}:
        return _clamp01(value)
    if normalization in {"machine-native-ordinal", "machine-native-count"}:
        return float(round(value))
    if normalization == "machine-native-scalar":
        return value
    return _clamp01(value)


def _extract_field(response: Any, full_text: str, field: dict[str, Any]) -> float:
    ex = field["extract"]
    rule = ex.get("textFallback", {})
    preserve_number = _field_preserves_number(field)
    if isinstance(response, dict):
        v = _json_pointer(response, ex.get("jsonPointer", ""))
        if v is not None:
            return _normalize_field_value(_coerce_number(v, rule, preserve_number=preserve_number), field)
        return _normalize_field_value(
            _match_rule(json.dumps(response).lower(), rule, preserve_number=preserve_number), field)
    key = ex.get("responseKey") or field["semantic"]
    value_text = _structured_value(response, key)
    if value_text is not None:
        return _normalize_field_value(
            _match_rule(value_text, rule, preserve_number=preserve_number), field)
    # contracted key absent: in a structured turn that means "default", not a
    # whole-text scan (which would cross-contaminate from other fields' values).
    if _looks_structured(response):
        return _normalize_field_value(float(rule.get("default", 0.0)), field)
    return _normalize_field_value(
        _match_rule(full_text, rule, preserve_number=preserve_number), field)  # pure free-text turn: best-effort scan


def apply_response_mapping(response: Any, mapping: dict[str, Any]):
    """Return (targets, extracted_fields).

    targets: list of {sensorId, region, values, semantics} — one per PE region the
    response fans out to.  extracted_fields: flat list of {semantic, value} for
    logging/audit.  Fields with target=None (observe) contribute to audit only.
    """
    full_text = response if isinstance(response, str) else json.dumps(response)
    full_text = full_text.lower()
    targets: dict[tuple, dict[str, Any]] = {}
    extracted: list[dict[str, Any]] = []
    for field in mapping["fields"]:
        value = _extract_field(response, full_text, field)
        extracted.append({"semantic": field["semantic"], "value": value})
        target = field.get("target")
        if not target:
            continue
        region = target["region"]
        key = (target["sensorId"], region["offset"], region["length"])
        slot = targets.setdefault(key, {
            "sensorId": target["sensorId"], "region": dict(region),
            "values": [0.0] * region["length"], "semantics": [None] * region["length"],
            "normalizations": [None] * region["length"],
        })
        slot["values"][target["index"]] = value
        slot["semantics"][target["index"]] = field["semantic"]
        slot["normalizations"][target["index"]] = field.get("normalization")
    return list(targets.values()), extracted


# --- simulated agent turn -----------------------------------------------------

def _simulate_textual_response(envelope: dict[str, Any], agent_rec: dict[str, Any]) -> str:
    """Deterministic stand-in for an OpenClaw ACP turn: structured-keys text."""
    mode = envelope["dispatch"]["autonomyMode"]
    rag = envelope["governance"]["ragStatusCode"]
    label = envelope["outputVector"]["assertedLabel"]
    agent = agent_rec["agent"]
    blocked = rag in agent_rec["agentBinding"]["riskControls"]["blockedWhenRag"]
    seed = hashlib.sha256((envelope["correlationId"] + agent).encode()).hexdigest()
    confidence = ["low", "moderate", "high"][int(seed[:1], 16) % 3]

    if blocked:
        return "\n".join([
            "verdict: blocked",
            "completed: no",
            "failed: yes",
            f"confidence: {confidence}",
            "review_required: yes (escalate to governance; human review required)",
            f"summary: {label} is {rag}; direct action blocked and routed to governance.",
        ])
    verdict = {"observe": "observed", "advise": "advised",
               "supervised-act": "staged", "automated-act": "executed"}[mode]
    lines = [f"verdict: {verdict}", "completed: yes", "failed: no",
             f"confidence: {confidence}"]
    if mode == "supervised-act":
        lines.append("review_required: yes (human approval required before outreach)")
    if mode == "automated-act":
        lines += ["executed: yes", "rollback_ok: yes (reversible; rollback ready)"]
    lines.append(f"summary: {agent} {verdict} action for {label}.")
    return "\n".join(lines)


def _build_completion(envelope: dict[str, Any], agent_rec: dict[str, Any],
                      target: dict[str, Any]) -> dict[str, Any]:
    wb = agent_rec["agentBinding"]["writeBack"]
    return {  # localai-completion-writeback.schema.json
        "provider": "acp",
        "agent": agent_rec["agent"],
        "completionId": str(uuid4()),
        "correlationId": envelope["correlationId"],
        "envelopeId": envelope["envelopeId"],
        "sensorId": target["sensorId"],
        "name": wb.get("name", target["sensorId"]),
        "region": dict(target["region"]),
        "sourceMapping": wb.get("sourceMapping", {}),
        "values": [_round_value(v) for v in target["values"]],
        "ttlMs": wb["ttlMs"],
        "metadata": {
            "machineCode": envelope["ces"]["machineCode"],
            "sequenceId": envelope["ces"]["sequenceId"],
            "autonomyMode": envelope["dispatch"]["autonomyMode"],
            "semantics": target["semantics"],
            "normalizations": target.get("normalizations", []),
        },
        "triggerPush": wb["ingest"]["triggerPush"],
        "compactPush": wb["ingest"]["compactPush"],
    }


def run(envelope: dict[str, Any], agent_rec: dict[str, Any], cfg: dict[str, Any],
        logger: BehaviorLogger, live: bool = False) -> dict[str, Any]:
    oc = _as_object(cfg.get("openclaw"))
    mapping = agent_rec["responseMapping"]
    binding = agent_rec["agentBinding"]
    acp_run_id = str(uuid4())
    session_key = oc.get("sessionKey", "agent:main:main")

    logger.log("openclaw.session-spawn", acpRunId=acp_run_id, sessionKey=session_key,
               targetAgent=agent_rec["agent"], command=oc.get("command"),
               envelopeId=envelope["envelopeId"], trigger=binding["trigger"])

    response_text = _simulate_textual_response(envelope, agent_rec)
    targets, extracted = apply_response_mapping(response_text, mapping)
    logger.log("openclaw.agent-response", acpRunId=acp_run_id, agent=agent_rec["agent"],
               autonomyMode=envelope["dispatch"]["autonomyMode"],
               responseFormat=mapping["responseContract"], responseText=response_text)
    logger.log("openclaw.response-mapped", acpRunId=acp_run_id,
               extracted={f["semantic"]: f["value"] for f in extracted},
               targetRegions=len(targets))

    if not targets:
        logger.log("openclaw.completion-skipped", acpRunId=acp_run_id,
                   reason="observe-mode-no-writeback", agent=agent_rec["agent"])
        return {"acpRunId": acp_run_id, "responseText": response_text,
                "extracted": extracted, "completions": []}

    completions = []
    for target in targets:
        completion = _build_completion(envelope, agent_rec, target)
        logger.log("openclaw.completion-built", acpRunId=acp_run_id, agent=agent_rec["agent"],
                   sensorId=completion["sensorId"], region=completion["region"],
                   semantics=target["semantics"], values=completion["values"])
        posted = "dry-run"
        if live:
            posted = _post(cfg, completion, logger)
        logger.log("openclaw.completion-posted", acpRunId=acp_run_id,
                   completionId=completion["completionId"], postResult=posted,
                   region=completion["region"])
        completions.append(completion)
    return {"acpRunId": acp_run_id, "responseText": response_text,
            "extracted": extracted, "completions": completions}


def _post(cfg: dict[str, Any], completion: dict[str, Any], logger: BehaviorLogger) -> str:
    pe = _as_object(cfg.get("pe"))
    url = pe.get("baseUrl", "http://localhost:5300") + pe.get("completionsEndpoint", "/api/integrations/completions")
    body = json.dumps(completion).encode()
    req = request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=5) as resp:
            return f"{resp.status}"
    except error.URLError as exc:
        logger.log("openclaw.completion-post-failed", url=url, reason=str(exc.reason))
        return f"unreachable:{exc.reason}"
