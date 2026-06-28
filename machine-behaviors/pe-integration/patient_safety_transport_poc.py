#!/usr/bin/env python3
"""Dry-run the patient-safety transport OpenClaw -> PE -> localAIStack handoff.

The runner intentionally performs no network I/O by default. It consumes the
declarative transform beside this file, applies each referenced OpenClaw
agent's responseMapping, emits the PE completion payloads that would be posted,
and builds the downstream localAIStack/Ollama resolver handoff back into PE.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openclaw_side import apply_response_mapping  # noqa: E402


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def round_values(values: list[float]) -> list[float]:
    return [round(float(v), 4) for v in values]


def build_agent_completion(transform: dict, agent_cfg: dict) -> dict:
    agent_path = (Path(__file__).resolve().parent / agent_cfg["agentFile"]).resolve()
    agent = load_json(agent_path)
    targets, extracted = apply_response_mapping(
        agent_cfg["proofResponseText"], agent["responseMapping"])
    if len(targets) != 1:
        raise ValueError(f"{agent_cfg['agentId']} produced {len(targets)} target regions")
    target = targets[0]
    expected = agent_cfg["expectedCompletionValues"]
    actual = round_values(target["values"])
    if actual != round_values(expected):
        raise ValueError(
            f"{agent_cfg['agentId']} values {actual} did not match expected {expected}")

    wb = agent["agentBinding"]["writeBack"]
    return {
        "provider": wb["provider"],
        "agent": agent_cfg["agentId"],
        "completionId": str(uuid4()),
        "correlationId": transform["transformId"],
        "envelopeId": f"poc-{agent_cfg['agentId']}",
        "sensorId": target["sensorId"],
        "name": wb["name"],
        "region": target["region"],
        "sourceMapping": wb["sourceMapping"],
        "values": actual,
        "ttlMs": wb["ttlMs"],
        "metadata": {
            "proof": "patient-safety-transport-openclaw",
            "responseText": agent_cfg["proofResponseText"],
            "extracted": extracted,
            "semantics": target["semantics"],
            "normalizations": target.get("normalizations", []),
        },
        "triggerPush": wb["ingest"]["triggerPush"],
        "compactPush": wb["ingest"]["compactPush"],
    }


def build_graphql_trigger(transform: dict) -> dict:
    handoff = transform["finalResolverHandoff"]["trigger"]
    composition = transform["peComposition"]
    return {
        "url": transform["providers"]["localAIStack"]["baseUrl"] + handoff["endpoint"],
        "operation": handoff["operation"],
        "variables": {
            "input": {
                "id": handoff["processId"],
                "name": handoff["processName"],
                "status": handoff["status"],
                "ragStatusCode": handoff["ragStatusCode"],
                "sourceMachine": composition["targetMachine"],
                "sourceSequence": composition["publishedSequenceId"],
                "context": json.dumps({
                    "busId": composition["busId"],
                    "busTag": composition["busTag"],
                    "inputRegion": composition["inputRegion"],
                    "inputValues": composition["inputValues"],
                    "publishedOutputRegion": composition["publishedOutputRegion"],
                    "publishedOutputValues": composition["publishedOutputValues"],
                }, separators=(",", ":")),
            }
        },
    }


def build_chat_request(transform: dict) -> dict:
    resolver = transform["finalResolverHandoff"]["resolver"]
    composition = transform["peComposition"]
    return {
        "url": transform["providers"]["localAIStack"]["baseUrl"] + resolver["endpoint"],
        "body": {
            "model": resolver["model"],
            "temperature": 0.2,
            "stream": False,
            "health_context": False,
            "messages": [
                {"role": "system", "content": resolver["systemPrompt"]},
                {"role": "user", "content": json.dumps({
                    "event": composition["publishedSequenceId"],
                    "busId": composition["busId"],
                    "busInput": composition["inputValues"],
                    "busOutput": composition["publishedOutputValues"],
                    "requiredOutcome": "Return a care-coordination completion for PE ingestion."
                }, separators=(",", ":"))},
            ],
        },
    }


def build_resolution_completion(transform: dict) -> dict:
    wb = transform["finalResolverHandoff"]["peWriteBack"]
    return {
        "provider": wb["provider"],
        "agent": "localai-ollama-patient-safety-transport-resolver",
        "completionId": str(uuid4()),
        "correlationId": transform["transformId"],
        "envelopeId": "poc-localai-ollama-resolution",
        "sensorId": wb["sensorId"],
        "name": wb["name"],
        "region": wb["region"],
        "sourceMapping": wb["sourceMapping"],
        "values": wb["values"],
        "ttlMs": wb["ttlMs"],
        "metadata": {
            "proof": "patient-safety-transport-localai-ollama",
            "normalization": wb["normalization"],
            "semantics": wb["semantics"],
            "resolvedAt": datetime.now(timezone.utc).isoformat(),
        },
        "triggerPush": False,
        "compactPush": True,
    }


def run(config_path: Path) -> dict:
    transform = load_json(config_path)
    completions = [build_agent_completion(transform, a) for a in transform["agents"]]
    return {
        "schemaVersion": "1.0.0",
        "transformId": transform["transformId"],
        "mode": transform["dispatch"]["mode"],
        "openclawCompletions": completions,
        "deterministicMachineOutputs": transform["deterministicMachineOutputs"],
        "peComposition": transform["peComposition"],
        "localAIStackGraphQLTrigger": build_graphql_trigger(transform),
        "localAIStackChatRequest": build_chat_request(transform),
        "peResolutionCompletion": build_resolution_completion(transform),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default=str(Path(__file__).with_name("patient_safety_transport_openclaw_transform.json")),
        help="Path to the patient-safety OpenClaw transform config.",
    )
    args = parser.parse_args()
    print(json.dumps(run(Path(args.config)), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
