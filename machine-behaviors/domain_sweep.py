#!/usr/bin/env python3
"""Sweep a whole domain: derive OpenClaw agents for every machine at once.

Extends the single-machine prototype to a domain.  Key additions over
derive_agents.py:

  * domain discovery by metadata.tagging.primaryDomain / category;
  * a corpus-wide perceptual-space scan that reserves the completion band
    *above* the highest offset used anywhere in the corpus (the M5 fix), so
    derived regions can never collide with a real machine;
  * global, monotonically-increasing region allocation so no two agents in the
    domain share a completion vector;
  * batch validation against the canonical agent-binding schema;
  * a coverage report (text + optional markdown/JSON artifacts).

READ-ONLY w.r.t. the corpus and PE loaders.

    python3.13 domain_sweep.py                       # health-personal, text report
    python3.13 domain_sweep.py --domain health-personal --write   # also write out/
    python3.13 domain_sweep.py --md                  # markdown report to stdout
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from derive_agents import derive, load_config, primary_domain, as_object, as_list, _abs
import minischema

HERE = Path(__file__).parent
OUT = HERE / "out"


def discover(machines_dir: Path, domain: str | None) -> list[Path]:
    """Machines for a domain, or all machines when domain is None/'*'.

    Uses rglob so subdirectory corpora (e.g. machines/domains/energy/) are covered.
    """
    found = []
    for p in sorted(machines_dir.rglob("*.json")):
        try:
            d = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        md = as_object(as_object(d.get("machine")).get("metadata"))
        if not md:
            continue
        if domain in (None, "*") or primary_domain(md) == domain:
            found.append(p)
    return found


def reserved_band(cfg: dict[str, Any]) -> dict[str, Any] | None:
    """The registry-declared ACP completion band, the single source of truth.

    Returns the reservedRange owned by provider 'acp' (or any write-back range)
    from domains/domain-registry.json -> rangePolicy.reservedRanges, or None if the
    registration has not been added.
    """
    try:
        registry = json.loads(_abs(cfg["registryPath"]).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    ranges = as_list(as_object(registry.get("rangePolicy")).get("reservedRanges"))
    for r in ranges:
        r = as_object(r)
        if r.get("provider") == "acp" or r.get("writeBack"):
            return r
    return ranges[0] if ranges else None


def corpus_max_end(machines_dir: Path) -> int:
    end = 0
    for p in machines_dir.rglob("*.json"):
        try:
            d = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        pm = as_object(as_object(d.get("machine")).get("perceptualMapping"))
        for key in ("input", "output"):
            r = as_object(pm.get(key))
            if "offset" in r and "length" in r:
                end = max(end, int(r["offset"]) + int(r["length"]))
    return end


def sweep(domain: str, cfg: dict[str, Any]) -> dict[str, Any]:
    machines_dir = _abs(cfg["machinesDir"])
    region_cfg = as_object(cfg.get("completionRegions"))
    length = int(region_cfg.get("length", 4))
    configured_base = int(region_cfg.get("baseOffset", 4400))

    # Prefer the registry-declared reserved band (rangePolicy.reservedRanges) as
    # the single source of truth.  Fall back to reserving above the corpus max,
    # rounded to a 100 boundary, when the registration is absent.
    max_end = corpus_max_end(machines_dir)
    reserved = reserved_band(cfg)
    if reserved:
        band_base = int(reserved["offset"])
        band_limit = int(reserved["offset"]) + int(reserved["length"])
        band_source = f"registry-reserved:{reserved.get('id')}"
    else:
        band_base = max(configured_base, int(math.ceil(max_end / 100.0) * 100))
        band_limit = None
        band_source = "dynamic-above-corpus-max"

    paths = discover(machines_dir, domain)
    plans: list[dict[str, Any]] = []
    cursor = band_base
    for path in paths:
        plan = derive(path, cfg, region_base=cursor)
        # advance by the total positions actually allocated (regions are variable
        # length now: advise=4, supervised-act=5, automated-act=6).
        used = sum(a["realityVectorImpact"]["length"]
                   for a in plan["agents"] if a["realityVectorImpact"])
        cursor += used
        plans.append(plan)

    schema = minischema.load_schema(_abs(cfg["schemasDir"]) / "agent-binding.schema.json")
    validation_errors: list[str] = []
    all_regions: list[tuple[int, int]] = []
    for plan in plans:
        for a in plan["agents"]:
            errs = minischema.validate(a["agentBinding"], schema)
            for e in errs:
                validation_errors.append(f"{plan['machine']['code']}/{a['agent']}: {e}")
            rv = a["realityVectorImpact"]
            if rv:
                all_regions.append((rv["offset"], rv["offset"] + rv["length"]))

    # global region collisions
    region_collisions = []
    ordered = sorted(all_regions)
    for i in range(1, len(ordered)):
        if ordered[i][0] < ordered[i - 1][1]:
            region_collisions.append((ordered[i - 1], ordered[i]))

    # band-vs-corpus collisions (should be empty because band_base > max_end)
    band_span = (band_base, cursor)

    # allocation must stay inside the registry-reserved band when one exists
    band_overflow = bool(band_limit is not None and cursor > band_limit)
    if band_overflow:
        validation_errors.append(
            f"completion allocation [{band_base}:{cursor}] overflows reserved band "
            f"[{band_base}:{band_limit}] — widen reservedRanges length")

    return {
        "domain": domain,
        "machineCount": len(plans),
        "corpusMaxEnd": max_end,
        "bandBase": band_base,
        "bandLimit": band_limit,
        "bandSource": band_source,
        "bandSpan": list(band_span),
        "bandOverflow": band_overflow,
        "regionLength": length,
        "plans": plans,
        "validationErrors": validation_errors,
        "regionCollisions": region_collisions,
    }


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    plans = result["plans"]
    outputs = sum(len(p["outputs"]) for p in plans)
    bindings = sum(len(p["agents"]) for p in plans)
    agent_use = Counter(a["agent"] for p in plans for a in p["agents"])
    mode_dist = Counter(o["autonomyMode"] for p in plans for o in p["outputs"])
    class_dist = Counter(p["machine"]["machineClass"] for p in plans)
    writeback_agents = sum(1 for p in plans for a in p["agents"] if a["realityVectorImpact"])
    observe_agents = bindings - writeback_agents
    low_conf = [(p["machine"]["code"], o["label"], o["agent"])
                for p in plans for o in p["outputs"] if o.get("lowConfidence")]
    per_domain = {}
    for p in plans:
        dom = p["machine"]["domain"]
        d = per_domain.setdefault(dom, {"machines": 0, "outputs": 0, "bindings": 0})
        d["machines"] += 1
        d["outputs"] += len(p["outputs"])
        d["bindings"] += len(p["agents"])
    return {
        "lowConfidenceSelections": low_conf,
        "perDomain": per_domain,
        "machines": len(plans),
        "cesOutputs": outputs,
        "agentBindings": bindings,
        "writebackAgents": writeback_agents,
        "observeAgents": observe_agents,
        "agentFamilyUse": dict(agent_use.most_common()),
        "modeDistribution": dict(mode_dist),
        "classDistribution": dict(class_dist),
    }


def text_report(result: dict[str, Any]) -> str:
    s = summarize(result)
    lines = []
    lines.append(f"\n=== Domain sweep: {result['domain']} ===")
    lines.append(f"machines={s['machines']}  CES-outputs={s['cesOutputs']}  "
                 f"agent-bindings={s['agentBindings']} "
                 f"(write-back={s['writebackAgents']}, observe={s['observeAgents']})")
    limit = f"/{result['bandLimit']}" if result.get("bandLimit") else ""
    lines.append(f"corpus max offset+len={result['corpusMaxEnd']}  "
                 f"completion band={result['bandSpan'][0]}..{result['bandSpan'][1]}{limit} "
                 f"[{result['bandSource']}]  overflow={result['bandOverflow']}")
    lines.append(f"validation errors={len(result['validationErrors'])}  "
                 f"region collisions={len(result['regionCollisions'])}  "
                 f"low-confidence selections={len(s['lowConfidenceSelections'])}")
    if len(s["perDomain"]) > 1:
        lines.append("\nper-domain coverage:")
        for dom, d in sorted(s["perDomain"].items()):
            lines.append(f"  {dom:22} machines={d['machines']:4} "
                         f"behaviors={d['outputs']:5} bindings={d['bindings']:5}")
    lines.append("\nmachine class distribution: " +
                 ", ".join(f"{k}={v}" for k, v in s["classDistribution"].items()))
    lines.append("autonomy mode distribution: " +
                 ", ".join(f"{k}={v}" for k, v in s["modeDistribution"].items()))
    lines.append("agent family usage: " +
                 ", ".join(f"{k}={v}" for k, v in s["agentFamilyUse"].items()))
    lines.append("\nper-machine:")
    lines.append(f"  {'machine':40}{'class':24}{'agents (mode@region)'}")
    for p in result["plans"]:
        agents = "; ".join(
            f"{a['agent']}@{a['autonomyMode']}"
            + (f"[{a['realityVectorImpact']['offset']}:"
               f"{a['realityVectorImpact']['offset'] + a['realityVectorImpact']['length']}]"
               if a["realityVectorImpact"] else "[observe]")
            for a in p["agents"])
        lines.append(f"  {p['machine']['code']:40}{p['machine']['machineClass']:24}{agents}")
    if result["validationErrors"]:
        lines.append("\nVALIDATION ERRORS:")
        lines += [f"  {e}" for e in result["validationErrors"][:20]]
    if result["regionCollisions"]:
        lines.append("\nREGION COLLISIONS:")
        lines += [f"  {c}" for c in result["regionCollisions"]]
    return "\n".join(lines)


def md_report(result: dict[str, Any]) -> str:
    s = summarize(result)
    out = [f"# OpenClaw agent automation — `{result['domain']}` domain (generated snapshot)\n",
           f"Generated by `domain_sweep.py`. Regenerate with "
           f"`python3.13 domain_sweep.py --domain {result['domain']} --md`.\n",
           "## Coverage\n",
           f"- Machines: **{s['machines']}**",
           f"- CES outputs (behaviors): **{s['cesOutputs']}**",
           f"- Agent bindings: **{s['agentBindings']}** "
           f"(write-back **{s['writebackAgents']}**, observe **{s['observeAgents']}**)",
           f"- Reserved completion band: **{result['bandSpan']}** "
           f"(corpus max offset+len = {result['corpusMaxEnd']})",
           f"- Schema validation errors: **{len(result['validationErrors'])}**, "
           f"region collisions: **{len(result['regionCollisions'])}**\n",
           "## Autonomy mode distribution\n",
           "| mode | count |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in s["modeDistribution"].items()]
    out += ["\n## Agent family usage\n", "| agent | bindings |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in s["agentFamilyUse"].items()]
    out += ["\n## Per-machine agent map\n",
            "| machine | class | agent | mode | reality-vector impact |",
            "|---|---|---|---|---|"]
    for p in result["plans"]:
        for a in p["agents"]:
            rv = a["realityVectorImpact"]
            rv_s = (f"`[{rv['offset']}:{rv['offset'] + rv['length']}]`" if rv else "none (observe)")
            out.append(f"| {p['machine']['code']} | {p['machine']['machineClass']} "
                       f"| {a['agent']} | {a['autonomyMode']} | {rv_s} |")
    return "\n".join(out) + "\n"


def pe_source_mappings(result: dict[str, Any]) -> dict[str, Any]:
    """Materialize the PE source mappings for every completion sensor.

    These register the agents' write-back regions as PE integration sources so PE
    can ingest OpenClaw completions (POST /api/integrations/completions) and RE can
    read them — the 'mappings to PE sources for the responses'.  Shape mirrors
    RealityEngine_CPP/config/integrations.example.json `sourceMappings`.
    """
    mappings = []
    for p in result["plans"]:
        for a in p["agents"]:
            wb = a["agentBinding"]["writeBack"]
            if wb.get("type") != "pe-sensor":
                continue
            sm = wb["sourceMapping"]
            semantics = [f["semantic"] for f in a["responseMapping"]["fields"] if f.get("target")]
            mappings.append({
                "id": sm["id"],
                "sensorId": sm["sensorId"],
                "name": sm.get("name", sm["sensorId"]),
                "provider": "acp",
                "machine": p["machine"]["code"],
                "domain": p["machine"]["domain"],
                "agent": a["agent"],
                "region": sm["region"],
                "extract": {"type": "json", "pointers": [f"/{s}" for s in semantics]},
                "normalize": {"mode": "passthrough", "clamp": True},
                "ttlMs": sm["ttlMs"],
                "pushMode": "debounced",
                "debounceMs": 250,
            })
    return {
        "_comment": "Generated by domain_sweep.py --all --write. PE INTEGRATIONS_CONFIG "
                    "source mappings for OpenClaw/ACP agent completion write-back. "
                    "Drop into the PE integrations config sourceMappings array.",
        "schemaVersion": "1.0.0",
        "generatedFrom": result["domain"],
        "reservedBand": result["bandSpan"],
        "count": len(mappings),
        "sourceMappings": mappings,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", default="health-personal")
    parser.add_argument("--all", action="store_true", help="sweep every machine in every domain")
    parser.add_argument("--md", action="store_true", help="emit markdown report to stdout")
    parser.add_argument("--json", action="store_true", help="emit full JSON to stdout")
    parser.add_argument("--write", action="store_true", help="write out/ artifacts")
    args = parser.parse_args()

    cfg = load_config()
    domain = "*" if args.all else args.domain
    result = sweep(domain, cfg)
    label = "corpus" if args.all else args.domain

    if args.json:
        print(json.dumps(result, indent=2))
    elif args.md:
        print(md_report(result))
    else:
        print(text_report(result))

    if args.write:
        OUT.mkdir(exist_ok=True)
        (OUT / f"{label}.agents.json").write_text(json.dumps(result, indent=2) + "\n")
        (OUT / f"{label}.report.md").write_text(md_report(result))
        sm = pe_source_mappings(result)
        (OUT / f"{label}.pe-source-mappings.json").write_text(json.dumps(sm, indent=2) + "\n")
        print(f"\nwrote out/{label}.agents.json, out/{label}.report.md, "
              f"out/{label}.pe-source-mappings.json ({sm['count']} PE source mappings)")

    return 1 if (result["validationErrors"] or result["regionCollisions"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
