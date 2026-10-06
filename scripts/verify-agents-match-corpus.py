#!/usr/bin/env python3
"""OpenClaw's agents must be the agents of the machine corpus the engines loaded.

Owner rule (2026-10-06): if the Reality Engines load the regression corpus,
OpenClaw loads the regression agents; full corpus, full agents. This checks the
deployment against the running universe rather than against a fixed index.

    verify-agents-match-corpus.py [--deployed-index PATH] [--registry-file PATH]

1. The running universe is read from the instance registry (RE_REGISTRY_FILE,
   default /tmp/re-registry/re-registry.json; or RE_REGISTRY_URL).
2. Every listed engine is asked what it loaded: GET {re_url}/api/machines/json/list
   (`machines[].relFile`). All engines must report the same corpus. No engine is
   the reference: a disagreement is reported with every party's count.
3. That corpus, as a manifest, resolves through agent-profile.sh manifest:<file>
   to the agents OpenClaw should hold (fixtures and localAIStack machines carry
   no agent by rule).
4. The deployed index (what start.sh loaded, INDEX.deployed.json) must name
   exactly those agents.

No instance registry, or one whose engines all fail to answer, means no running
universe: there is nothing to follow, and that is reported, not failed. Some
engines answering and others not is a failure.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEPLOYED = ROOT / "machine-behaviors" / "agents" / ".generated" / "INDEX.deployed.json"
DEFAULT_REGISTRY_FILE = Path(os.environ.get("RE_REGISTRY_FILE", "/tmp/re-registry/re-registry.json"))
INSECURE = ssl.create_default_context()
INSECURE.check_hostname = False
INSECURE.verify_mode = ssl.CERT_NONE


def ok(msg: str) -> None:
    print(f"[ok]   {msg}")


def fail(msg: str) -> int:
    print(f"[fail] {msg}", file=sys.stderr)
    return 1


def get_json(url: str, timeout: float = 10.0):
    with urllib.request.urlopen(url, timeout=timeout, context=INSECURE) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_registry(registry_file: Path) -> dict | None:
    url = os.environ.get("RE_REGISTRY_URL", "")
    if registry_file.is_file():
        try:
            return json.loads(registry_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    if url:
        try:
            return get_json(url)
        except Exception:  # noqa: BLE001 - unreachable means no universe here
            return None
    return None


def agent_ids(index_path: Path) -> set[str]:
    return {a["agentId"] for a in json.loads(index_path.read_text(encoding="utf-8")).get("agents", [])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--deployed-index", type=Path,
                        default=Path(os.environ.get("OPENCLAW_AGENT_INDEX_PATH") or DEFAULT_DEPLOYED))
    parser.add_argument("--registry-file", type=Path, default=DEFAULT_REGISTRY_FILE)
    args = parser.parse_args()

    if not args.deployed_index.is_file():
        return fail(f"no deployed agent index at {args.deployed_index}; start the stack with scripts/start.sh")
    deployed = json.loads(args.deployed_index.read_text(encoding="utf-8"))
    deployed_ids = agent_ids(args.deployed_index)
    deployed_label = deployed.get("profile") or "full"

    registry = load_registry(args.registry_file)
    instances = (registry or {}).get("instances") or []
    if not instances:
        ok(f"no running universe in the instance registry; agents checked against the deployed profile only "
           f"({deployed_label}, {len(deployed_ids)} agents)")
        return 0

    corpora: dict[str, set[str]] = {}
    unreachable: list[str] = []
    for inst in instances:
        iid, re_url = inst.get("id", "?"), (inst.get("re_url") or "").rstrip("/")
        try:
            body = get_json(f"{re_url}/api/machines/json/list")
        except Exception as exc:  # noqa: BLE001
            unreachable.append(f"{iid} ({re_url}: {exc.__class__.__name__})")
            continue
        corpora[iid] = {str(r.get("relFile") or r.get("filename")) for r in body.get("machines") or []}

    if not corpora:
        ok(f"instance registry lists {len(instances)} instance(s) but none answer: no running universe; "
           f"agents checked against the deployed profile only ({deployed_label})")
        return 0
    if unreachable:
        return fail("cannot read the deployed corpus: instance(s) listed in the instance registry did not answer: "
                    + ", ".join(unreachable))

    distinct = {frozenset(v) for v in corpora.values()}
    if len(distinct) > 1:
        parts = ", ".join(f"{iid}={len(v)}" for iid, v in sorted(corpora.items()))
        union = set().union(*corpora.values())
        split = sorted(f for f in union if any(f not in v for v in corpora.values()))
        return fail(f"the engines disagree on the deployed corpus ({parts}); not held by every engine: "
                    + " ".join(split[:10]) + (f" … +{len(split) - 10}" if len(split) > 10 else ""))
    corpus = sorted(next(iter(distinct)))
    engines = ", ".join(sorted(corpora))

    with tempfile.NamedTemporaryFile("w", suffix=".txt", prefix="deployed-corpus-", delete=False) as fh:
        fh.write("\n".join(corpus) + "\n")
        manifest = fh.name
    try:
        proc = subprocess.run([str(ROOT / "scripts" / "agent-profile.sh"), f"manifest:{manifest}"],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            return fail(f"cannot derive agents from the corpus the engines loaded ({len(corpus)} files):\n"
                        + proc.stderr.strip())
        expected_ids = agent_ids(Path(proc.stdout.strip()))
    finally:
        os.unlink(manifest)

    missing = sorted(expected_ids - deployed_ids)
    extra = sorted(deployed_ids - expected_ids)
    if missing or extra:
        detail = []
        if missing:
            detail.append(f"missing {len(missing)}: " + " ".join(missing[:10]) + (" …" if len(missing) > 10 else ""))
        if extra:
            detail.append(f"not in the deployed corpus {len(extra)}: " + " ".join(extra[:10]) + (" …" if len(extra) > 10 else ""))
        return fail(f"OpenClaw agents do not follow the deployed corpus: the engines ({engines}) loaded "
                    f"{len(corpus)} corpus files -> {len(expected_ids)} agents; OpenClaw holds {len(deployed_ids)} "
                    f"({deployed_label}). " + "; ".join(detail)
                    + ". Restart OpenClaw with the universe's agent profile (startUniverse.sh does this).")
    ok(f"OpenClaw agents follow the deployed corpus: {len(expected_ids)} agents for the {len(corpus)} corpus files "
       f"loaded by {engines} ({deployed_label})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
