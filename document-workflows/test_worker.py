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
    assert len(result["attempts"]) == 3
    assert result["automaticApproval"] is False
    prompt = next(c for c in calls if "messages" in c)
    assert prompt["user"].startswith("ifrs-document-task-")
    assert "fixture-operator-secret" not in json.dumps(prompt)
    sessions = [c["user"] for c in calls if "messages" in c]
    assert len(sessions) == len(set(sessions)) == 3


def test_reporting_worker_requires_server_typed_provenance(tmp_path):
    for typed in [False, True]:
        state = {"changed": False}
        def transport(request):
            body = json.loads(request.content)
            if request.url.path == "/v1/chat/completions":
                assert "ifrs_reporting_draft" in body["messages"][0]["content"]
                state["changed"] = True
                return httpx.Response(200, json={"choices": [{"message": {"content": "Synthetic draft observed"}}]})
            op = body["operation"]
            if op == "tasks":
                return httpx.Response(200, json={"tasks": [dict(taskId="reporting-task", assetTypeId="DIVIDEND-REGISTER", status="OPEN", action="CREATE_DRAFT_AND_REQUEST_EVIDENCE")]})
            if op == "retrieve": return httpx.Response(200, json={"interactionId": "synthetic-source"})
            if op == "calculate": return httpx.Response(200, json={"result": {"caseId": "fixture-case", "determination": {"status": "ADVISORY", "documentAssetTypeId": "DIVIDEND-REGISTER"}}})
            content = {"typedProducerReceipt": dict(caseId="fixture-case", sourceInteractionId="synthetic-source")} if typed else {"narrative": "claimed draft"}
            return httpx.Response(200, json={"head": dict(revision=1, status="DRAFT", contentHash="synthetic-hash") if state["changed"] else None, "revision": {"content": content}})
        config = dict(stateDirectory=str(tmp_path / str(typed)), documentURL="http://fixture-doc", gatewayURL="http://fixture-gateway", gatewayToken="synthetic-secret", operatorToken="synthetic-operator", context=dict(entityId="SYNTHETIC-A", period="2027", factsByCase={"fixture-case": {"control": "section-6-dividend-note", "facts": {"evidenceRefs": ["synthetic:record"]}}}), assetRoutes={"DIVIDEND-REGISTER": {"agentId": "reporting-fixture", "token": "synthetic-agent", "caseId": "fixture-case"}})
        result = worker.dispatch_once(config, httpx.Client(transport=httpx.MockTransport(transport)))
        assert result["attempts"][0]["status"] == ("ARTIFACT_OBSERVED_REVIEW_REQUIRED" if typed else "FAILED")
