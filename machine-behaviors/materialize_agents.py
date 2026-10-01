#!/usr/bin/env python3
"""Materialize the full OC-Agent-Template corpus, structured by domain.

Derives one input-analyst agent spec per machine (oc_agent_template.derive) and
writes it to agents/<domain>/<code>.oc-agent.json — one agent per machine, named
after the machine. Also emits agents/INDEX.json (machine→agent→domain→path) and
agents/INDEX.md (per-domain counts).

Idempotent and regenerable: the deriver is deterministic, so re-running rewrites
the same content. Pass --fresh to clear agents/ first (keeps schema/template dirs
in templates/, which live elsewhere).

Usage:
    python3 materialize_agents.py             # write the whole corpus
    python3 materialize_agents.py --fresh     # wipe agents/ first, then write
    python3 materialize_agents.py --domain health-personal   # one domain only
    python3 materialize_agents.py --check --manifest ../../RealityEngine_CI/config/regression-corpus.txt
                                              # fail if any agent the manifest's
                                              # machines derive differs from disk

--check writes nothing. It derives in memory and compares byte for byte with the
committed specs. With --manifest it covers only the machines that manifest lists
(the regression corpus: RealityEngine_CI#467 item 2) and leaves the INDEX files
alone, since they describe the whole corpus. Without it, it covers the whole
corpus including INDEX.json/INDEX.md and also reports committed specs that the
corpus no longer derives.

Nothing used to detect a stale agent corpus. `agents/` sat five weeks behind
RealityEngine_Machines#146/#154 while every check passed, and the analyst kept
receiving the pre-#154 actions (CSX030 said `dispatch-agent` where the corpus
said `urgent-intervention`).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

import oc_agent_template as tmpl
from derive_agents import as_object, load_config, _abs, primary_domain

HERE = Path(__file__).parent
AGENTS_DIR = HERE / "agents"


def _domain_slug(domain: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", domain.lower()).strip("-") or "uncategorized"


CORPUS_CONTRACT = "RealityEngine_Machines corpus-exit-v2.0 (docs/CORPUS_EXIT_CRITERIA.md §3.7)"


def _provenance(mdir: Path, skipped_fixtures: list[str]) -> dict:
    """What this index was generated from, per CORPUS_EXIT_CRITERIA §3.7 item 4.

    §3.7 asks a regeneration to record the tag (`corpus-exit-v2.0`) in its output. The tag
    alone would overstate it: the corpus moves on after the tag (the 2026-09-16
    action changes of Machines#154 are after it), and an index stamped only with
    the tag reads as generated *from* the tagged corpus. So record both — the
    contract this output satisfies, and the corpus it was actually derived from.

    The fingerprint is the corpus repo's own definition
    (`scripts/ces_corpus_fingerprint.py`), imported rather than restated, so this
    and the CES contract shards cannot disagree about what "the corpus changed"
    means. Content-derived, never time-derived: two regenerations of the same
    corpus stamp the same digest. Only the rolled-up digest is kept — per-file
    members for 1,327 machines would dwarf the index they describe.
    """
    corpus: dict = {"machineCount": None, "digest": None}
    scripts = mdir.parent / "scripts"
    try:
        sys.path.insert(0, str(scripts))
        import ces_corpus_fingerprint as fp  # type: ignore[import-not-found]
        f = fp.fingerprint_paths(sorted(mdir.rglob("*.json")), mdir)
        corpus = {"algorithm": f["algorithm"], "machineCount": f["machineCount"], "digest": f["digest"]}
    except ImportError as exc:
        corpus["unavailable"] = f"{scripts}/ces_corpus_fingerprint.py not importable: {exc}"
        print(f"warning: corpus fingerprint unavailable ({exc}); provenance records no digest", file=sys.stderr)
    finally:
        if sys.path and sys.path[0] == str(scripts):
            sys.path.pop(0)
    return {
        "generator": "machine-behaviors/materialize_agents.py",
        "conformsTo": CORPUS_CONTRACT,
        "corpus": corpus,
        "skippedConformanceFixtures": skipped_fixtures,
    }


def read_manifest(path: Path) -> list[str]:
    """Corpus-relative machine paths, one per line; blanks and # comments skipped."""
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            entries.append(line)
    return entries


def check_outputs(planned: dict[Path, str], stale_scope: Path | None) -> list[str]:
    """Differences between what would be written and what is on disk."""
    problems = []
    for path, content in sorted(planned.items()):
        rel = path.relative_to(AGENTS_DIR)
        if not path.exists():
            problems.append(f"missing  {rel}")
        elif path.read_text(encoding="utf-8") != content:
            problems.append(f"differs  {rel}")
    if stale_scope is not None:
        for path in sorted(stale_scope.glob("*/*.oc-agent.json")):
            if path not in planned:
                problems.append(f"orphan   {path.relative_to(AGENTS_DIR)} (no machine derives it)")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="Materialize OC agent specs by domain.")
    ap.add_argument("--fresh", action="store_true", help="clear agents/ before writing")
    ap.add_argument("--domain", default=None, help="restrict to one domain")
    ap.add_argument("--check", action="store_true",
                    help="write nothing; fail if the committed specs differ from what the corpus derives")
    ap.add_argument("--manifest", type=Path, default=None,
                    help="with --check: only the machines this corpus manifest lists")
    args = ap.parse_args()
    if args.manifest and not args.check:
        ap.error("--manifest selects a subset to verify; it requires --check "
                 "(writing a subset would leave INDEX.json describing a corpus that is not on disk)")
    if args.check and args.fresh:
        ap.error("--check writes nothing, so --fresh has nothing to clear")

    cfg = load_config()
    mdir = _abs(cfg["machinesDir"])
    selected: set[Path] | None = None
    not_in_corpus: list[str] = []
    if args.manifest:
        selected = set()
        for entry in read_manifest(args.manifest):
            path = (mdir / entry).resolve()
            if path.is_file():
                selected.add(path)
            else:
                # localAIStack machines are listed in the regression corpus but
                # contracted in localAIStack; OpenClaw derives no agent for them.
                not_in_corpus.append(entry)
    if args.fresh and AGENTS_DIR.exists():
        # --fresh clears generated specs, not everything under agents/.
        # agents/profiles/ holds hand-maintained corpus selections —
        # regression.txt is what `startUniverse.sh --agent-profile=regression`
        # loads — and a blanket rmtree deleted it, which is a broken lane rather
        # than a stale artifact. Preserve any non-generated subdirectory.
        for child in AGENTS_DIR.iterdir():
            if child.name == "profiles":
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    if not args.check:
        AGENTS_DIR.mkdir(parents=True, exist_ok=True)

    planned: dict[Path, str] = {}
    index = []
    per_domain = Counter()
    axis_basis = Counter()
    errors = []
    written = 0
    seen_paths: dict[str, str] = {}
    skipped_fixtures: list[str] = []  # output path -> machine stem, to catch collisions

    for f in sorted(mdir.rglob("*.json")):  # rglob: cover machines/domains/** subdirs
        if selected is not None and f.resolve() not in selected:
            continue
        try:
            data = json.loads(f.read_text())
        except Exception as exc:
            errors.append((f.name, f"unparseable: {exc}"))
            continue
        meta = as_object(as_object(data.get("machine")).get("metadata"))
        domain = primary_domain(meta)
        if args.domain and domain != args.domain:
            continue
        # Conformance fixtures get no agent, deliberately.
        #
        # The five arbitration fixtures exist to prove resolution is
        # deterministic, and an agent is a `generated` contributor — exactly the
        # non-determinism that would invalidate what they test. Recorded in
        # RealityEngine_Machines CORPUS_EXIT_CRITERIA §3.3, which states that a
        # regeneration producing one spec per machine (1,327 at corpus-exit-v2.0)
        # rather than 1,322 is wrong. It was:
        # a --fresh run produced agents for all five before this guard existed.
        #
        # Matched on the family *or* the workflow tags. RealityEngine_Machines#110
        # (2026-09-06) moved `arbitration-fixture` from `tagging.family` into
        # `tagging.workflowTags`; a family-only test then matched nothing, and
        # a 2026-09-25 regeneration produced 1,328 specs.
        tagging = as_object(meta.get("tagging"))
        if (str(tagging.get("family", "")) == "arbitration-fixture"
                or "arbitration-fixture" in [str(t) for t in tagging.get("workflowTags") or []]):
            skipped_fixtures.append(f.stem)
            continue
        try:
            inst = tmpl.derive(f, cfg)
        except Exception as exc:
            errors.append((f.name, f"derive error: {exc}"))
            continue
        dom_slug = _domain_slug(domain)
        out_dir = AGENTS_DIR / dom_slug
        code = inst["machine"]["code"]
        # filename keys off agentId (slug of machine name) — unique corpus-wide,
        # unlike code (triggerConfig.processId can repeat, e.g. RSFlipFlop variants).
        out = out_dir / f"{inst['agentId']}.oc-agent.json"
        key = str(out)
        if key in seen_paths:
            errors.append((f.stem, f"filename collision with {seen_paths[key]} -> {out.name}"))
            continue
        seen_paths[key] = f.stem
        planned[out] = json.dumps(inst, indent=2) + "\n"
        written += 1
        per_domain[dom_slug] += 1
        axis_basis[inst["diagnostics"]["axisBasis"]] += 1
        index.append({
            "machineId": inst["machine"]["id"],
            "machineName": inst["machine"]["name"],
            "code": code,
            "agentId": inst["agentId"],
            "domain": dom_slug,
            "machineClass": inst["machine"]["machineClass"],
            "role": inst["role"],
            "inputRegion": inst["machine"]["inputRegion"],
            "axisBasis": inst["diagnostics"]["axisBasis"],
            "path": str(out.relative_to(AGENTS_DIR)),
        })

    def report_errors() -> None:
        print(f"\n{len(errors)} error(s):")
        for name, msg in errors[:20]:
            print(f"  {name}: {msg}")

    # A check that could not derive every agent has not verified them.
    if args.check and errors:
        report_errors()
        return 1

    if selected is not None:
        # Subset check: the specs only. INDEX.* describe the whole corpus.
        problems = check_outputs(planned, None)
        print(f"checked {len(planned)} agent(s) derived from {args.manifest}")
        if skipped_fixtures:
            print(f"  excluded by rule (conformance fixtures): {sorted(skipped_fixtures)}")
        if not_in_corpus:
            print(f"  excluded by rule (not in the machine corpus): {not_in_corpus}")
        for line in problems:
            print(f"  STALE {line}")
        if problems:
            print(f"\n{len(problems)} agent spec(s) are stale against the corpus. Regenerate:\n"
                  "  python3 machine-behaviors/materialize_agents.py --fresh")
            return 1
        print("agent specs are current")
        return 0

    # indexes
    provenance = _provenance(mdir, sorted(skipped_fixtures))
    planned[AGENTS_DIR / "INDEX.json"] = json.dumps({
        "total": written, "byDomain": dict(sorted(per_domain.items())),
        "provenance": provenance,
        "agents": sorted(index, key=lambda r: (r["domain"], r["code"])),
    }, indent=2) + "\n"

    lines = ["# OC-Agent corpus — index", "",
             f"One input-analyst agent per machine ({written} total), under `agents/<domain>/`.",
             "", "| domain | agents |", "|---|---|"]
    for dom, n in sorted(per_domain.items()):
        lines.append(f"| {dom} | {n} |")
    lines += ["", f"**total: {written}**", "",
              "axis grounding: " + ", ".join(f"{k}={v}" for k, v in sorted(axis_basis.items())),
              "", f"Conforms to: {provenance['conformsTo']}. "
              f"Corpus: {provenance['corpus'].get('machineCount')} machines, "
              f"`{provenance['corpus'].get('digest')}`.",
              "", "Regenerate: `python3 materialize_agents.py --fresh`."]
    planned[AGENTS_DIR / "INDEX.md"] = "\n".join(lines) + "\n"

    if args.check:
        problems = check_outputs(planned, None if args.domain else AGENTS_DIR)
        for line in problems:
            print(f"  STALE {line}")
        if problems:
            print(f"\n{len(problems)} file(s) are stale against the corpus. Regenerate:\n"
                  "  python3 machine-behaviors/materialize_agents.py --fresh")
            return 1
        print(f"agent corpus is current ({written} agents)")
        return 0

    for path, content in planned.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    print(f"materialized {written} agents across {len(per_domain)} domains")
    for dom, n in sorted(per_domain.items()):
        print(f"  {dom:24s} {n}")
    print(f"axis grounding: {dict(sorted(axis_basis.items()))}")
    print(f"skipped conformance fixtures: {len(skipped_fixtures)} "
          f"{sorted(skipped_fixtures)}")
    if errors:
        report_errors()
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
