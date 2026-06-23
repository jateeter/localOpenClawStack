#!/usr/bin/env python3
"""Structured, correlated debug logging for both sides of the integration.

The task requires "debug logging capability on both sides of the integration".
There are two sides:

  * dispatch side  (RE/PE):  derivation + envelope emission + dispatch record.
  * openclaw side  (agent):  session spawn + agent run + completion write-back.

Both sides write newline-delimited JSON (JSONL) so a single correlationId can be
grepped end-to-end across the boundary.  Logging is gated by MB_DEBUG:

    MB_DEBUG=0   silent (default for tests asserting silence)
    MB_DEBUG=1   write JSONL to the side's log file
    MB_DEBUG=2   also echo a compact line to stderr
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG_DIR = Path(os.environ.get("MB_LOG_DIR", Path(__file__).parent / "logs"))

# Stable side identifiers used in every record and in the file name.
SIDE_DISPATCH = "dispatch"   # RE / PE side
SIDE_OPENCLAW = "openclaw"   # agent side


def _level() -> int:
    try:
        return int(os.environ.get("MB_DEBUG", "1"))
    except ValueError:
        return 1


class BehaviorLogger:
    """One logger per side; correlationId threads records across both sides."""

    def __init__(self, side: str, correlation_id: str | None = None) -> None:
        self.side = side
        self.correlation_id = correlation_id
        self.path = LOG_DIR / f"behavior.{side}.jsonl"

    def bind(self, correlation_id: str) -> "BehaviorLogger":
        self.correlation_id = correlation_id
        return self

    def log(self, event: str, **fields: Any) -> dict[str, Any]:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "side": self.side,
            "correlationId": self.correlation_id,
            "event": event,
            **fields,
        }
        level = _level()
        if level >= 1:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as handle:
                handle.write(json.dumps(record) + "\n")
        if level >= 2:
            extra = " ".join(f"{k}={fields[k]}" for k in list(fields)[:4])
            sys.stderr.write(f"[{self.side}] {event} corr={self.correlation_id} {extra}\n")
        return record


def read_log(side: str) -> list[dict[str, Any]]:
    path = LOG_DIR / f"behavior.{side}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def clear_logs() -> None:
    for side in (SIDE_DISPATCH, SIDE_OPENCLAW):
        path = LOG_DIR / f"behavior.{side}.jsonl"
        if path.exists():
            path.unlink()
