#!/usr/bin/env python3
"""Synthetic action answer — an action agent can always complete.

An action agent is dispatched after a CES fires (`derive_agents.py`), performs
or recommends the action, and reports back on the shared completion lane:
`acp-openclaw-completion`, cells [4210:4214], `[completed, failed, confidence,
actionClass]`. When its real turn cannot report (the gateway is unreachable,
the model times out, or the answer does not resolve), the loop stays open.

The synthetic body answers in its place, on exactly that path. It is an action
answer in the action's own response contract, mapped by the action's own
`responseMapping` onto the same lane, and labelled synthetic. It never writes
anywhere a real action answer could not.

The admissible answers
    What an action agent may answer for its autonomy mode and the dispatch's
    RAG status, as the action contract defines it:

      blocked (RAG in the mode's blockedWhenRag)
          verdict blocked, completed no, failed yes
          (+ review_required yes at supervised-act / automated-act)
      otherwise
          verdict = the mode's own verdict (advise -> advised,
          supervised-act -> staged, automated-act -> executed),
          completed yes, failed no
          (+ review_required yes at supervised-act;
             executed yes, rollback_ok yes at automated-act)

    each at every confidence anchor (low, moderate, high). `observe` writes no
    vector, so it has no synthetic answer.

Ordinality and selection
    `ordinality` is the number of admissible answers (three, one per confidence
    anchor). With more than one, `strategy` chooses:

      seeded-random   SHA-256(agent | seedKey) mod ordinality. Reproducible on
                      every host, so every engine in parity receives the same
                      completion. seedKey is the dispatch's correlation id.
      round-robin     step mod ordinality, in the order above.

Mode — when the action answers synthetically
    off       never (default).
    fallback  only when the real turn produced no mappable answer, or failed.
    always    never call the model.

    Resolution, first match wins: OC_SYNTHETIC_MODE / OC_SYNTHETIC_STRATEGY;
    config `syntheticBody.domains.<domain>`; config `syntheticBody`.

A synthetic completion carries `metadata.synthetic: true` and the selection
that produced it (`completion_metadata`), so it is never read as agent
reasoning or counted as LLM drift.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from typing import Any

MODES = ("off", "fallback", "always")
STRATEGIES = ("seeded-random", "round-robin")
DEFAULT_MODE = "off"
DEFAULT_STRATEGY = "seeded-random"

# The action contract's verdict per autonomy mode, and its confidence anchors
# (derive_agents._VERDICT_CLASS / _CONFIDENCE_PHRASE map them to lane values).
VERDICT_BY_MODE = {"advise": "advised", "supervised-act": "staged", "automated-act": "executed"}
CONFIDENCE_ANCHORS = ("low", "moderate", "high")

LANE = {"sourceMappingId": "acp-openclaw-completion", "offset": 4210, "length": 4,
        "semantics": ["completed", "failed", "confidence", "actionClass"]}


def _as_object(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def blocked_when_rag(mode: str, risk_controls: dict[str, Any] | None = None) -> list[str]:
    """The RAG statuses that block this action: its own riskControls, else the
    contract's default for the mode (derive_agents._risk_controls)."""
    declared = _as_object(risk_controls).get("blockedWhenRag")
    if isinstance(declared, list):
        return [str(x) for x in declared]
    from derive_agents import _risk_controls
    return list(_risk_controls(mode)["blockedWhenRag"])


