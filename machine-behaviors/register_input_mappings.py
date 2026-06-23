#!/usr/bin/env python3
"""Generalize input-analyst source-mapping registration across a domain.

M6 wired ONE input-region mapping (Home Chronic Pain Monitor) into the PE so an
OpenClaw input-analyst completion lands in the machine's own input region and RE
fires its CES.  This generalizes that to every agent-capable machine in a domain
(default: health-personal):

  1. derive each machine's OC-Agent-Template instance (oc_agent_template.derive);
  2. build a PE source mapping at the machine's *input* region with the axis-key
     pointers;
  3. skip machines whose input region collides with the integration sensor bands
     (4200-4319) or with another selected machine — those are real sensor inputs,
     not agent-supplied (e.g. sensor-preaggregators);
  4. --write merges them (idempotent, by id) into BOTH
     RealityEngine_CI/config/integrations.json and integrations.example.json;
  5. --verify posts a deterministic firing input for each registered machine,
     pushes the PE once, and reports which machines transitioned (output region
     non-zero) — proving domain-wide agent transitions, not just registration.

Usage:
    python3 register_input_mappings.py                 # dry-run: list mappings + collisions
    python3 register_input_mappings.py --write         # merge into the CI configs
    python3 register_input_mappings.py --verify         # post firing inputs, push, report
    python3 register_input_mappings.py --domain energy --write
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import oc_agent_template as tmpl
from derive_agents import as_object, as_list, load_config, _abs, primary_domain

HERE = Path(__file__).parent
CI_CONFIG_DIR = (HERE / "../../RealityEngine_CI/config").resolve()
INTEGRATION_BANDS = [(4200, 120)]  # reserved sensor-integration band: do not overlay


def _overlaps(o1: int, l1: int, o2: int, l2: int) -> bool:
    return o1 < o2 + l2 and o2 < o1 + l1


def _mapping_for(inst: dict[str, Any]) -> dict[str, Any]:
    code = inst["machine"]["code"]
    region = as_object(inst["machine"]["inputRegion"])
    axes = as_list(inst["reasoning"]["inputAxes"])
    return {
        "id": f"acp-{code}-input-assessment",
        "sensorIdTemplate": "acp.openclaw.{agent}.assessment",
        "region": {"offset": region.get("offset"), "length": region.get("length")},
        "extract": {"type": "json", "pointers": [f"/{ax['key']}" for ax in axes]},
        "normalize": {"mode": "passthrough", "clamp": True},
        "ttlMs": 300000, "pushMode": "debounced", "debounceMs": 250,
    }


def _corpus_outputs(mdir: Path) -> list[tuple[str, int, int, str]]:
    """(machineName, outOffset, outLength, fileStem) for every machine."""
    out = []
    for f in mdir.glob("*.json"):
        try:
            m = as_object(json.loads(f.read_text()).get("machine"))
        except Exception:
            continue
        o = as_object(as_object(m.get("perceptualMapping")).get("output"))
        if isinstance(o.get("offset"), int) and isinstance(o.get("length"), int):
            out.append((str(m.get("name")), o["offset"], o["length"], f.stem))
    return out


def collect(domain: str, include_bridged: bool = False) -> dict[str, Any]:
    cfg = load_config()
    mdir = _abs(cfg["machinesDir"])
    corpus_outputs = _corpus_outputs(mdir)
    selected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for f in sorted(mdir.glob("*.json")):
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue
        meta = as_object(as_object(data.get("machine")).get("metadata"))
        if primary_domain(meta) != domain:
            continue
        inst = tmpl.derive(f, cfg)
        region = as_object(inst["machine"]["inputRegion"])
        off, ln = region.get("offset"), region.get("length")
        rec = {"code": inst["machine"]["code"], "agentId": inst["agentId"], "stem": f.stem,
               "machineClass": inst["machine"]["machineClass"],
               "offset": off, "length": ln, "instance": inst}
        if not isinstance(off, int) or not isinstance(ln, int) or ln < 1:
            rec["reason"] = "no usable input region"
            skipped.append(rec); continue
        if any(_overlaps(off, ln, bo, bl) for bo, bl in INTEGRATION_BANDS):
            rec["reason"] = f"input region [{off}:{off+ln}] collides with integration band"
            skipped.append(rec); continue
        # bridge-fed: input region IS another machine's output region. The input
        # is produced by composition; an agent there would overwrite it.
        producers = [nm for (nm, oo, ol, st) in corpus_outputs
                     if st != f.stem and _overlaps(off, ln, oo, ol)]
        if producers:
            rec["bridgedBy"] = producers[:3]
        selected.append(rec)

    # intra-domain input-region collision check among the selected set
    for i in range(len(selected)):
        for j in range(i + 1, len(selected)):
            a, b = selected[i], selected[j]
            if _overlaps(a["offset"], a["length"], b["offset"], b["length"]):
                a.setdefault("collidesWith", []).append(b["code"])
                b.setdefault("collidesWith", []).append(a["code"])

    def eligible(r):
        if "collidesWith" in r:
            return False
        if "bridgedBy" in r and not include_bridged:
            return False
        return True

    leaf = [r for r in selected if eligible(r)]
    bridged = [r for r in selected if "bridgedBy" in r and "collidesWith" not in r]
    mappings = [_mapping_for(r["instance"]) for r in leaf]
    # mapping ids for every health-personal machine (used to prune stale ones on write)
    all_domain_ids = {f"acp-{r['code']}-input-assessment" for r in selected}
    keep_ids = {m["id"] for m in mappings}
    return {"domain": domain, "selected": selected, "skipped": skipped,
            "collided": [r for r in selected if "collidesWith" in r],
            "bridged": bridged, "leaf": leaf, "mappings": mappings,
            "pruneIds": sorted(all_domain_ids - keep_ids)}


def _merge_into(path: Path, mappings: list[dict[str, Any]],
                prune_ids: list[str]) -> tuple[int, int, int]:
    d = json.loads(path.read_text())
    sms = d.setdefault("sourceMappings", [])
    prune = set(prune_ids)
    pruned = len([m for m in sms if m.get("id") in prune])
    sms = [m for m in sms if m.get("id") not in prune]
    ids = {m.get("id"): k for k, m in enumerate(sms)}
    added = updated = 0
    for m in mappings:
        if m["id"] in ids:
            sms[ids[m["id"]]] = m; updated += 1
        else:
            sms.append(m); added += 1
    d["sourceMappings"] = sms
    path.write_text(json.dumps(d, indent=2) + "\n")
    return added, updated, pruned


# ── verification: deterministic firing input per machine ──────────────────────

def _firing_input(machine_path: Path, in_len: int) -> dict[str, Any] | None:
    """Craft an input that fires an isInitial output vector (single-step CES)."""
    data = json.loads(machine_path.read_text())
    machine = as_object(data.get("machine"))
    for seq in as_list(machine.get("sequences")):
        seq = as_object(seq)
        for vec in as_list(seq.get("vectors")):
            vec = as_object(vec)
            if not vec.get("isInitial") or not as_list(vec.get("outputVectors")):
                continue
            elems = as_list(vec.get("elements"))
            if len(elems) != in_len:
                continue
            values = []
            for e in elems:
                e = as_object(e)
                thr = e.get("threshold", 0.5) or 0.5
                hi = (e.get("value", 0) or 0) >= thr
                values.append(round(min(0.99, thr + 0.3), 3) if hi
                              else round(max(0.01, thr - 0.3), 3))
            out = as_object(as_list(vec.get("outputVectors"))[0]).get("vector")
            return {"values": values, "sequenceId": seq.get("id"), "expectedOutput": out}
    return None


def verify(report: dict[str, Any]) -> None:
    import urllib.request
    cfg = load_config()
    pe = as_object(cfg.get("pe")).get("baseUrl", "http://localhost:5100")
    mdir = _abs(cfg["machinesDir"])

    leaf_codes = {r["code"] for r in report["leaf"]}
    posted = []
    for r in report["selected"]:
        if r["code"] not in leaf_codes:
            posted.append({**r, "fire": None, "note": "not-registered",
                           "kind": "bridge" if "bridgedBy" in r else "skip"}); continue
        inst = r["instance"]
        mpath = mdir / f"{inst['machine']['id']}.json"
        fire = _firing_input(mpath, r["length"])
        if not fire:
            posted.append({**r, "fire": None, "note": "no single-step isInitial output"})
            continue
        axes = as_list(inst["reasoning"]["inputAxes"])
        body = {"provider": "acp",
                "sourceMappingId": f"acp-{r['code']}-input-assessment",
                "agent": r["agentId"],
                "values": fire["values"]}
        for ax, v in zip(axes, fire["values"]):
            body[ax["key"]] = v
        data = json.dumps(body).encode()
        req = urllib.request.Request(pe + "/api/integrations/completions", data=data,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                ok = resp.status == 200
        except Exception as exc:
            posted.append({**r, "fire": fire, "note": f"post failed: {exc}"}); continue
        posted.append({**r, "fire": fire, "note": "posted" if ok else "post non-200"})

    # one push, then read each machine's output region
    req = urllib.request.Request(pe + "/api/push", data=b"{}",
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        step = json.loads(resp.read()).get("step", {})
    ps = step.get("perceptualSpace", [])

    leaf_fired = leaf_total = bridge_fired = bridge_total = 0
    print(f"\n=== verify: posted firing inputs to {len(report['leaf'])} leaf machines, then 1 push ===")
    print(f"{'machine':34s} {'role':6s} {'in→out':>12s}  output-region        result")
    for p in posted:
        inst = p["instance"]; outr = as_object(inst["machine"]["outputRegion"])
        oo, ol = outr.get("offset"), outr.get("length")
        outvals = [round(ps[oo + k], 2) for k in range(ol)] if isinstance(oo, int) and ps and oo + ol <= len(ps) else []
        nonzero = any(v for v in outvals)
        kind = p.get("kind", "leaf")
        if kind == "bridge":
            bridge_total += 1; bridge_fired += 1 if nonzero else 0
            res = "FIRED via composition ✓" if nonzero else "(awaiting upstream)"
            role = "bridge"
        elif kind == "skip":
            res = "skipped (band collision)"; role = "—"
        elif p.get("fire") is None:
            res = "no single-step fire (multi-step seq)"; role = "leaf"
        elif "fail" in p["note"] or "non-200" in p["note"]:
            res = "POST " + p["note"]; role = "leaf"
        else:
            leaf_total += 1; leaf_fired += 1 if nonzero else 0
            res = "FIRED direct ✓" if nonzero else "no transition"; role = "leaf"
        print(f"{p['code']:34s} {role:6s} {p['offset']}→{str(oo):>6s}  {str(outvals):20s} {res}")
    print(f"\nleaf (direct agent input): {leaf_fired}/{leaf_total} single-step machines fired")
    print(f"bridge (composition-driven): {bridge_fired}/{bridge_total} aggregators fired from leaf outputs")


def main() -> int:
    ap = argparse.ArgumentParser(description="Register input-analyst source mappings for a domain.")
    ap.add_argument("--domain", default="health-personal")
    ap.add_argument("--write", action="store_true", help="merge into CI integrations configs")
    ap.add_argument("--verify", action="store_true", help="post firing inputs and report transitions")
    ap.add_argument("--include-bridged", action="store_true",
                    help="also register machines whose input is another machine's output (default: exclude)")
    args = ap.parse_args()

    report = collect(args.domain, include_bridged=args.include_bridged)
    print(f"domain={args.domain}: {len(report['selected'])} in-domain, "
          f"{len(report['skipped'])} skipped, {len(report['collided'])} colliding, "
          f"{len(report['bridged'])} bridge-fed → {len(report['mappings'])} leaf mappings"
          f"{' (incl. bridged)' if args.include_bridged else ''}")
    for r in report["skipped"]:
        print(f"  skip    {r['code']:34s} {r.get('reason')}")
    for r in report["collided"]:
        print(f"  COLLIDE {r['code']:32s} input [{r['offset']}:{r['offset']+r['length']}] "
              f"overlaps {r['collidesWith']}")
    for r in report["bridged"]:
        tag = "incl" if args.include_bridged else "excl"
        print(f"  bridge[{tag}] {r['code']:30s} input [{r['offset']}:{r['offset']+r['length']}] "
              f"<= output of {r['bridgedBy']}")
    if not (args.write or args.verify):
        print("\nmappings (dry-run; pass --write to register):")
        for m in report["mappings"]:
            print(f"  {m['id']:46s} region={m['region']} pointers={m['extract']['pointers']}")
        # persist an artifact for the record
        out = HERE / "out" / f"{args.domain}.input-mappings.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report["mappings"], indent=2) + "\n")
        print(f"\nwrote {out.relative_to(HERE)}")

    if args.write:
        for name in ("integrations.json", "integrations.example.json"):
            p = CI_CONFIG_DIR / name
            a, u, pr = _merge_into(p, report["mappings"], report["pruneIds"])
            print(f"  {name}: +{a} added, {u} updated, -{pr} pruned")
        if report["pruneIds"]:
            print(f"  pruned (bridge-fed/ineligible): {report['pruneIds']}")
        print("Restart the PE to load the new mappings.")

    if args.verify:
        verify(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
