"""Every IFRS sidecar is reproducible and preserves the native analyst channel."""
import json
import os
from pathlib import Path
import sys

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "machine-behaviors"))
import oc_agent_template
import document_binding
from derive_agents import load_config


def test_all_ifrs_bindings_match_catalog_and_native_channel_is_unchanged(monkeypatch):
    root = Path(os.environ.get("IFRS_TEST_MACHINES_ROOT", HERE.parent / "RealityEngine_Machines"))
    catalog = json.loads((root / "machine_factory/domains/ifrs-accounting/document-asset-catalog.json").read_text())["assetTypes"]
    assets = {a["assetTypeId"]: a for a in catalog}
    schema = json.loads((HERE / "machine-behaviors/templates/oc-agent.schema.json").read_text())
    offline = {}
    for path in (root / "schemas").glob("*.json"):
        document = json.loads(path.read_text())
        offline[path.as_uri()] = document
        if document.get("$id"):
            offline[document["$id"]] = document
        offline["https://realityengine.example.org/RealityEngine_Machines/schemas/" + path.name] = document
        offline["https://realityengine.example.org/schemas/" + path.name] = document
    offline_registry = Registry().with_resources((uri, Resource.from_contents(document, default_specification=DRAFT202012)) for uri, document in offline.items())
    cfg = load_config(); cfg["machinesDir"] = str(root / "machines")
    covered = set()
    for path in sorted((root / "machines/domains/ifrs-accounting").glob("*.json")):
        spec = oc_agent_template.derive(path, cfg)
        jsonschema.Draft202012Validator(schema, registry=offline_registry).validate(spec)
        binding = spec["documentWorkflowBinding"]
        assert spec["agentBinding"]["mode"] == "advise"
        assert len(spec["agentId"]) <= 64
        for aid in binding["ownedAssetTypeIds"]:
            assert assets[aid]["familyId"] == binding["familyId"]
            covered.add(aid)
        with monkeypatch.context() as context:
            context.setattr(document_binding, "derive", lambda *args: {})
            legacy = oc_agent_template.derive(path, cfg)
        assert {k: v for k, v in spec.items() if k not in {"documentWorkflowBinding", "documentAssetWorkflow"}} == legacy
    assert covered == set(assets)