def admissible(mode: str, rag: str = "", risk_controls: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Every answer the action contract admits for this mode and RAG status."""
    if mode not in VERDICT_BY_MODE:
        return []   # observe writes no vector; an unknown mode admits nothing
    blocked = bool(rag) and rag in blocked_when_rag(mode, risk_controls)
    answers = []
    for confidence in CONFIDENCE_ANCHORS:
        if blocked:
            answer = {"verdict": "blocked", "completed": "no", "failed": "yes", "confidence": confidence}
            if mode in ("supervised-act", "automated-act"):
                answer["review_required"] = "yes"
        else:
            answer = {"verdict": VERDICT_BY_MODE[mode], "completed": "yes", "failed": "no",
                      "confidence": confidence}
            if mode == "supervised-act":
                answer["review_required"] = "yes"
            if mode == "automated-act":
                answer["executed"] = "yes"
                answer["rollback_ok"] = "yes"
        answers.append(answer)
    return answers


def action_mapping(mode: str) -> dict[str, Any]:
    """The action's own responseMapping onto the completion lane."""
    from derive_agents import _build_response_mapping, _response_field_spec
    region = {"offset": LANE["offset"], "length": LANE["length"]}
    return _build_response_mapping(mode, LANE["sourceMappingId"], region, _response_field_spec(mode))


def lane_values(answer: dict[str, Any], mapping: dict[str, Any]) -> list[float]:
    """The lane values a real action answer like this one is posted with.

    Through the same extractor as a model answer. A position the answer does
    not resolve (a blocked answer has no actionClass) is posted as 0.0, as the
    action path already posts it (openclaw_side._build_completion).
    """
    from openclaw_side import _round_value, apply_response_mapping
    targets, _extracted, _unresolved = apply_response_mapping(answer, mapping)
    if not targets:
        return []
    return [_round_value(v) for v in targets[0]["values"]]


def resolve(cfg: dict[str, Any] | None = None, domain: str = "",
            env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Effective {mode, strategy}; first match wins."""
    env = os.environ if env is None else env
    global_cfg = _as_object(_as_object(cfg).get("syntheticBody"))
    domain_cfg = _as_object(_as_object(global_cfg.get("domains")).get(domain))
    layers = [{"mode": env.get("OC_SYNTHETIC_MODE"), "strategy": env.get("OC_SYNTHETIC_STRATEGY")},
              domain_cfg, global_cfg]
    out: dict[str, str] = {}
    for key, allowed, default in (("mode", MODES, DEFAULT_MODE), ("strategy", STRATEGIES, DEFAULT_STRATEGY)):
        chosen = next((str(layer[key]) for layer in layers
                       if isinstance(layer.get(key), str) and layer.get(key)), default)
        if chosen not in allowed:
            raise ValueError(f"syntheticBody {key} {chosen!r} is not one of {', '.join(allowed)}")
        out[key] = chosen
    return out


def select(answers: list[dict[str, str]], *, agent: str, strategy: str,
           seed_key: str | None = None, step: int = 0) -> tuple[int, dict[str, Any]]:
    """Choose one admissible answer. Pure: no I/O, no clock, no global state."""
    if not answers:
        raise LookupError("no admissible synthetic answer (observe mode writes no vector)")
    n = len(answers)
    key = seed_key if seed_key not in (None, "") else str(step)
    if n == 1:
        index = 0
    elif strategy == "seeded-random":
        index = int(hashlib.sha256(f"{agent}|{key}".encode()).hexdigest()[:16], 16) % n
    elif strategy == "round-robin":
        index = step % n
    else:
        raise ValueError(f"unknown strategy {strategy!r}")
    return index, {
        "synthetic": True,
        "agent": agent,
        "strategy": strategy if n > 1 else "only-admissible",
        "ordinality": n,
        "answerIndex": index,
        "seedKey": key if strategy == "seeded-random" else None,
        "step": step,
        "source": "action-contract",
    }


def answer_text(answer: dict[str, str], agent: str, label: str = "") -> str:
    """The answer as structured-keys text, the action contract's turn form."""
    lines = [f"{k}: {answer[k]}" for k in ("verdict", "completed", "failed", "confidence",
                                            "review_required", "executed", "rollback_ok") if k in answer]
    lines.append("synthetic: yes")
    lines.append(f"summary: synthetic {answer['verdict']} answer from {agent}"
                 + (f" for {label}." if label else "."))
    return "\n".join(lines)


def synthesize(*, agent: str, mode: str, rag: str = "", risk_controls: dict[str, Any] | None = None,
               strategy: str = DEFAULT_STRATEGY, seed_key: str | None = None,
               step: int = 0) -> dict[str, Any]:
    """One synthetic action answer: {answer, values, provenance}."""
    answers = admissible(mode, rag, risk_controls)
    index, provenance = select(answers, agent=agent, strategy=strategy, seed_key=seed_key, step=step)
    answer = answers[index]
    provenance.update({"autonomyMode": mode, "ragStatusCode": rag or None,
                       "blocked": answer["verdict"] == "blocked"})
    return {"answer": answer, "values": lane_values(answer, action_mapping(mode)), "provenance": provenance}


def completion_metadata(provenance: dict[str, Any]) -> dict[str, Any]:
    """Fields to merge into a completion's `metadata` for a synthetic answer."""
    if not provenance.get("synthetic"):
        return {}
    return {"synthetic": True, "syntheticBody": {k: v for k, v in provenance.items() if k != "synthetic"}}
