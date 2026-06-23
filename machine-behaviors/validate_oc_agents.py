#!/usr/bin/env python3
"""Validate OC-Agent-Template instances across the corpus.

For each machine it derives the input-analyst OC-Agent-Template instance
(`oc_agent_template.derive`) and checks:

  1. the instance validates against `templates/oc-agent.schema.json`
     (which $refs the canonical corpus `agent-binding.schema.json`, so the
     embedded binding is checked against the real contract);
  2. structural template invariants the schema can't express:
     - input region length >= 1;
     - one reasoning axis and one responseMapping field per input position;
     - write-back region == the machine's own input region (the analyst feeds
       inputs, it does not invent a band);
     - response-mapping field targets cover every input index exactly once;
     - a non-empty CES catalog (something for the inputs to trigger).

Reports per-machine pass/fail, a domain/corpus coverage summary, and the gaps
that need corpus attention (the validator doubles as a corpus linter).

Usage:
    python3 validate_oc_agents.py                     # default: health-personal
    python3 validate_oc_agents.py --domain energy
    python3 validate_oc_agents.py --all               # whole corpus (structural)
    python3 validate_oc_agents.py --md                # markdown summary
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import minischema
import oc_agent_template as tmpl
from derive_agents import as_object, as_list, load_config, _abs, primary_domain

HERE = Path(__file__).parent
SCHEMA_PATH = HERE / "templates" / "oc-agent.schema.json"


def _machine_files(cfg: dict[str, Any]) -> list[Path]:
    return sorted(_abs(cfg["machinesDir"]).glob("*.json"))


def _machine_domain(path: Path) -> str:
    try:
        data = json.loads(path.read_text())
    except Exception:
        return "unreadable"
    return primary_domain(as_object(as_object(data.get("machine")).get("metadata")))


def validate_instance(instance: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    errors = list(minischema.validate(instance, schema, base_dir=str(SCHEMA_PATH.parent)))

    in_region = as_object(instance["machine"]["inputRegion"])
    length = int(in_region.get("length", 0))
    axes = as_list(instance["reasoning"]["inputAxes"])
    fields = as_list(instance["responseMapping"]["fields"])
    wb = as_object(instance["agentBinding"]["writeBack"])
    wb_region = as_object(wb.get("region"))

    if length < 1:
        errors.append("input region length < 1 (no input vector to feed)")
    if len(axes) != length:
        errors.append(f"reasoning axes ({len(axes)}) != input length ({length})")
    if len(fields) != length:
        errors.append(f"responseMapping fields ({len(fields)}) != input length ({length})")
    if wb_region != in_region:
        errors.append(f"write-back region {wb_region} != machine input region {in_region}")
    if [str(s) for s in as_list(wb.get("semantics"))] != [ax["key"] for ax in axes]:
        errors.append("write-back semantics do not match input axis keys")
    # every input index covered exactly once by a response field target
    covered = sorted(int(as_object(f.get("target")).get("index", -1)) for f in fields)
    if covered != list(range(length)):
        errors.append(f"response field target indices {covered} != 0..{length - 1}")
    if not as_list(instance["reasoning"]["sequenceCatalog"]):
        errors.append("empty CES catalog (no sequence for the inputs to trigger)")
    return errors


def run(domain: str | None, scan_all: bool) -> dict[str, Any]:
    cfg = load_config()
    schema = minischema.load_schema(SCHEMA_PATH)
    rows: list[dict[str, Any]] = []
    by_domain: dict[str, dict[str, int]] = {}

    for path in _machine_files(cfg):
        dom = _machine_domain(path)
        if not scan_all and domain is not None and dom != domain:
            continue
        row: dict[str, Any] = {"file": path.name, "domain": dom}
        try:
            instance = tmpl.derive(path, cfg)
            errors = validate_instance(instance, schema)
            row.update(
                agentId=instance["agentId"],
                inputLength=instance["diagnostics"]["inputLength"],
                axisBasis=instance["diagnostics"]["axisBasis"],
                warnings=instance["diagnostics"]["warnings"],
                errors=errors,
                ok=not errors,
            )
        except Exception as exc:  # derivation itself failed
            row.update(agentId=None, errors=[f"derive error: {exc}"], ok=False,
                       warnings=[], axisBasis="-", inputLength=0)
        rows.append(row)
        bucket = by_domain.setdefault(dom, {"machines": 0, "ok": 0, "warned": 0})
        bucket["machines"] += 1
        bucket["ok"] += 1 if row["ok"] else 0
        bucket["warned"] += 1 if row.get("warnings") else 0

    total = len(rows)
    ok = sum(1 for r in rows if r["ok"])
    return {"rows": rows, "total": total, "ok": ok, "failed": total - ok,
            "byDomain": by_domain,
            "scope": "all-domains" if scan_all else (domain or "all")}


def _print_text(report: dict[str, Any]) -> None:
    rows = report["rows"]
    print(f"OC-Agent-Template validation — scope: {report['scope']}")
    print(f"  machines: {report['total']}  ok: {report['ok']}  failed: {report['failed']}")
    print("  by domain:")
    for dom, b in sorted(report["byDomain"].items()):
        print(f"    {dom:32s} machines={b['machines']:4d} ok={b['ok']:4d} warned={b['warned']:4d}")
    failures = [r for r in rows if not r["ok"]]
    if failures:
        print(f"\n  {len(failures)} failing machine(s):")
        for r in failures[:40]:
            print(f"    {r['file']}: {'; '.join(r['errors'][:3])}")
        if len(failures) > 40:
            print(f"    ... and {len(failures) - 40} more")
    warned = [r for r in rows if r["ok"] and r.get("warnings")]
    if warned:
        print(f"\n  {len(warned)} machine(s) valid with corpus-quality warnings (first 15):")
        for r in warned[:15]:
            print(f"    {r['file']}: {'; '.join(r['warnings'][:2])}")


def _print_md(report: dict[str, Any]) -> None:
    print(f"# OC-Agent-Template validation — {report['scope']}\n")
    print(f"- machines: **{report['total']}**, ok: **{report['ok']}**, "
          f"failed: **{report['failed']}**\n")
    print("| domain | machines | ok | warned |")
    print("|---|---|---|---|")
    for dom, b in sorted(report["byDomain"].items()):
        print(f"| {dom} | {b['machines']} | {b['ok']} | {b['warned']} |")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate OC-Agent-Template instances.")
    parser.add_argument("--domain", default="health-personal")
    parser.add_argument("--all", action="store_true", help="scan the whole corpus")
    parser.add_argument("--md", action="store_true", help="markdown output")
    parser.add_argument("--write", action="store_true", help="write out/oc-agents.<scope>.report.md")
    args = parser.parse_args()
    report = run(None if args.all else args.domain, args.all)
    if args.md:
        _print_md(report)
    else:
        _print_text(report)
    if args.write:
        out = HERE / "out" / f"oc-agents.{report['scope']}.report.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _print_md(report)
        out.write_text(buf.getvalue())
        print(f"\nwrote {out.relative_to(HERE)}")
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
