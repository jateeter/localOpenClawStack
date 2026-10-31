"""Strict, separate document permissions derived from canonical accounting scope."""
import json
import hashlib
from pathlib import Path

OPERATIONS = ["inspect-current-head", "assess-document-impact", "resolve-owned-assessment",
              "request-authentic-evidence", "submit-contribution", "resolve-owned-contribution", "calculate-reviewed-controls", "create-draft-for-owned-type",
              "revise-owned-draft", "reconcile-and-invalidate", "request-independent-review"]
FORBIDDEN = ["approve-own-or-other-draft", "forge-issuer-evidence", "post-journal", "file-report", "publish", "release-financial-statements"]


def derive(machine_path, metadata):
    duty = metadata.get("documentAssetWorkflow")
    if not duty:
        return {}
    root = Path(machine_path).resolve().parents[3] / "machine_factory/domains/ifrs-accounting"
    assets = {a["assetTypeId"]: a for a in json.loads((root / "document-asset-catalog.json").read_text())["assetTypes"]}
    resolver = json.loads((root / "runbook-resolution.json").read_text())
    if not resolver:
        raise ValueError("Missing runbook resolution")
    runbook = resolver.get("runbooks", {}).get(metadata["governance"]["runbook"])
    if not runbook:
        raise ValueError("Unknown versioned runbook")
    if hashlib.sha256((root / runbook["path"]).read_bytes()).hexdigest() != runbook["sha256"]:
        raise ValueError("Runbook hash mismatch")
    family = duty["familyId"]
    references = {d["assetTypeId"] for d in duty["duties"]} | set(metadata.get("documentStewardship", []))
    if not references <= assets.keys():
        raise ValueError("Unknown document asset reference")
    owned = sorted(a for a in references if assets[a]["familyId"] == family)
    for aid in references:
        if not (root / "templates" / (aid + ".json")).is_file():
            raise ValueError("Missing template " + aid)
    return {"documentAssetWorkflow": duty, "documentWorkflowBinding": {
        "contractRef": duty["contractRef"], "mode": "draft-and-maintain",
        "familyId": family, "actorOwnerName": metadata["governance"]["ownerTeam"],
        "ownedAssetTypeIds": owned, "requestOnlyAssetTypeIds": sorted(references - set(owned)),
        "allowedOperations": OPERATIONS, "forbiddenOperations": FORBIDDEN,
        "outputChannel": "document-task-and-artifact-sidecar", "nativeCompletionContractUnchanged": True}}
