"""Mechanical synthetic integration cases, never authentic accounting acceptance."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import sys

from fastapi.testclient import TestClient
import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from service import Command, WorkflowService, create_app

ROOT = Path(os.environ.get("IFRS_TEST_MACHINES_ROOT", Path(__file__).resolve().parents[2] / "RealityEngine_Machines"))
ASSETS = ROOT / "machine_factory/domains/ifrs-accounting"
CONTEXT = dict(entityId="SYNTHETIC-ENTITY-A", period="2026", evidenceClass="synthetic", factsFingerprint="fixture-facts-v1", sourceFingerprint="fixture-source", policyFingerprint="fixture-policy", indexSnapshot="fixture-index", unresolvedRequirements=["synthetic fixture is not accounting acceptance"], evidenceRefAllowlist=["synthetic:record-not-authentic", "synthetic:new", "synthetic:record"])
TEXT = "Synthetic passage fixture. Not normative accounting text."
PASSAGE = dict(chunkId="fixture-chunk", text=TEXT, textSHA256=hashlib.sha256(TEXT.encode()).hexdigest(), sourceSHA256="fixture-source-hash")


def rag_transport(request):
    return httpx.Response(200, json=dict(interactionId="fixture-interaction", indexSnapshot="fixture-index", sourceFingerprint="fixture-source", request=dict(domain="ifrs-accounting", documentRole="standard_source", sectionId="01", question="fixture", edition="February 2025 third edition"), passages=[{**PASSAGE, "sectionId": "01"}]))


@pytest.fixture
def service(tmp_path):
    return WorkflowService(ASSETS, ROOT / "machines/domains/ifrs-accounting", tmp_path, [], "http://synthetic-rag", "fixture-secret", httpx.Client(transport=httpx.MockTransport(rag_transport)))


def principal(service, asset="SOURCE-REGISTER", role="agent"):
    return dict(name=service.store.catalog[asset]["reviewerName"] if role == "reviewer" else "synthetic-" + role,
                role=role, scopes=[[CONTEXT["entityId"], CONTEXT["period"]]], machineId=service.graph["assetStewards"][asset])


def call(service, op, args, actor=None, context=None, dispatch=None):
    import uuid
    return service.execute(Command(dispatchId=dispatch or str(uuid.uuid4()), operation=op, context=context or CONTEXT, arguments=args), actor or principal(service))


def open_period(service):
    call(service, "event", {"event": dict(type="period-opened", entityId=CONTEXT["entityId"], period=CONTEXT["period"], eventId="fixture-open"), "applicableAssetTypeIds": ["SOURCE-REGISTER", "RAG-RECEIPTS"]}, principal(service, role="operator"))


def draft(service, asset="SOURCE-REGISTER", revision=0, change=""):
    fields = {key: ["synthetic fixture value"] for key in service.store.catalog[asset]["requiredFields"]}
    if "unresolved" in fields:
        fields["unresolved"] = []
    return call(service, "draft", dict(assetTypeId=asset, content=fields, evidenceRefs=["synthetic:record-not-authentic"], expectedRevision=revision, changeReason=change), principal(service, asset))["result"]


def approve(service, asset, head):
    call(service, "request-review", dict(assetTypeId=asset, expectedRevision=head["revision"]), principal(service, asset))
    review = dict(reviewerName="ignored-client-claim", ownerName=service.store.catalog[asset]["ownerName"], evidenceChecked=True, citationsChecked=True, reconciliationsChecked=True, unresolvedItems=[], revision=head["revision"], contentHash=head["contentHash"], ragReceipt=dict(interactionId="fixture-interaction", sourceHash="fixture-source-hash", sourceFingerprint="fixture-source", indexSnapshot="fixture-index", verifiedPassageRefs=["fixture-chunk"], citationVerified=True))
    return call(service, "approve", dict(assetTypeId=asset, review=review, interactionId="fixture-interaction"), principal(service, asset, "reviewer"))


def test_transactional_lifecycle_exact_review_and_downstream_invalidation(service):
    open_period(service)
    source = draft(service)
    approve(service, "SOURCE-REGISTER", source)
    rag = draft(service, "RAG-RECEIPTS")
    approve(service, "RAG-RECEIPTS", rag)
    changed = call(service, "draft", dict(assetTypeId="SOURCE-REGISTER", content={k: ["changed fixture"] for k in service.store.catalog["SOURCE-REGISTER"]["requiredFields"]}, evidenceRefs=["synthetic:new"], expectedRevision=1, changeReason="changed source"))["result"]
    assert changed["revision"] == 2
    assert service.store.head(CONTEXT, "RAG-RECEIPTS")["status"] == "STALE"
    assert service.store.revision(CONTEXT, "SOURCE-REGISTER", 1)["contentHash"] == source["contentHash"]
    with pytest.raises(ValueError):
        approve(service, "SOURCE-REGISTER", source)
    exported = call(service, "export", {}, principal(service, role="projection"))
    assert all(e["synthetic"] and e["evidenceClass"] == "synthetic" for e in exported["events"])


def test_denies_self_review_unowned_mutation_wrong_scope_and_fabricated_context(service):
    open_period(service)
    head = draft(service)
    with pytest.raises(PermissionError):
        call(service, "approve", dict(assetTypeId="SOURCE-REGISTER", review={}, interactionId="fixture"))
    for op in ["post-journal", "file-report", "release-financial-statements", "event"]:
        with pytest.raises(PermissionError):
            call(service, op, {})
    with pytest.raises(PermissionError):
        call(service, "draft", dict(assetTypeId="LEASE-SCHEDULE", content={}, evidenceRefs=[]))
    with pytest.raises(PermissionError):
        call(service, "inspect", dict(assetTypeId="SOURCE-REGISTER"), context={**CONTEXT, "entityId": "other"})
    with pytest.raises(ValueError, match="operator-pinned"):
        call(service, "inspect", dict(assetTypeId="SOURCE-REGISTER"), context={**CONTEXT, "factsFingerprint": "invented"})


def test_observation_assesses_only_and_contribution_routes_to_declared_steward(service):
    open_period(service); source = draft(service); approve(service, "SOURCE-REGISTER", source)
    actor = principal(service)
    machine = json.loads((service.machines / (actor["machineId"] + ".json")).read_text())["machine"]
    seq = machine["sequences"][0]
    call(service, "observe", {"observation": dict(machineId=machine["id"], sequenceId=seq["id"], outputVector=[.5, 0, 0, 0], providerRef="synthetic-provider")})
    assert service.store.head(CONTEXT, "SOURCE-REGISTER")["status"] == "APPROVED"
    task = call(service, "contribute", dict(assetTypeId="SOURCE-REGISTER", content={"finding": "unresolved synthetic fixture"}, evidenceRefs=["synthetic:record"]))
    assert task["result"]["targetMachine"] == service.graph["assetStewards"]["SOURCE-REGISTER"]
    assert task["nativeWrites"] == 0
    retry = call(service, "contribute", dict(assetTypeId="SOURCE-REGISTER", content={"finding": "unresolved synthetic fixture"}, evidenceRefs=["synthetic:record"]))
    assert retry["result"]["contributionId"] == task["result"]["contributionId"]
    with pytest.raises(ValueError, match="does not reference"):
        call(service, "resolve-contribution", dict(contributionId=task["result"]["contributionId"], revision=source["revision"], contentHash=source["contentHash"]))


def test_durable_retry_concurrency_and_cas(service):
    open_period(service)
    args = dict(assetTypeId="SOURCE-REGISTER", content={"edition": "synthetic"}, evidenceRefs=["synthetic:record"], expectedRevision=0)
    def run(_):
        return call(service, "draft", args, dispatch="same-dispatch")
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        receipts = list(pool.map(run, range(3)))
    assert receipts[0] == receipts[1] == receipts[2]
    assert service.store.head(CONTEXT, "SOURCE-REGISTER")["revision"] == 1
    with pytest.raises(ValueError):
        call(service, "draft", {**args, "content": {"edition": "different"}}, dispatch="same-dispatch")
    with pytest.raises(ValueError):
        call(service, "draft", {**args, "content": {"edition": "different"}, "changeReason": "update"})
    reopened = WorkflowService(service.assets, service.machines, service.store.state, [], "http://synthetic-rag", "fixture-secret")
    assert reopened.store.head(CONTEXT, "SOURCE-REGISTER")["revision"] == 1


def test_http_auth_and_typed_amount_guards(service):
    open_period(service)
    service.principals = [{**principal(service), "token": "synthetic-private-token"}]
    client = TestClient(create_app(service))
    command = dict(dispatchId="fixture-http", operation="draft", context=CONTEXT,
                   arguments=dict(assetTypeId="SOURCE-REGISTER", content={"balance": {"amount": .5, "currency": "USD", "asOf": "2026-12-31"}}, evidenceRefs=["synthetic:record"]))
    assert client.post("/tools/execute", json=command).status_code == 403
    assert client.post("/tools/execute", json=command, headers={"Authorization": "Bearer synthetic-private-token"}).status_code == 409


def test_index_version_change_invalidates_prior_approval_and_foreign_evidence_is_denied(service):
    open_period(service); head = draft(service); approve(service, "SOURCE-REGISTER", head)
    changed = {**CONTEXT, "indexSnapshot": "fixture-index-v2"}
    call(service, "event", {"event": dict(type="period-close-requested", entityId=CONTEXT["entityId"], period=CONTEXT["period"]), "applicableAssetTypeIds": ["SOURCE-REGISTER"]}, principal(service, role="operator"), context=changed)
    assert service.store.head(changed, "SOURCE-REGISTER")["status"] == "STALE"
    with pytest.raises(ValueError, match="Evidence reference"):
        call(service, "draft", dict(assetTypeId="SOURCE-REGISTER", content={"edition": "fixture"}, evidenceRefs=["synthetic:another-entity"], expectedRevision=1, changeReason="bad evidence"), context=changed)


def test_resource_delivery_is_bounded_and_unresolved_placeholders_cannot_be_approved(service):
    open_period(service)
    resource = call(service, "resource", {"assetTypeId": "ELIGIBILITY-MEMO"})
    assert "text" not in resource["runbook"]
    assert resource["sourceControls"]
    assert len(json.dumps(resource)) < 20000
    fields = {key: ["synthetic fixture value"] for key in service.store.catalog["SOURCE-REGISTER"]["requiredFields"]}
    fields["edition"] = "{}"
    head = call(service, "draft", dict(assetTypeId="SOURCE-REGISTER", content=fields, evidenceRefs=["synthetic:record"], expectedRevision=0))["result"]
    with pytest.raises(ValueError, match="Unresolved required field"):
        approve(service, "SOURCE-REGISTER", head)


def test_typed_eligibility_renderer_uses_pinned_facts_and_keeps_missing_profile_fields_unresolved(service):
    context = {**CONTEXT, "factsByCase": {"fixture-case": {"control": "section-1-eligibility", "facts": {"publicAccountability": False, "generalPurposeStatements": True, "jurisdictionPermits": True, "evidenceRefs": ["synthetic:record"]}}},
               "evidenceRecords": [{"recordId": "synthetic:record", "entityId": CONTEXT["entityId"], "period": CONTEXT["period"], "synthetic": True, "profile": {"entityFacts": "Fictional private entity"}}]}
    call(service, "event", {"event": dict(type="period-opened", entityId=context["entityId"], period=context["period"]), "applicableAssetTypeIds": ["ELIGIBILITY-MEMO"]}, principal(service, role="operator"), context=context)
    head = call(service, "draft-from-case", dict(assetTypeId="ELIGIBILITY-MEMO", caseId="fixture-case", interactionId="fixture-interaction", expectedRevision=0), principal(service, "ELIGIBILITY-MEMO"), context=context)["result"]
    revision = service.store.revision(context, "ELIGIBILITY-MEMO", head["revision"])
    assert head["status"] == "DRAFT"
    assert revision["content"]["conclusion"] == "ELIGIBLE_SUBJECT_TO_REVIEW"
    assert revision["content"]["publicMarkets"] is None
    assert "publicMarkets" in revision["content"]["unresolved"]


@pytest.mark.parametrize("control,section,facts,expected", [
    ("version-3-period-applicability", "A", dict(edition="IFRS_FOR_SMES_2025_THIRD_EDITION", annualPeriod=True, periodStart="2027-01-01", periodEnd="2027-12-31"), "EFFECTIVE_PERIOD_SUBJECT_TO_REVIEW"),
    ("section-2-recognition-screen", "02", dict(element="asset", specificSectionApplies=False, definitionMet=True, relevantInformation=True, faithfulRepresentation=True, uncertaintyAssessed=True), "GENERAL_RECOGNITION_SCREEN_PASSED_SUBJECT_TO_REVIEW"),
    ("section-2-substance-screen", "02", dict(legalFormDiffersFromSubstance=True, substanceRepresented=True, faithfulRepresentationAssessed=True), "SUBSTANCE_SCREEN_PASSED_SUBJECT_TO_REVIEW"),
])
def test_foundation_calculation_requires_matching_source_scope(service, control, section, facts, expected):
    # Synthetic protocol proof only: these passages are not normative source receipts.
    context = {**CONTEXT, "factsByCase": {"foundation": {"control": control, "facts": {**facts, "evidenceRefs": ["synthetic:record"]}}}}
    call(service, "event", {"event": dict(type="period-opened", entityId=context["entityId"], period=context["period"]), "applicableAssetTypeIds": ["SOURCE-REGISTER"]}, principal(service, role="operator"), context=context)
    args = dict(caseId="foundation", interactionId="fixture-interaction")
    with pytest.raises(ValueError, match="source scope"):
        call(service, "calculate", args, context=context)
    def transport(request):
        receipt = rag_transport(request).json()
        receipt["request"]["sectionId"] = section
        receipt["passages"][0]["sectionId"] = section
        return httpx.Response(200, json=receipt)
    service.http = httpx.Client(transport=httpx.MockTransport(transport))
    result = call(service, "calculate", args, context=context)["result"]
    assert result["determination"]["decision"] == expected
    assert result["reviewRequired"] is True
    assert result["nativeProjection"] == "NOT_AUTOMATIC"


@pytest.mark.parametrize("case", (json.loads((ASSETS / "acceptance/synthetic/policy-cases.json").read_text())["cases"] + json.loads((ASSETS / "acceptance/synthetic/lease-compliance-cases.json").read_text())["cases"] + json.loads((ASSETS / "acceptance/synthetic/source-disclosure-cases.json").read_text())["cases"] + json.loads((ASSETS / "acceptance/synthetic/reporting-cases.json").read_text())["cases"] + json.loads((ASSETS / "acceptance/synthetic/remaining-cases.json").read_text())["cases"]), ids=lambda c: c["caseId"])
def test_policy_calculation_alternatives_use_scoped_receipts(service, case):
    context = {**CONTEXT, "period": case["period"], "evidenceRefAllowlist": CONTEXT["evidenceRefAllowlist"] + ["SYNTHETIC-POLICY-RECORD", "SYNTHETIC-LEASE-RECORD", "SYNTHETIC-SOURCE-RECORD", "SYNTHETIC-REPORTING-RECORD", "SYNTHETIC-COMPLETION-RECORD"],
               "factsByCase": {case["caseId"]: {"control": case["control"], "facts": case["facts"]}}}
    operator = principal(service, role="operator"); operator["scopes"] = [[context["entityId"], context["period"]]]
    agent = principal(service); agent["scopes"] = operator["scopes"]
    call(service, "event", {"event": dict(type="period-opened", entityId=context["entityId"], period=context["period"]), "applicableAssetTypeIds": ["SOURCE-REGISTER"]}, operator, context=context)
    def transport(section):
        def respond(request):
            receipt = rag_transport(request).json()
            actual = json.loads(request.content).get("sectionId", section) if request.method == "POST" else request.url.path.rsplit("fixture-dep-", 1)[-1] if "fixture-dep-" in request.url.path else section
            receipt["request"]["sectionId"] = actual
            receipt["passages"][0]["sectionId"] = actual
            return httpx.Response(200, json=receipt)
        return httpx.MockTransport(respond)
    args = dict(caseId=case["caseId"], interactionId="fixture-interaction", dependencyInteractionIds=["fixture-dep-"+section for section in case.get("dependencySections", []) if section != case["sourceSection"]])
    service.http = httpx.Client(transport=transport("01" if case["sourceSection"] == "03" else "03"))
    with pytest.raises(ValueError, match="source scope"):
        call(service, "calculate", args, agent, context=context)
    service.http = httpx.Client(transport=transport(case["sourceSection"]))
    if case.get("dependencySections") and case["expected"]["status"] == "ADVISORY" and any(s != case["sourceSection"] for s in case["dependencySections"]):
        with pytest.raises(ValueError, match="dependency-section"):
            call(service, "calculate", dict(args, dependencyInteractionIds=[]), agent, context=context)
    result = call(service, "calculate", args, agent, context=context)["result"]
    for key, expected in case["expected"].items():
        assert result["determination"][key] == expected
    assert result["reviewRequired"] is True
    assert result["nativeProjection"] == "NOT_AUTOMATIC"


@pytest.mark.parametrize("case", [c for c in json.loads((ASSETS / "acceptance/synthetic/reporting-cases.json").read_text())["cases"] if c["control"] != "section-8-note-structure-screen"], ids=lambda c: c["caseId"])
def test_owned_typed_reporting_drafts_and_unresolved_rejection(service, case):
    targets = {"section-6-dividend-note": "DIVIDEND-REGISTER", "section-7-financing-reconciliation": "FINANCING-RECONCILIATION", "section-7-supplier-finance-note": "SUPPLIER-FINANCE-NOTE"}
    aid = targets[case["control"]]
    context = {**CONTEXT, "period": case["period"], "evidenceRefAllowlist": ["SYNTHETIC-REPORTING-RECORD", "SYNTHETIC-COMPLETION-RECORD"],
               "factsByCase": {case["caseId"]: {"control": case["control"], "facts": case["facts"]}},
               "evidenceRecords": [dict(recordId="SYNTHETIC-REPORTING-RECORD", entityId=CONTEXT["entityId"], period=case["period"], synthetic=True)]}
    actor = principal(service, aid); actor["scopes"] = [[context["entityId"], context["period"]]]
    operator = principal(service, role="operator"); operator["scopes"] = actor["scopes"]
    call(service, "event", {"event": dict(type="period-opened", entityId=context["entityId"], period=context["period"]), "applicableAssetTypeIds": [aid]}, operator, context=context)
    def transport(request):
        receipt = rag_transport(request).json(); receipt["request"]["sectionId"] = case["sourceSection"]; receipt["passages"][0]["sectionId"] = case["sourceSection"]
        return httpx.Response(200, json=receipt)
    service.http = httpx.Client(transport=httpx.MockTransport(transport))
    args = dict(assetTypeId=aid, caseId=case["caseId"], interactionId="fixture-interaction", expectedRevision=0)
    if case["expected"]["status"] == "UNRESOLVED":
        with pytest.raises(ValueError, match="Unresolved reporting"):
            call(service, "draft-from-case", args, actor, context=context)
        assert service.store.head(context, aid) is None
    else:
        head = call(service, "draft-from-case", args, actor, context=context)["result"]
        revision = service.store.revision(context, aid, head["revision"])
        assert head["status"] == "DRAFT"
        assert revision["content"]["typedProducerReceipt"]["caseId"] == case["caseId"]
        assert revision["content"]["unresolved"] == []
        assert set(service.store.catalog[aid]["requiredFields"]) <= set(revision["content"])
        with pytest.raises(ValueError, match="server-owned"):
            call(service, "draft", dict(assetTypeId=aid, content=revision["content"], evidenceRefs=["SYNTHETIC-REPORTING-RECORD", "SYNTHETIC-COMPLETION-RECORD"], expectedRevision=1), actor, context=context)
        # The owner cannot review its own financial draft.
        with pytest.raises(PermissionError):
            call(service, "approve", dict(assetTypeId=aid), actor, context=context)


def test_declared_steward_can_contribute_without_legacy_duty_but_foreign_agent_cannot(service):
    open_period(service)
    aid = "REVENUE-GUIDANCE-MEMO"
    owner = principal(service, aid)
    machine = json.loads((service.machines / (owner["machineId"] + ".json")).read_text())["machine"]
    assert aid in machine["metadata"]["documentStewardship"]
    assert aid not in {d["assetTypeId"] for d in machine["metadata"]["documentAssetWorkflow"]["duties"]}
    args = dict(assetTypeId=aid, content={"finding": "synthetic conditional judgment"}, evidenceRefs=["synthetic:record"])
    result = call(service, "contribute", args, owner)
    assert result["result"]["status"] == "OPEN"
    assert result["result"]["targetMachine"] == owner["machineId"]
    assert result["nativeWrites"] == 0
    with pytest.raises(PermissionError):
        call(service, "contribute", args, principal(service, "ELIGIBILITY-MEMO"))
