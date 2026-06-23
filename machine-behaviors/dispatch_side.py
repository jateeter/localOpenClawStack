#!/usr/bin/env python3
"""RE/PE side of the integration: build the dispatch envelope and record it.

This mirrors RealityEngine_Machines/scripts/build-dispatch-envelope.py but sources
the agent/dispatch fields from the *derived* sidecar binding instead of an
in-corpus agentBinding, so no machine file is modified.  The envelope conforms to
schemas/ai-trigger-envelope.schema.json.

Debug logging is emitted on the dispatch side for every step:
derive -> envelope-built -> dispatch-accepted (the PE 202 no-wait contract).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import request, error
from uuid import uuid4

from behavior_log import BehaviorLogger, SIDE_DISPATCH

_STATUS_FROM_RAG = {"RED": "error", "AMBER": "warning", "GREEN": "info"}


def _as_object(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _governance(machine_meta: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    gov = _as_object(machine_meta.get("governance"))
    sla = _as_object(gov.get("sla"))
    status = output["processStatus"] or _STATUS_FROM_RAG.get(output["rag"], "info")
    return {
        "ragStatusCode": output["rag"],
        "processStatus": status,
        "ownerTeam": gov.get("ownerTeam"),
        "slaSeconds": sla.get(status),
        "runbook": gov.get("runbook"),
        "escalationPolicy": gov.get("escalationPolicy"),
        "contact": gov.get("contact"),
        "description": output.get("label"),
    }


def build_envelope(plan: dict[str, Any], machine_meta: dict[str, Any],
                   output: dict[str, Any], agent_rec: dict[str, Any],
                   cfg: dict[str, Any], correlation_id: str) -> dict[str, Any]:
    machine = plan["machine"]
    binding = agent_rec["agentBinding"]
    n = max(o["index"] for o in plan["outputs"]) + 1
    values = [1 if i == output["index"] else 0 for i in range(n)]
    pe = _as_object(cfg.get("pe"))
    return {
        "schemaVersion": "1.0.0",
        "envelopeType": "ces.terminal.event",
        "envelopeId": str(uuid4()),
        "correlationId": correlation_id,
        "emittedAt": datetime.now(timezone.utc).isoformat(),
        "source": {"engine": "RE", "instance": "local",
                   "endpoint": pe.get("baseUrl", "http://localhost:5300")},
        "ces": {
            "machineId": machine["id"],
            "machineName": machine["name"],
            "machineCode": machine["code"],
            "sequenceId": output["sequenceId"],
            "sequenceName": output["sequenceId"],
            "outputIndex": output["index"],
            "stepNumber": 0,
            "perceptualMapping": {"input": machine["inputRegion"], "output": machine["outputRegion"]},
            "provenance": [output["sequenceId"]],
            "deprecation": None,
        },
        "outputVector": {
            "values": values,
            "encoding": "one-hot",
            "semantics": [{"index": o["index"], "label": o["label"]} for o in plan["outputs"]],
            "assertedLabel": output["label"],
        },
        "projection": None,
        "governance": _governance(machine_meta, output),
        "dispatch": {
            "processId": machine["code"],
            "processName": machine["name"],
            "agent": binding["agent"],
            "action": binding["allowedActions"][0],
            "agentActionsCatalog": binding["allowedActions"],
            "trigger": binding["trigger"],
            "autonomyMode": output["autonomyMode"],
            "writeBack": binding["writeBack"] if binding["writeBack"].get("type") != "none" else None,
            "endpoint": {
                "kind": "graphql",
                "url": pe.get("graphqlEndpoint", "http://localhost:4000/graphql"),
                "mutation": "updateProcessState",
                "schemaRef": "localAIStack/services/api/routers/graphql_endpoint.py",
            },
        },
        "mqttContext": None,
    }


def dispatch(envelope: dict[str, Any], agent_rec: dict[str, Any], cfg: dict[str, Any],
             logger: BehaviorLogger, live: bool = False) -> dict[str, Any]:
    """Record the dispatch (PE accepted-no-wait contract). Returns a ledger record."""
    oc = _as_object(cfg.get("openclaw"))
    dispatch_id = str(uuid4())
    logger.log(
        "dispatch.envelope-built",
        envelopeId=envelope["envelopeId"],
        machineCode=envelope["ces"]["machineCode"],
        sequenceId=envelope["ces"]["sequenceId"],
        assertedLabel=envelope["outputVector"]["assertedLabel"],
        rag=envelope["governance"]["ragStatusCode"],
        agent=envelope["dispatch"]["agent"],
        autonomyMode=envelope["dispatch"]["autonomyMode"],
        realityVectorImpact=agent_rec.get("realityVectorImpact"),
    )
    record = {
        "dispatchId": dispatch_id,
        "provider": "acp",
        "platform": oc.get("platform", "OpenClaw"),
        "targetAgent": agent_rec["agent"],
        "gatewayUrl": oc.get("gatewayUrl"),
        "sessionKey": oc.get("sessionKey"),
        "dispatchMode": oc.get("dispatchMode", "accepted-no-wait"),
        "completionSourceMappingId": agent_rec["agentBinding"]["writeBack"].get(
            "sourceMapping", {}).get("id", oc.get("completionSourceMappingId")),
        "envelopeId": envelope["envelopeId"],
        "correlationId": envelope["correlationId"],
        "acceptedAt": datetime.now(timezone.utc).isoformat(),
        "status": "accepted-no-wait",
    }
    posted = "dry-run"
    if live:
        posted = _post(cfg, envelope, record, logger)
    logger.log("dispatch.accepted", dispatchId=dispatch_id, status=record["status"],
               postResult=posted, targetAgent=agent_rec["agent"])
    return record


def _post(cfg: dict[str, Any], envelope: dict[str, Any], record: dict[str, Any],
          logger: BehaviorLogger) -> str:
    pe = _as_object(cfg.get("pe"))
    url = pe.get("baseUrl", "http://localhost:5300") + pe.get("dispatchEndpoint", "/api/integrations/acp/dispatch")
    body = json.dumps({"dispatchId": record["dispatchId"], "envelope": envelope}).encode()
    req = request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with request.urlopen(req, timeout=5) as resp:
            return f"{resp.status}"
    except error.URLError as exc:  # PE not running in the prototype is expected
        logger.log("dispatch.post-failed", url=url, reason=str(exc.reason))
        return f"unreachable:{exc.reason}"


def load_machine_meta(plan: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    from derive_agents import _abs
    path = _abs(cfg["machinesDir"]) / f"{plan['machine']['id']}.json"
    data = json.loads(Path(path).read_text())
    return _as_object(_as_object(data.get("machine")).get("metadata"))
