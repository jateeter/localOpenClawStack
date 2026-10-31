"""IFRS document sidecar: authenticated tools and transactional canonical store.

The Machines reference supplies domain lifecycle rules. This boundary adds
durability, server-owned identities/scopes, immutable dispatch receipts, verified
RAG interactions and a pull outbox. It never sends native PE completions.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import threading
import uuid
from decimal import Decimal, InvalidOperation

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def validate_quantities(value):
    if isinstance(value, list):
        for item in value:
            validate_quantities(item)
    elif isinstance(value, dict):
        if "amount" in value:
            if not isinstance(value["amount"], str) or not value.get("currency") or not value.get("asOf"):
                raise ValueError("Amounts require decimal string, currency and asOf")
            try:
                if not Decimal(value["amount"]).is_finite():
                    raise ValueError("Non-finite monetary amount")
            except InvalidOperation as exc:
                raise ValueError("Invalid decimal monetary amount") from exc
        for item in value.values():
            validate_quantities(item)


def load_lifecycle(assets):
    path = Path(assets) / "tools/document_workflow.py"
    spec = importlib.util.spec_from_file_location("ifrs_lifecycle", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def transactional_store(assets, state):
    rules = load_lifecycle(assets)

    class Store(rules.Store):
        def __init__(self):
            self.local = threading.local()
            self.state = Path(state)
            self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self.db() as db:
                db.execute("CREATE TABLE IF NOT EXISTS objects (key TEXT PRIMARY KEY, body TEXT NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS dispatch (id TEXT PRIMARY KEY, request_hash TEXT, body TEXT)")
                db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
                db.execute("INSERT OR IGNORE INTO metadata VALUES('storeId',?)", (str(uuid.uuid4()),))
                self.store_id = db.execute("SELECT value FROM metadata WHERE key='storeId'").fetchone()[0]
            super().__init__(self.state, Path(assets) / "document-asset-catalog.json")

        def db(self):
            db = sqlite3.connect(self.state / "documents.sqlite", timeout=60)
            db.execute("PRAGMA synchronous=FULL")
            return db

        @contextlib.contextmanager
        def locked(self):
            if getattr(self.local, "db", None):
                yield
                return
            db = self.db()
            try:
                db.execute("BEGIN IMMEDIATE")
                self.local.db = db
                yield
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                self.local.db = None
                db.close()

        @contextlib.contextmanager
        def connection(self):
            db = getattr(self.local, "db", None)
            if db is not None:
                yield db
            else:
                with contextlib.closing(self.db()) as db:
                    yield db

        def _read(self, path, default=None):
            with self.connection() as db:
                row = db.execute("SELECT body FROM objects WHERE key=?", (str(path.relative_to(self.root)),)).fetchone()
            return json.loads(row[0]) if row else default

        def _write(self, path, data):
            db = getattr(self.local, "db", None)
            if db is None:
                raise RuntimeError("Writes require a transaction")
            key = str(path.relative_to(self.root))
            if path.name.startswith("v") and db.execute("SELECT 1 FROM objects WHERE key=?", (key,)).fetchone():
                raise rules.ConflictError("Immutable revision already exists")
            db.execute("INSERT OR REPLACE INTO objects VALUES(?,?)", (key, canonical(data)))

        def _event(self, data):
            self.local.db.execute("INSERT INTO events(body) VALUES(?)", (canonical(data),))

    return Store(), rules


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dispatchId: str
    operation: str
    context: dict
    arguments: dict = {}


class WorkflowService:
    def __init__(self, assets, machines, state, principals, rag_url, rag_token, http=None):
        self.assets, self.machines = Path(assets), Path(machines)
        self.store, self.rules = transactional_store(assets, state)
        self.principals = principals
        self.rag_url, self.rag_token = rag_url.rstrip("/"), rag_token
        self.http = http or httpx.Client(timeout=60)
        self.graph = json.loads((self.assets / "accounting-workflow-graph.json").read_text())

    def principal(self, authorization):
        for item in self.principals:
            if hmac.compare_digest(authorization, "Bearer " + item["token"]):
                return {k: v for k, v in item.items() if k != "token"}
        raise PermissionError("Invalid workflow credential")

    def rag(self, path, body=None):
        kwargs = {"headers": {"Authorization": "Bearer " + self.rag_token}}
        response = self.http.get(self.rag_url + path, **kwargs) if body is None else self.http.post(self.rag_url + path, json=body, **kwargs)
        response.raise_for_status()
        return response.json()

    def verify_rag(self, context, interaction):
        receipt = self.rag("/rag/evidence/receipts/" + interaction)
        req = receipt["request"]
        if req["domain"] != "ifrs-accounting" or req["documentRole"] != "standard_source" or not receipt["passages"]:
            raise ValueError("Standard passages required")
        if receipt["indexSnapshot"] != context["indexSnapshot"]:
            raise ValueError("RAG index context mismatch")
        if receipt["sourceFingerprint"] != context["sourceFingerprint"]:
            raise ValueError("RAG source context mismatch")
        # Revalidate the committed index now; old receipts do not grant freshness.
        current = self.rag("/rag/evidence/retrieve", {**req, "indexSnapshot": receipt["indexSnapshot"]})
        if not current["passages"]:
            raise ValueError("Source no longer retrievable")
        for p in receipt["passages"]:
            if hashlib.sha256(p["text"].encode()).hexdigest() != p["textSHA256"]:
                raise ValueError("Passage hash mismatch")
        return receipt

    def execute(self, command, principal):
        c = command.context
        if [c.get("entityId"), c.get("period")] not in principal.get("scopes", []):
            raise PermissionError("Wrong entity/period")
        self.rules.scope(c)
        if c.get("evidenceClass") not in {"synthetic", "actual"}:
            raise ValueError("An explicit evidence class is required")
        op, args = command.operation, dict(command.arguments)
        if args.get("evidenceClass", c["evidenceClass"]) != c["evidenceClass"]:
            raise PermissionError("Evidence class cannot expand the operator-pinned context")
        role = principal["role"]
        mid = principal.get("machineId")
        m = None
        if role == "agent":
            if not mid or Path(mid).name != mid:
                raise PermissionError("Invalid machine identity")
            m = json.loads((self.machines / (mid + ".json")).read_text())["machine"]
            family = m["metadata"]["documentAssetWorkflow"]["familyId"]
            references = {d["assetTypeId"] for d in m["metadata"]["documentAssetWorkflow"]["duties"]} | set(m["metadata"].get("documentStewardship", []))
            owned = {aid for aid, asset in self.store.catalog.items() if asset["familyId"] == family and aid in references}
            aid = args.get("assetTypeId")
            allowed = {"inspect", "tasks", "retrieve", "calculate", "resource", "observe", "contribute", "resolve-contribution", "draft", "draft-from-case", "request-review", "resolve-assessment"}
            if op not in allowed or (op in {"draft", "draft-from-case", "request-review"} and aid not in owned):
                raise PermissionError("Operation or asset is outside agent document binding")
            if op == "resolve-assessment":
                task = self.store._read(self.store.root / "outbox.json", {}).get(args["taskId"], {})
                if task.get("assetTypeId") not in owned:
                    raise PermissionError("Unowned assessment")
                args["resolution"]["ownerName"] = self.store.catalog[task["assetTypeId"]]["ownerName"]
        elif role not in {"reviewer", "operator", "projection"}:
            raise PermissionError("Unknown role")
        if op in {"approve", "reject"} and role != "reviewer":
            raise PermissionError("Only the independent reviewer can review")
        if op == "event" and role != "operator":
            raise PermissionError("Only the operator may assert verified input changes")
        if role == "projection" and op not in {"inspect", "export"}:
            raise PermissionError("Projection is read-only")
        if role == "reviewer" and op not in {"inspect", "approve", "reject", "retrieve", "resource"}:
            raise PermissionError("Reviewer cannot draft or change facts")
        context_key = self.store.root / "contexts" / self.rules.asset_key(c, "CONTEXT")
        applicability_key = self.store.root / "applicability" / self.rules.asset_key(c, "CONTEXT")
        current_context = self.store._read(context_key)
        if op != "event" and (not current_context or current_context != c):
            raise ValueError("Context is not the current operator-pinned entity/period snapshot")
        if op in {"draft", "contribute"}:
            refs = args.get("evidenceRefs", [])
            allowed_refs = c.get("evidenceRefAllowlist", [])
            if not refs or any(not isinstance(ref, str) for ref in refs):
                raise ValueError("Evidence reference is absent from this operator-pinned entity/period")
            for ref in refs:
                if ref in allowed_refs:
                    continue
                # Newly retrieved source receipts are verified with the source
                # service, never accepted as fabricated issuer evidence.
                try:
                    uuid.UUID(ref)
                except ValueError as exc:
                    raise ValueError("Evidence reference is absent from this operator-pinned entity/period") from exc
                self.verify_rag(c, ref)
        if op == "retrieve":
            if args.get("domain", "ifrs-accounting") != "ifrs-accounting":
                raise PermissionError("Wrong domain")
            return self.rag("/rag/evidence/retrieve", {**args, "indexSnapshot": c["indexSnapshot"]})
        if op == "resource":
            aid = args["assetTypeId"]
            if aid not in self.store.catalog:
                raise ValueError("Unknown document asset")
            resolver = json.loads((self.assets / "runbook-resolution.json").read_text())["runbooks"]
            runbook_path = "runbooks/" + self.store.catalog[aid]["familyId"] + ".md"
            runbook_id, runbook = next((key, value) for key, value in resolver.items() if value["path"] == runbook_path)
            text = (self.assets / runbook_path).read_text()
            if hashlib.sha256(text.encode()).hexdigest() != runbook["sha256"]:
                raise ValueError("Runbook integrity mismatch")
            asset = self.store.catalog[aid]
            kind = args.get("resourceKind", "template")
            if kind == "runbook":
                return {"resourceId": runbook_id, "sha256": runbook["sha256"], "text": text}
            if kind == "controls":
                requested = args.get("controlIds", [])
                if not requested or not set(requested) <= {r["controlId"] for r in asset["sourceRequirements"]}:
                    raise ValueError("Choose known control IDs from the template resource")
                return {"assetTypeId": aid, "controls": [r for r in asset["sourceRequirements"] if r["controlId"] in requested]}
            if kind != "template":
                raise ValueError("Unknown resource kind")
            return {"asset": {k: asset[k] for k in ["assetTypeId", "title", "familyId", "ownerName", "reviewerName", "motivation", "requiredFields", "dependencies"]},
                    "template": json.loads((self.assets / "templates" / (aid + ".json")).read_text()),
                    "sourceControls": [{"controlId": r["controlId"], "paragraphs": r["paragraphs"]} for r in asset["sourceRequirements"]],
                    "runbook": {"resourceId": runbook_id, "sha256": runbook["sha256"], "fetch": {"assetTypeId": aid, "resourceKind": "runbook"}}}
        # Atomic idempotency covers state changes and dispatch response together.
        with self.store.locked():
            db = self.store.local.db
            if op != "event" and self.store._read(context_key) != c:
                raise self.rules.ConflictError("Context changed during dispatch")
            if op == "inspect":
                h = self.store.head(c, args["assetTypeId"])
                if h and h["inputFingerprint"] != self.rules.input_fingerprint(c, self.store._upstream(c, args["assetTypeId"])):
                    h = {**h, "status": "STALE", "review": None}
                self.store._event({"type": "DOCUMENT_TOOL_READ", "dispatchId": command.dispatchId, "principal": principal["name"], "assetTypeId": args["assetTypeId"], "entityId": c["entityId"], "period": c["period"]})
                visible_context = c if role != "agent" or args.get("includeFacts") is True else {k: c[k] for k in ["entityId", "period", "factsFingerprint", "sourceFingerprint", "policyFingerprint", "indexSnapshot"]}
                return {"head": h, "revision": self.store.revision(c, args["assetTypeId"], h["revision"]) if h else None, "context": visible_context, "canonicalStoreId": self.store.store_id}
            if op == "export":
                if role not in {"operator", "projection"}:
                    raise PermissionError("Canonical export requires projection/operator credential")
                sequence = db.execute("SELECT COALESCE(MAX(sequence),0) FROM events").fetchone()[0]
                result = []
                for asset in self.store.catalog:
                    h = self.store.head(c, asset)
                    if not h:
                        continue
                    if h["inputFingerprint"] != self.rules.input_fingerprint(c, self.store._upstream(c, asset)):
                        h = {**h, "status": "STALE", "review": None}
                    revision = self.store.revision(c, asset, h["revision"])
                    result.append({**h, "sequence": sequence, "canonicalStoreId": self.store.store_id, "synthetic": revision.get("evidenceClass") != "actual",
                                   "evidenceClass": revision.get("evidenceClass", "UNSPECIFIED"),
                                   "evidenceRefs": revision["evidenceRefs"],
                                   "sourceRefs": (h.get("review") or {}).get("ragReceipt", {}).get("verifiedPassageRefs", [])})
                return {"events": result, "sequence": sequence}
            if op == "tasks":
                tasks = self.store._read(self.store.root / "outbox.json", {})
                return {"tasks": [task for task in tasks.values() if task["entityId"] == c["entityId"] and task["period"] == c["period"] and (role == "operator" or task["assetTypeId"] in owned)],
                        "contributions": [json.loads(body) for (body,) in db.execute("SELECT body FROM objects WHERE key LIKE 'contributions/%'") if json.loads(body)["entityId"] == c["entityId"] and json.loads(body)["period"] == c["period"] and (role == "operator" or json.loads(body)["targetMachine"] == mid)]}
            key = principal["name"] + ":" + command.dispatchId
            request_hash = hashlib.sha256(canonical(command.model_dump()).encode()).hexdigest()
            old = db.execute("SELECT request_hash,body FROM dispatch WHERE id=?", (key,)).fetchone()
            if old:
                if old[0] != request_hash:
                    raise self.rules.ConflictError("Dispatch ID reused for another request")
                return json.loads(old[1])
            aid = args.get("assetTypeId")
            if op == "inspect":
                h = self.store.head(c, aid)
                result = {"head": h, "revision": self.store.revision(c, aid, h["revision"]) if h else None}
            elif op == "draft":
                if aid not in self.store._read(applicability_key, []):
                    raise ValueError("Asset applicability has not been operator-assessed")
                if "typedProducerReceipt" in args["content"]:
                    raise ValueError("Typed producer provenance is server-owned; use draft-from-case")
                validate_quantities(args["content"])
                result = self.store.draft(c, aid, args["content"], args["evidenceRefs"], args.get("expectedRevision", 0), args.get("changeReason", ""))
            elif op == "draft-from-case":
                reporting_targets = {"DIVIDEND-REGISTER": "section-6-dividend-note", "FINANCING-RECONCILIATION": "section-7-financing-reconciliation", "SUPPLIER-FINANCE-NOTE": "section-7-supplier-finance-note"}
                if aid not in {"ELIGIBILITY-MEMO", *reporting_targets} or aid not in self.store._read(applicability_key, []):
                    raise ValueError("Applicable supported typed document required")
                case = c.get("factsByCase", {}).get(args["caseId"])
                expected_control = reporting_targets.get(aid, "section-1-eligibility")
                if not case or case["control"] != expected_control:
                    raise ValueError("A matching operator-pinned typed case is required")
                refs = case["facts"].get("evidenceRefs", [])
                if not refs or not set(refs) <= set(c.get("evidenceRefAllowlist", [])):
                    raise ValueError("Case evidence is outside this entity/period")
                receipt = self.verify_rag(c, args["interactionId"])
                spec = importlib.util.spec_from_file_location("ifrs_controls", self.assets / "tools/accounting_controls.py")
                controls = importlib.util.module_from_spec(spec); spec.loader.exec_module(controls)
                section = controls.source_section(case["control"], case["facts"])
                if receipt["request"].get("sectionId") != section or any(p.get("sectionId") != section for p in receipt["passages"]):
                    raise ValueError("Typed document source scope does not match the control")
                determination = controls.determine(case["control"], case["facts"])
                records = [r for r in c.get("evidenceRecords", []) if r.get("recordId") in refs]
                if any(r.get("entityId") != c["entityId"] or r.get("period") != c["period"] for r in records):
                    raise ValueError("Record scope differs from the canonical document scope")
                if aid in reporting_targets:
                    if case["facts"].get("period") != c["period"] or set(refs) != {r.get("recordId") for r in records}:
                        raise ValueError("Reporting requires complete scoped supporting record bindings")
                    if determination.get("status") != "ADVISORY" or determination.get("documentAssetTypeId") != aid:
                        raise ValueError("Unresolved reporting facts cannot produce a financial draft")
                    content = dict(determination["documentContent"])
                    for field in self.store.catalog[aid]["requiredFields"]:
                        content.setdefault(field, None)
                    content["advisoryDetermination"] = {k: v for k, v in determination.items() if k != "documentContent"}
                    content["typedProducerReceipt"] = dict(caseId=args["caseId"], control=case["control"], sourceInteractionId=receipt["interactionId"],
                        implementationSHA256=hashlib.sha256((self.assets / "tools/reporting_controls.py").read_bytes()).hexdigest(), entityId=c["entityId"], period=c["period"], evidenceClass=c["evidenceClass"])
                    validate_quantities(content)
                else:
                    profile = next((r["profile"] for r in records if isinstance(r.get("profile"), dict)), {})
                    content = {key: profile.get(key) for key in self.store.catalog[aid]["requiredFields"]}
                    content["conclusion"] = determination.get("decision")
                    content["advisoryDetermination"] = determination
                content["sourceInteractionId"] = receipt["interactionId"]
                content["unresolved"] = [key for key in self.store.catalog[aid]["requiredFields"] if content[key] is None]
                result = self.store.draft(c, aid, content, refs + [receipt["interactionId"]], args["expectedRevision"], args.get("changeReason", ""))
            elif op == "calculate":
                spec = importlib.util.spec_from_file_location("ifrs_controls", self.assets / "tools/accounting_controls.py")
                controls = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(controls)
                case = c.get("factsByCase", {}).get(args["caseId"])
                if not case:
                    raise ValueError("Calculation requires current operator-pinned case facts")
                if not case["facts"].get("evidenceRefs") or not set(case["facts"]["evidenceRefs"]) <= set(c.get("evidenceRefAllowlist", [])):
                    raise ValueError("Calculation evidence is outside this entity/period")
                receipt = self.verify_rag(c, args["interactionId"])
                section = controls.source_section(case["control"], case["facts"])
                if not section or receipt["request"].get("sectionId") != section or any(p.get("sectionId") != section for p in receipt["passages"]):
                    raise ValueError("Calculation source scope does not match the control")
                reporting_controls = {"section-6-dividend-note", "section-7-financing-reconciliation", "section-7-supplier-finance-note", "section-8-note-structure-screen"}
                if case["control"] in reporting_controls and case["facts"].get("period") != c["period"]:
                    raise ValueError("Reporting case period differs from current context")
                determination = controls.determine(case["control"], case["facts"])
                compliance_hash = None
                if case["control"] == "section-3-compliance-evidence-screen":
                    path = self.assets / "tools/compliance_evidence.py"
                    spec = importlib.util.spec_from_file_location("ifrs_compliance", path)
                    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
                    determination = module.assess(self.store, self.rules, c,
                        json.loads((self.assets / "source-requirements.json").read_text()),
                        json.loads((self.assets / "document-asset-catalog.json").read_text()))
                    compliance_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                required_sections = set(determination.get("requiredSections", [])) - {section}
                verified_dependencies = []
                supplied = args.get("dependencyInteractionIds", [])
                if not isinstance(supplied, list) or len(supplied) > 10 or any(not isinstance(ref, str) for ref in supplied):
                    raise ValueError("Bounded dependency source receipt list required")
                for ref in supplied:
                    dependency = self.verify_rag(c, ref)
                    dependency_section = dependency["request"].get("sectionId")
                    if any(p.get("sectionId") != dependency_section for p in dependency["passages"]):
                        raise ValueError("Dependency source section mismatch")
                    verified_dependencies.append(dict(sectionId=dependency_section, interactionId=ref))
                if determination.get("status") == "ADVISORY" and not required_sections <= {d["sectionId"] for d in verified_dependencies}:
                    raise ValueError("Missing verified dependency-section source context")
                result = {"caseId": args["caseId"], "determination": determination,
                          "verifiedDependencyInteractions": verified_dependencies,
                          "remediationImplementationSHA256": hashlib.sha256((self.assets / "tools/remaining_controls.py").read_bytes()).hexdigest() if case["control"].startswith("rem-") else None,
                          "canonicalEvidenceImplementationSHA256": compliance_hash,
                          "reportingImplementationSHA256": hashlib.sha256((self.assets / "tools/reporting_controls.py").read_bytes()).hexdigest() if case["control"] in reporting_controls else None,
                          "sourceInteractionId": receipt["interactionId"], "evidenceClass": c.get("evidenceClass", "synthetic"),
                          "controlImplementationSHA256": hashlib.sha256((self.assets / "tools/accounting_controls.py").read_bytes()).hexdigest(),
                          "reviewRequired": True, "nativeProjection": "NOT_AUTOMATIC"}
            elif op == "request-review":
                result = self.store.request_review(c, aid, args["expectedRevision"])
            elif op == "approve":
                review = dict(args["review"])
                review["reviewerName"] = principal["name"]
                receipt = self.verify_rag(c, args["interactionId"])
                if not review.get("ragReceipt") or review["ragReceipt"]["interactionId"] != receipt["interactionId"]:
                    raise ValueError("Review must reference verified interaction")
                claimed = review["ragReceipt"].get("verifiedPassageRefs", [])
                if not claimed or not set(claimed) <= {p["chunkId"] for p in receipt["passages"]}:
                    raise ValueError("Unknown cited passages")
                if review["ragReceipt"].get("sourceHash") not in {p["sourceSHA256"] for p in receipt["passages"] if p["chunkId"] in claimed}:
                    raise ValueError("Review source hash does not match cited passages")
                result = self.store.approve(c, aid, review)
            elif op == "reject":
                if principal["name"] != self.store.catalog[aid]["reviewerName"]:
                    raise PermissionError("Wrong assigned reviewer")
                h = self.store.head(c, aid)
                if not h or h["revision"] != args["expectedRevision"] or h["contentHash"] != args["contentHash"]:
                    raise self.rules.ConflictError("Review targets another revision")
                result = self.store.invalidate(c, [aid], "independent review rejected: " + args["reason"])
            elif op == "event":
                self.store._write(context_key, c)
                self.store._write(applicability_key, args["applicableAssetTypeIds"])
                result = self.store.reconcile_event(c, args["event"], args["applicableAssetTypeIds"])
            elif op == "observe":
                if not m:
                    raise PermissionError("Agent identity required for native observation")
                result = self.store.observe_machine(c, {"machine": m}, args["observation"])
            elif op == "resolve-assessment":
                result = self.store.resolve_assessment(c, args["taskId"], args["resolution"])
            elif op == "contribute":
                if not m or aid not in {d["assetTypeId"] for d in m["metadata"]["documentAssetWorkflow"]["duties"]}:
                    raise PermissionError("Contribution is outside declared duties")
                if not args.get("evidenceRefs") or not args.get("content"):
                    raise ValueError("Contribution requires evidence and content")
                fingerprint = self.rules.input_fingerprint(c, self.store._upstream(c, aid))
                cid = hashlib.sha256(canonical({"sourceMachine": mid, "entityId": c["entityId"], "period": c["period"], "assetTypeId": aid, "inputFingerprint": fingerprint, "content": args["content"], "evidenceRefs": args["evidenceRefs"]}).encode()).hexdigest()
                result = {"status": "OPEN", "sourceMachine": mid, "targetMachine": self.graph["assetStewards"][aid], "entityId": c["entityId"], "period": c["period"], **args}
                # Caller cannot override route, scope, or status.
                result.update(sourceMachine=mid, targetMachine=self.graph["assetStewards"][aid], entityId=c["entityId"], period=c["period"], status="OPEN", contributionId=cid, inputFingerprint=fingerprint)
                path = self.store.root / "contributions" / (cid + ".json")
                existing = self.store._read(path)
                if existing:
                    result = existing
                else:
                    self.store._write(path, result)
                    self.store._event({"type": "CONTRIBUTION_SUBMITTED", "dispatchId": key, "sourceMachine": mid, "targetMachine": result["targetMachine"], "assetTypeId": aid, "contributionId": cid})
            elif op == "resolve-contribution":
                cid = args["contributionId"]
                if len(cid) != 64 or any(char not in "0123456789abcdef" for char in cid):
                    raise ValueError("Invalid contribution ID")
                path = self.store.root / "contributions" / (cid + ".json")
                contribution = self.store._read(path)
                if not m or not contribution or contribution["targetMachine"] != mid or contribution["entityId"] != c["entityId"] or contribution["period"] != c["period"]:
                    raise PermissionError("Unowned contribution/scope")
                aid = contribution["assetTypeId"]
                if contribution["status"] != "OPEN" or contribution["inputFingerprint"] != self.rules.input_fingerprint(c, self.store._upstream(c, aid)):
                    raise ValueError("Only a current open contribution can be satisfied")
                h = self.store.head(c, aid)
                if not h or h["status"] != "APPROVED" or h["revision"] != args["revision"] or h["contentHash"] != args["contentHash"]:
                    raise ValueError("Contribution satisfaction requires the exact approved artifact")
                revision = self.store.revision(c, aid, h["revision"])
                if cid not in revision["content"].get("contributionRefs", []):
                    raise ValueError("Approved artifact does not reference this contribution")
                contribution.update(status="SATISFIED", completionRef={k: h[k] for k in ["assetTypeId", "revision", "contentHash"]})
                self.store._write(path, contribution)
                self.store._event({"type": "CONTRIBUTION_SATISFIED", "contributionId": cid})
                result = contribution
            else:
                raise PermissionError("Unknown/forbidden document operation")
            if op in {"draft", "draft-from-case", "approve", "reject"}:
                self.store.reconcile_event(c, {"type": "period-close-requested", "entityId": c["entityId"], "period": c["period"], "eventId": command.dispatchId}, self.store._read(applicability_key, []))
            if op in {"event", "draft", "draft-from-case", "approve", "reject"}:
                for path_key, body in list(db.execute("SELECT key,body FROM objects WHERE key LIKE 'contributions/%'")):
                    item = json.loads(body)
                    if item["entityId"] == c["entityId"] and item["period"] == c["period"] and item["status"] == "OPEN" and item["inputFingerprint"] != self.rules.input_fingerprint(c, self.store._upstream(c, item["assetTypeId"])):
                        item["status"] = "SUPERSEDED"
                        self.store._write(self.store.root / path_key, item)
                        self.store._event({"type": "CONTRIBUTION_SUPERSEDED", "contributionId": item["contributionId"]})
            receipt = {"dispatchId": command.dispatchId, "principal": principal["name"], "operation": op, "entityId": c["entityId"], "period": c["period"], "result": result, "channel": "document-task-and-artifact-sidecar", "nativeWrites": 0}
            db.execute("INSERT INTO dispatch VALUES(?,?,?)", (key, request_hash, canonical(receipt)))
            return receipt


def create_app(service):
    app = FastAPI(title="IFRS document sidecar")

    @app.post("/tools/execute")
    def execute(command: Command, authorization: str = Header(default="")):
        try:
            return service.execute(command, service.principal(authorization))
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except (ValueError, httpx.HTTPError) as exc:
            raise HTTPException(409, str(exc)) from exc

    return app


def configured_app():
    principal_path = Path(os.environ["IFRS_WORKFLOW_PRINCIPALS"])
    if principal_path.stat().st_mode & 0o077:
        raise RuntimeError("Workflow principal file must be private (0600)")
    principals = json.loads(principal_path.read_text())
    if not principals or any(not p.get("token") for p in principals):
        raise RuntimeError("Explicit scoped service credentials are required")
    if len({p["token"] for p in principals}) != len(principals) or len({p["name"] for p in principals}) != len(principals):
        raise RuntimeError("Workflow credentials and principal names must be unique")
    return create_app(WorkflowService(os.environ["IFRS_ASSETS_DIR"], os.environ["IFRS_MACHINES_DIR"], os.environ["IFRS_DOCUMENT_STATE_DIR"], principals, os.environ["IFRS_RAG_URL"], os.environ["IFRS_EVIDENCE_TOKEN"]))
