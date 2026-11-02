#!/usr/bin/env python3
"""Synthetic agent body — every generated agent can always answer.

An agent's real turn can fail to close the loop: the gateway is unreachable,
the model times out, or its answer does not resolve through the agent's
`responseMapping`. With the synthetic body enabled, the agent answers anyway,
with a response that is admissible for its machine and that says it is
synthetic.

Where the admissible responses come from
    The machine's own corpus `inputSequences` — the stimulus fixtures the
    corpus declares for it and the parity loop replays. Every vector there
    drives one of the machine's CES, so a synthetic answer is never an
    invented input. The body does not re-derive CES matching rules: the
    corpus is the oracle, not an engine (no reference implementation).

    Each distinct vector is one admissible response. Its `values` are what the
    PE actually receives: the vector passed through the agent's own
    `responseMapping` (the same extractor a model answer goes through). Where
    that normalization changes a corpus value, the response is kept but marked
    `normalizationShift` so the difference is visible, never hidden.

Ordinality and selection
    `ordinality` is the number of admissible responses. With one, the answer is
    that response. With more than one, the agent's `strategy` chooses:

      seeded-random   SHA-256(agentId | seedKey) mod ordinality. Reproducible:
                      the same agent and seed key give the same answer on every
                      host and for every engine, so engines in parity receive
                      the same value (it is computed once, then fanned out).
      round-robin     the first vector of each input sequence in turn, so
                      successive turns cover every scenario the corpus declares
                      for the machine: each CES, and the corpus's own fixtures
                      that fire none (e.g. "no assertion") or several.
      sequence-walk   every vector of every input sequence in corpus order, so
                      successive turns walk each sequence step by step.

    The algorithmic strategies advance by `step` (the caller's turn counter);
    seeded-random keys on `seedKey` (default: correlation id, else step).

Mode — when the agent answers synthetically
    off       never (default). The agent answers only from its model.
    fallback  only when the real turn produced no mappable answer.
    always    never call the model; answer synthetically.

    Resolution, first match wins: OC_SYNTHETIC_MODE / OC_SYNTHETIC_STRATEGY
    environment; config `syntheticBody.domains.<domain>`; config
    `syntheticBody` (global); the agent's own `syntheticBody`.

Every synthetic completion carries `metadata.synthetic: true` and the
selection that produced it, so it can never be mistaken for agent reasoning or
counted as LLM drift.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from typing import Any

SCHEMA_VERSION = "1.0.0"
MODES = ("off", "fallback", "always")
STRATEGIES = ("seeded-random", "round-robin", "sequence-walk")
DEFAULT_MODE = "off"
DEFAULT_STRATEGY = "seeded-random"
VALUE_DIGITS = 4


def _as_object(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _as_list(v: Any) -> list[Any]:
    return v if isinstance(v, list) else []


def _num(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return float(x)


# --- declaration (generation time) -------------------------------------------

def _mapped(vector: list[float], mapping: dict[str, Any]) -> tuple[list[float] | None, list[str]]:
    """The values the PE receives for `vector`, through the agent's own mapping.

    Returns (values, problems). values is None when the mapping does not
    resolve every input position — such a vector cannot be a complete answer.
    """
    from openclaw_side import apply_response_mapping

    fields = _as_list(mapping.get("fields"))
    response: dict[str, Any] = {}
    problems: list[str] = []
    for field in fields:
        index = _as_object(field.get("target")).get("index")
        pointer = str(_as_object(field.get("extract")).get("jsonPointer", ""))
        if not isinstance(index, int) or not pointer.startswith("/") or "/" in pointer[1:]:
            problems.append(f"field {field.get('semantic')!r} has no single-key jsonPointer and index")
            continue
        if index >= len(vector):
            problems.append(f"field {field.get('semantic')!r} index {index} is outside the vector")
            continue
        response[pointer[1:]] = vector[index]
    if problems:
        return None, problems
    targets, _extracted, unresolved = apply_response_mapping(response, mapping)
    if unresolved:
        return None, [f"unresolved: {u['semantic']}" for u in unresolved]
    if len(targets) != 1:
        return None, [f"mapping fans out to {len(targets)} targets, expected 1"]
    target = targets[0]
    if not all(target["present"]):
        return None, ["mapping leaves input positions unwritten"]
    return [round(float(v), VALUE_DIGITS) for v in target["values"]], []


def declare(machine: dict[str, Any], response_mapping: dict[str, Any],
            agent_id: str) -> dict[str, Any]:
    """The `syntheticBody` block written into a generated agent spec."""
    length = int(_as_object(_as_object(machine.get("perceptualMapping")).get("input")).get("length", 0) or 0)
    responses: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen: dict[tuple, str] = {}
    sequence_starts: list[str] = []
    for s_index, seq in enumerate(_as_list(machine.get("inputSequences"))):
        seq = _as_object(seq)
        seq_name = str(seq.get("name") or f"inputSequence-{s_index}")
        expected = str(_as_object(seq.get("metadata")).get("expectedSequenceId") or "")
        for step, raw in enumerate(_as_list(seq.get("events"))):
            parsed = [_num(x) for x in _as_list(raw)]
            rid = f"{seq_name}#{step}"
            vector = [v for v in parsed if v is not None]
            if len(parsed) != length or len(vector) != length:
                excluded.append({"id": rid, "reason": f"not a numeric vector of length {length}"})
                continue
            if any(v < 0.0 or v > 1.0 for v in vector):
                excluded.append({"id": rid, "reason": "values outside [0, 1]; an analyst answer is normalized to [0, 1]"})
                continue
            key = tuple(vector)
            if key in seen:
                if step == 0:
                    sequence_starts.append(seen[key])
                continue
            values, problems = _mapped(vector, response_mapping)
            if values is None:
                excluded.append({"id": rid, "reason": "; ".join(problems)})
                continue
            entry = {"id": rid, "inputSequence": seq_name, "step": step,
                     "expectedSequenceId": expected or None, "values": values}
            if values != [round(v, VALUE_DIGITS) for v in vector]:
                entry["normalizationShift"] = {"corpus": [round(v, VALUE_DIGITS) for v in vector]}
            seen[key] = rid
            responses.append(entry)
            if step == 0:
                sequence_starts.append(rid)
    body = {
        "schemaVersion": SCHEMA_VERSION,
        "mode": DEFAULT_MODE,
        "strategy": DEFAULT_STRATEGY,
        "strategies": list(STRATEGIES),
        "source": "corpus-inputSequences",
        "seedBasis": "sha256(agentId|seedKey); seedKey = correlationId, else step",
        "ordinality": len(responses),
        "responses": responses,
        "roundRobin": [r for r in dict.fromkeys(sequence_starts) if any(x["id"] == r for x in responses)],
    }
    if excluded:
        body["excluded"] = excluded
    if not responses:
        body["unavailable"] = "no admissible response: the corpus declares no usable input vector for this machine"
    return body


# --- resolution and selection (turn time) ------------------------------------

def resolve(agent: dict[str, Any], cfg: dict[str, Any] | None = None,
            env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Effective {mode, strategy} for one agent; first match wins."""
    env = os.environ if env is None else env
    cfg = _as_object(cfg)
    body = _as_object(agent.get("syntheticBody"))
    domain = str(_as_object(agent.get("machine")).get("domain") or "")
    global_cfg = _as_object(cfg.get("syntheticBody"))
    domain_cfg = _as_object(_as_object(global_cfg.get("domains")).get(domain))
    layers = [
        {"mode": env.get("OC_SYNTHETIC_MODE"), "strategy": env.get("OC_SYNTHETIC_STRATEGY")},
        domain_cfg, global_cfg, body,
    ]
    out: dict[str, str] = {}
    for key, allowed, default in (("mode", MODES, DEFAULT_MODE),
                                  ("strategy", STRATEGIES, DEFAULT_STRATEGY)):
        chosen = next((str(layer[key]) for layer in layers
                       if isinstance(layer.get(key), str) and layer.get(key)), default)
        if chosen not in allowed:
            raise ValueError(f"syntheticBody {key} {chosen!r} is not one of {', '.join(allowed)}")
        out[key] = chosen
    return out


