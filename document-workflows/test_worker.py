import importlib.util
import json
from pathlib import Path

import httpx

spec = importlib.util.spec_from_file_location("ifrs_worker", Path(__file__).parent / "worker.py")
worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)


def test_model_narrative_does_not_complete_work(tmp_path):
    calls = []
    def transport(request):
        body = json.loads(request.content)
        calls.append(body)
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(200, json={"choices": [{"message": {"content": "Everything is approved"}}]})
        if body["operation"] == "tasks":
            return httpx.Response(200, json={"tasks": [{"taskId": "synthetic-task", "assetTypeId": "SOURCE-REGISTER", "status": "OPEN", "action": "CREATE_DRAFT_AND_REQUEST_EVIDENCE"}]})
        return httpx.Response(200, json={"head": None})
    config = dict(stateDirectory=str(tmp_path), documentURL="http://fixture-doc", gatewayURL="http://fixture-gateway", gatewayToken="fixture-gateway-secret", operatorToken="fixture-operator-secret", context={"entityId": "SYNTHETIC-A", "period": "2027"}, assetRoutes={"SOURCE-REGISTER": {"agentId": "fixture-agent", "token": "fixture-agent-secret"}})
    result = worker.dispatch(config, httpx.Client(transport=httpx.MockTransport(transport)))
    assert result["attempts"][0]["status"] == "UNRESOLVED_NO_ARTIFACT_CHANGE"
    assert result["automaticApproval"] is False
    prompt = next(c for c in calls if "messages" in c)
    assert prompt["user"].startswith("ifrs-document-task-")
    assert "fixture-operator-secret" not in json.dumps(prompt)
