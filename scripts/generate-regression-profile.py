#!/usr/bin/env python3
"""Derive the `regression` agent profile from the RealityEngine regression corpus.

The regression profile must name exactly the machine-behavior agents bound to the
machines the Perception Engines load during a regression run. Those machines are
listed in RealityEngine_CI/config/standard-deployment-corpus.txt, so the profile
is generated from that manifest rather than maintained by hand — a hand-kept list
drifts silently, and a profile that disagrees with the corpus is precisely the
failure this profile exists to prevent.

    ./scripts/generate-regression-profile.py            # write the profile
    ./scripts/generate-regression-profile.py --check    # fail if it is stale

--check is the drift guard. Run it in CI and after any corpus change.

The sibling repositories are only needed to generate or check the profile. The
committed profile is what start.sh consumes, so the stack still starts on a host
that has no RealityEngine_CI checkout.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = ROOT.parent

DEFAULT_MANIFEST = WORKSPACE / "RealityEngine_CI" / "config" / "standard-deployment-corpus.txt"
DEFAULT_MACHINES_ROOT = WORKSPACE / "RealityEngine_Machines" / "machines"
DEFAULT_INDEX = ROOT / "machine-behaviors" / "agents" / "INDEX.json"
DEFAULT_OUT = ROOT / "machine-behaviors" / "agents" / "profiles" / "regression.txt"


def norm(value: str | None) -> str:
    """Fold a machine or agent name to a comparison key.

    The manifest addresses machines by file, the corpus by display name, and the
    agent index by `machineName`. Case, spaces, and hyphens differ across all
    three ("DC Memory Alert Flip-Flop" vs "dc-memory-alert-flip-flop"), so the
    join key drops everything that is not alphanumeric.
    """
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def read_manifest(path: Path) -> list[str]:
    entries: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            entries.append(line)
    return entries


def resolve_machine(machines_root: Path, entry: str) -> Path:
    """Mirror materialize-machine-corpus.sh: relative path first, then basename.

    Manifest entries stay valid across corpus reorganisations because a
    basename-only entry is resolved by recursive search. Ambiguity is an error
    rather than a silent first-match, since corpus filenames are required to be
    globally unique.
    """
    direct = machines_root / entry
    if direct.is_file():
        return direct
    matches = sorted(machines_root.rglob(Path(entry).name))
    if not matches:
        raise LookupError(f"machine not found under {machines_root}: {entry}")
    if len(matches) > 1:
        joined = " ".join(str(m.relative_to(machines_root)) for m in matches)
        raise LookupError(f"ambiguous machine entry (filenames must be unique): {entry} -> {joined}")
    return matches[0]


def machine_name(path: Path) -> str:
    document = json.loads(path.read_text(encoding="utf-8"))
    machine = document.get("machine") or document
    name = machine.get("name")
    if not name:
        raise LookupError(f"machine has no name: {path}")
    return name


def build(manifest: Path, machines_root: Path, index: Path) -> str:
    agents = json.loads(index.read_text(encoding="utf-8"))["agents"]
    by_name: dict[str, list[dict]] = {}
    for agent in agents:
        by_name.setdefault(norm(agent["machineName"]), []).append(agent)

    entries = read_manifest(manifest)
    if not entries:
        raise LookupError(f"manifest selected no machines: {manifest}")

    rows: list[tuple[str, str]] = []
    problems: list[str] = []
    for entry in entries:
        try:
            name = machine_name(resolve_machine(machines_root, entry))
        except LookupError as exc:
            problems.append(str(exc))
            continue
        candidates = by_name.get(norm(name), [])
        if not candidates:
            problems.append(f"no agent in {index.name} for machine {name!r} ({entry})")
        elif len(candidates) > 1:
            ids = " ".join(c["agentId"] for c in candidates)
            problems.append(f"machine {name!r} matches multiple agents: {ids}")
        else:
            rows.append((candidates[0]["agentId"], entry))

    # Report every unresolved entry at once. Aborting on the first turns a corpus
    # reorganisation into a one-at-a-time debugging session.
    if problems:
        raise LookupError("\n".join(f"  {p}" for p in problems))

    width = max(len(agent_id) for agent_id, _ in rows)
    lines = [
        "# Machine-behavior agents for the RealityEngine regression corpus.",
        "#",
        "# GENERATED — do not edit by hand.",
        f"#   source:     RealityEngine_CI/config/{manifest.name} ({len(rows)} machines)",
        "#   regenerate: ./scripts/generate-regression-profile.py",
        "#   verify:     ./scripts/generate-regression-profile.py --check",
        "#",
        "# One agentId per line, in corpus order; the trailing comment is the machine",
        "# it is bound to.",
        "",
    ]
    lines += [f"{agent_id:<{width}}  # {entry}" for agent_id, entry in rows]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="fail if the committed profile is stale")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--machines-root", type=Path, default=DEFAULT_MACHINES_ROOT)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    for label, path in (("manifest", args.manifest), ("machines root", args.machines_root), ("agent index", args.index)):
        if not path.exists():
            print(f"[profile] {label} not found: {path}", file=sys.stderr)
            print("[profile] set --manifest/--machines-root/--index if the sibling repos live elsewhere", file=sys.stderr)
            return 1

    try:
        content = build(args.manifest, args.machines_root, args.index)
    except LookupError as exc:
        print("[profile] cannot derive the regression profile:", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1

    count = sum(1 for line in content.splitlines() if line and not line.startswith("#"))

    if args.check:
        if not args.out.exists():
            print(f"[profile] missing {args.out} — run ./scripts/generate-regression-profile.py", file=sys.stderr)
            return 1
        if args.out.read_text(encoding="utf-8") != content:
            print(f"[profile] {args.out.name} is stale relative to {args.manifest.name}.", file=sys.stderr)
            print("[profile] the agent profile and the machine corpus have drifted.", file=sys.stderr)
            print("[profile] run ./scripts/generate-regression-profile.py and commit the result.", file=sys.stderr)
            return 1
        print(f"[profile] regression profile is current ({count} agents)")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(content, encoding="utf-8")
    print(f"[profile] wrote {args.out.relative_to(ROOT)} ({count} agents)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
