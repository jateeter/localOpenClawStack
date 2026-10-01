#!/usr/bin/env python3
"""Tests for materialize_agents.py --check (RealityEngine_CI#467 item 2).

Run: MB_DEBUG=0 python3.13 tests/run_materialize_check_tests.py

Nothing detected a stale agent corpus: agents/ sat five weeks behind the corpus
while every check passed. --check derives in memory and compares with the
committed specs. These drive it against a copy of agents/, so the committed
tree is never touched.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("MB_DEBUG", "0")
HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import materialize_agents as ma  # noqa: E402

_PASS = _FAIL = 0
REPO = ROOT.parent
MANIFEST = REPO.parent / "RealityEngine_CI" / "config" / "regression-corpus.txt"


def check(name, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  ok   {name}")
    else:
        _FAIL += 1
        print(f"  FAIL {name}  {detail}")


def run(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    sys.argv = ["materialize_agents.py", *argv]
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            rc = ma.main()
        except SystemExit as exc:  # argparse errors
            rc = exc.code if isinstance(exc.code, int) else 1
    return rc, out.getvalue()


def main() -> int:
    if not MANIFEST.is_file():
        print(f"  skip regression manifest not found at {MANIFEST} (RealityEngine_CI not a sibling)")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        agents = Path(tmp) / "agents"
        shutil.copytree(ma.AGENTS_DIR, agents)
        ma.AGENTS_DIR = agents

        rc, out = run("--check", "--manifest", str(MANIFEST))
        check("committed regression agents are current", rc == 0, out[-400:])
        check("the manifest yields 15 agents", "checked 15 agent(s)" in out, out[:200])
        check("fixtures are reported as excluded by rule", "conformance fixtures" in out)
        check("localAIStack machines are reported as excluded by rule", "not in the machine corpus" in out)

        target = agents / "health-personal" / "home-transportation-barrier-monitor.oc-agent.json"
        original = target.read_text()
        target.write_text(original.replace("continue-monitoring", "dispatch-agent", 1))
        rc, out = run("--check", "--manifest", str(MANIFEST))
        check("a hand-edited action in a regression agent fails", rc == 1, out[-300:])
        check("and the stale spec is named", "home-transportation-barrier-monitor" in out)
        target.write_text(original)

        target.unlink()
        rc, out = run("--check", "--manifest", str(MANIFEST))
        check("a missing regression agent fails", rc == 1 and "missing" in out, out[-300:])
        target.write_text(original)

        rc, out = run("--manifest", str(MANIFEST))
        check("--manifest without --check is refused", rc == 2 and "requires --check" in out)
        rc, out = run("--check", "--fresh")
        check("--check --fresh is refused", rc == 2)

    print(f"\n{_PASS} passed, {_FAIL} failed")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
