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


CORPUS_CONTRACT = "RealityEngine_Machines corpus-exit-v1.0 (docs/CORPUS_EXIT_CRITERIA.md §3.7)"


def _provenance(mdir: Path, skipped_fixtures: list[str]) -> dict:
    """What this index was generated from, per CORPUS_EXIT_CRITERIA §3.7 item 4.

    §3.7 asks a regeneration to record `corpus-exit-v1.0` in its output. The tag
    alone would overstate it: the corpus moves on after the tag (the 2026-09-16
    action changes of Machines#154 are after it), and an index stamped only with
    the tag reads as generated *from* the tagged corpus. So record both — the
    contract this output satisfies, and the corpus it was actually derived from.

    The fingerprint is the corpus repo's own definition
    (`scripts/ces_corpus_fingerprint.py`), imported rather than restated, so this
    and the CES contract shards cannot disagree about what "the corpus changed"
    means. Content-derived, never time-derived: two regenerations of the same
    corpus stamp the same digest. Only the rolled-up digest is kept — per-file
    members for 1,328 machines would dwarf the index they describe.
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Materialize OC agent specs by domain.")
    ap.add_argument("--fresh", action="store_true", help="clear agents/ before writing")
    ap.add_argument("--domain", default=None, help="restrict to one domain")
    args = ap.parse_args()

    cfg = load_config()
    mdir = _abs(cfg["machinesDir"])
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
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)

    index = []
    per_domain = Counter()
    axis_basis = Counter()
    errors = []
    written = 0
    seen_paths: dict[str, str] = {}
    skipped_fixtures: list[str] = []  # output path -> machine stem, to catch collisions

    for f in sorted(mdir.rglob("*.json")):  # rglob: cover machines/domains/** subdirs
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
        # RealityEngine_Machines corpus-exit-v1.0 §3.3, which states that a
        # regeneration producing 1,328 specs rather than 1,323 is wrong. It was:
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
        out_dir.mkdir(parents=True, exist_ok=True)
        code = inst["machine"]["code"]
        # filename keys off agentId (slug of machine name) — unique corpus-wide,
        # unlike code (triggerConfig.processId can repeat, e.g. RSFlipFlop variants).
        out = out_dir / f"{inst['agentId']}.oc-agent.json"
        key = str(out)
        if key in seen_paths:
            errors.append((f.stem, f"filename collision with {seen_paths[key]} -> {out.name}"))
            continue
        seen_paths[key] = f.stem
        out.write_text(json.dumps(inst, indent=2) + "\n")
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

    # indexes
    provenance = _provenance(mdir, sorted(skipped_fixtures))
    (AGENTS_DIR / "INDEX.json").write_text(json.dumps({
        "total": written, "byDomain": dict(sorted(per_domain.items())),
        "provenance": provenance,
        "agents": sorted(index, key=lambda r: (r["domain"], r["code"])),
    }, indent=2) + "\n")

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
    (AGENTS_DIR / "INDEX.md").write_text("\n".join(lines) + "\n")

    print(f"materialized {written} agents across {len(per_domain)} domains")
    for dom, n in sorted(per_domain.items()):
        print(f"  {dom:24s} {n}")
    print(f"axis grounding: {dict(sorted(axis_basis.items()))}")
    print(f"skipped conformance fixtures: {len(skipped_fixtures)} "
          f"{sorted(skipped_fixtures)}")
    if errors:
        print(f"\n{len(errors)} error(s):")
        for name, msg in errors[:20]:
            print(f"  {name}: {msg}")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