def _index(n: int, strategy: str, agent_id: str, seed_key: str, step: int,
           round_robin: list[int]) -> int:
    if n == 1:
        return 0
    if strategy == "seeded-random":
        digest = hashlib.sha256(f"{agent_id}|{seed_key}".encode()).hexdigest()
        return int(digest[:16], 16) % n
    if strategy == "round-robin":
        cycle = round_robin or list(range(n))
        return cycle[step % len(cycle)]
    if strategy == "sequence-walk":
        return step % n
    raise ValueError(f"unknown strategy {strategy!r}")


def select(agent: dict[str, Any], *, strategy: str, step: int = 0,
           seed_key: str | None = None) -> dict[str, Any]:
    """Choose one admissible response. Pure: no I/O, no clock, no global state."""
    body = _as_object(agent.get("syntheticBody"))
    responses = _as_list(body.get("responses"))
    if not responses:
        raise LookupError(body.get("unavailable") or "agent declares no synthetic responses")
    agent_id = str(agent.get("agentId") or "")
    key = seed_key if seed_key not in (None, "") else str(step)
    ids = [r["id"] for r in responses]
    rr = [ids.index(r) for r in _as_list(body.get("roundRobin")) if r in ids]
    i = _index(len(responses), strategy, agent_id, key, step, rr)
    chosen = responses[i]
    return {
        "values": list(chosen["values"]),
        "provenance": {
            "synthetic": True,
            "agentId": agent_id,
            "strategy": strategy if len(responses) > 1 else "only-admissible",
            "ordinality": len(responses),
            "responseId": chosen["id"],
            "expectedSequenceId": chosen.get("expectedSequenceId"),
            "seedKey": key if strategy == "seeded-random" else None,
            "step": step,
            "source": body.get("source"),
            **({"normalizationShift": chosen["normalizationShift"]}
               if "normalizationShift" in chosen else {}),
        },
    }


