#!/usr/bin/env python3
"""Materialize private, scoped document-tool credentials and a reviewable plugin patch.

Never edits the running gateway config. Existing private credentials are reused;
changing the active entity/period is an explicit operator configuration change.
"""
import argparse
import json
from pathlib import Path
import secrets


def write(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n")
    path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--machines-root", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--document-url", required=True)
    parser.add_argument("--gateway-url", required=True)
    parser.add_argument("--gateway-token-env", default="OPENCLAW_GATEWAY_TOKEN")
    args = parser.parse_args()
    import os
    context = json.loads(args.context.read_text())
    for key in ["entityId", "period", "factsFingerprint", "sourceFingerprint", "policyFingerprint", "indexSnapshot", "evidenceClass"]:
        if not context.get(key):
            parser.error("Context missing " + key)
    root = Path(__file__).resolve().parents[1]
    if args.state.resolve().is_relative_to(root):
        parser.error("Private runtime state must remain outside the OpenClaw repository")
    args.state.mkdir(parents=True, exist_ok=True, mode=0o700)
    index = json.loads((root / "machine-behaviors/agents/INDEX.json").read_text())
    agents = [a for a in index["agents"] if a["domain"] == "ifrs-accounting"]
    if not agents:
        parser.error("Generate the IFRS agent profile first")
    assets = args.machines_root / "machine_factory/domains/ifrs-accounting"
    catalog = json.loads((assets / "document-asset-catalog.json").read_text())["assetTypes"]
    graph = json.loads((assets / "accounting-workflow-graph.json").read_text())
    existing_path = args.state / "principals.json"
    existing = json.loads(existing_path.read_text()) if existing_path.exists() else []
    tokens = {p["name"]: p["token"] for p in existing}
    scope = [[context["entityId"], context["period"]]]
    principals, bindings, by_machine = [], {}, {}
    for agent in agents:
        spec = json.loads((root / "machine-behaviors/agents" / agent["path"]).read_text())
        if not spec.get("documentWorkflowBinding"):
            parser.error("Missing document facet for " + agent["machineId"])
        token = tokens.get(agent["machineId"], secrets.token_urlsafe(32))
        principals.append(dict(name=agent["machineId"], role="agent", machineId=agent["machineId"], token=token, scopes=scope))
        bindings[agent["agentId"]] = dict(token=token, context=context)
        by_machine[agent["machineId"]] = dict(agentId=agent["agentId"], token=token)
    for role, name in [("operator", "ifrs-document-operator"), ("projection", "ifrs-document-projection")] + [("reviewer", n) for n in sorted({a["reviewerName"] for a in catalog})]:
        principals.append(dict(name=name, role=role, token=tokens.get(name, secrets.token_urlsafe(32)), scopes=scope))
    write(existing_path, principals)
    write(args.state / "gateway-tools.json", dict(url=args.document_url, agents=bindings))
    write(args.state / "openclaw-plugin.patch.json", {"plugins": {"allow": ["ifrs-documents"], "load": {"paths": [str(root / "plugins/ifrs-documents")]}, "entries": {"ifrs-documents": {"enabled": True, "config": {"configurationFile": str(args.state.resolve() / "gateway-tools.json")}}}}, "tools": {"alsoAllow": ['ifrs_document_workflow', 'ifrs_document_inspect', 'ifrs_document_resource', 'ifrs_source_retrieve', 'ifrs_document_draft', 'ifrs_eligibility_draft', 'ifrs_document_tasks', 'ifrs_accounting_calculate', 'ifrs_document_request_review', 'ifrs_document_contribute', 'ifrs_contribution_resolve', 'ifrs_observation_assess', 'ifrs_assessment_resolve']}})
    gateway_token = os.environ.get(args.gateway_token_env)
    if gateway_token:
        write(args.state / "worker.json", dict(stateDirectory=str(args.state.resolve() / "worker"), documentURL=args.document_url, gatewayURL=args.gateway_url,
              gatewayToken=gateway_token, operatorToken=next(p["token"] for p in principals if p["role"] == "operator"), context=context,
              assetRoutes={aid: by_machine[mid] for aid, mid in graph["assetStewards"].items()}))
    print(json.dumps({"agents": len(agents), "assetRoutes": len(graph["assetStewards"]), "scope": scope, "stateDirectory": str(args.state), "runningGatewayModified": False}))


if __name__ == "__main__":
    main()