def response_object(agent: dict[str, Any], values: list[float]) -> dict[str, Any]:
    """The synthetic answer as the JSON object a model would return.

    Keyed by each field's own jsonPointer, so it goes through the agent's
    `responseMapping` exactly as a model's structured answer does.
    """
    out: dict[str, Any] = {}
    for field in _as_list(_as_object(agent.get("responseMapping")).get("fields")):
        pointer = str(_as_object(field.get("extract")).get("jsonPointer", ""))
        index = _as_object(field.get("target")).get("index")
        if pointer.startswith("/") and isinstance(index, int) and index < len(values):
            out[pointer[1:]] = values[index]
    out["synthetic"] = True
    return out


def turn(agent: dict[str, Any], *, cfg: dict[str, Any] | None = None,
         model_turn=None, step: int = 0, seed_key: str | None = None,
         env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """One analyst turn with the synthetic body in force.

    `model_turn` is a callable returning the model's answer (text or object),
    or raising when the model cannot be reached. Returns
    {source: model|synthetic|none, response, targets, unresolved, provenance}.
    """
    from openclaw_side import apply_response_mapping

    effective = resolve(agent, cfg, env)
    mapping = _as_object(agent.get("responseMapping"))
    failure = None
    if effective["mode"] != "always" and model_turn is not None:
        try:
            response = model_turn(agent)
            targets, _extracted, unresolved = apply_response_mapping(response, mapping)
            if targets and not unresolved:
                return {"source": "model", "response": response, "targets": targets,
                        "unresolved": [], "provenance": {"synthetic": False}}
            failure = f"model answer did not resolve ({len(unresolved)} unresolved position(s))"
            if effective["mode"] == "off":
                return {"source": "model", "response": response, "targets": targets,
                        "unresolved": unresolved, "provenance": {"synthetic": False}}
        except Exception as exc:  # unreachable, timeout, refused
            failure = f"model turn failed: {exc}"
            if effective["mode"] == "off":
                return {"source": "none", "response": None, "targets": [], "unresolved": [],
                        "provenance": {"synthetic": False, "failure": failure}}
    elif effective["mode"] == "off":
        return {"source": "none", "response": None, "targets": [], "unresolved": [],
                "provenance": {"synthetic": False, "failure": "no model turn and synthetic body off"}}

    chosen = select(agent, strategy=effective["strategy"], step=step, seed_key=seed_key)
    response = response_object(agent, chosen["values"])
    targets, _extracted, unresolved = apply_response_mapping(response, mapping)
    provenance = {**chosen["provenance"], "mode": effective["mode"]}
    if failure:
        provenance["replaced"] = failure
    for target in targets:
        target["synthetic"] = True
    return {"source": "synthetic", "response": response, "targets": targets,
            "unresolved": unresolved, "provenance": provenance}


def completion_metadata(provenance: dict[str, Any]) -> dict[str, Any]:
    """Fields to merge into a completion's `metadata` for a synthetic answer."""
    if not provenance.get("synthetic"):
        return {}
    return {"synthetic": True, "syntheticBody": {k: v for k, v in provenance.items() if k != "synthetic"}}


if __name__ == "__main__":  # pragma: no cover - manual inspection helper
    import sys

    spec = json.load(open(sys.argv[1]))
    strategy = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_STRATEGY
    for k in range(int(sys.argv[3]) if len(sys.argv) > 3 else 4):
        print(json.dumps(select(spec, strategy=strategy, step=k, seed_key=f"demo-{k}")))
