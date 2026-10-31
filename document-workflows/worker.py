"""Lease and dispatch canonical document work to fresh, scoped gateway sessions.

Only observed sidecar state can satisfy work; model narrative is not completion.
The operator config owns URLs/credentials, machine-to-agent routing and context.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import time
import uuid

import httpx


def dispatch_once(config, client=None, limit=1, task_ids=None):
    client = client or httpx.Client(timeout=240)
    state = Path(config["stateDirectory"]); state.mkdir(parents=True, exist_ok=True, mode=0o700)
    def command(op, args, token):
        response = client.post(config["documentURL"].rstrip("/") + "/tools/execute", headers={"Authorization": "Bearer " + token},
                               json={"dispatchId": str(uuid.uuid4()), "operation": op, "context": config["context"], "arguments": args})
        response.raise_for_status()
        return response.json()
    tasks = command("tasks", {}, config["operatorToken"])["tasks"]
    results = []
    with sqlite3.connect(state / "worker.sqlite", timeout=60) as db:
        db.execute("CREATE TABLE IF NOT EXISTS leases(task TEXT PRIMARY KEY, lease TEXT, expires REAL)")
        db.execute("CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY, task TEXT, body TEXT)")
        for task in tasks:
            if len(results) >= limit:
                break
            if task["status"] != "OPEN":
                continue
            if task_ids is not None and task["taskId"] not in task_ids:
                continue
            aid = task["assetTypeId"]
            route = config["assetRoutes"].get(aid)
            if not route:
                continue
            lease = str(uuid.uuid4())
            db.execute("BEGIN IMMEDIATE")
            held = db.execute("SELECT expires FROM leases WHERE task=?", (task["taskId"],)).fetchone()
            if held and held[0] > time.time():
                db.rollback()
                continue
            db.execute("INSERT OR REPLACE INTO leases VALUES(?,?,?)", (task["taskId"], lease, time.time() + 300))
            db.commit()
            attempt = {"attemptId": lease, "taskId": task["taskId"], "assetTypeId": aid, "agentId": route["agentId"], "entityId": config["context"]["entityId"], "period": config["context"]["period"], "nativeWrites": 0}
            try:
                before = command("inspect", {"assetTypeId": aid}, route["token"])
                facts = config["context"].get("factsByCase", {})
                calculations = []
                if aid == "ELIGIBILITY-MEMO":
                    facts = {key: value for key, value in facts.items() if value["control"] == "section-1-eligibility"}
                    source = command("retrieve", {"domain": "ifrs-accounting", "documentRole": "standard_source", "edition": "February 2025 third edition", "sectionId": "01", "question": "SME eligibility, public accountability and jurisdictional permission", "limit": 4}, route["token"])
                    for case_id in facts:
                        calculations.append(command("calculate", {"caseId": case_id, "interactionId": source["interactionId"]}, route["token"])["result"])
                reporting_types = {"DIVIDEND-REGISTER": ("section-6-dividend-note", "06"), "FINANCING-RECONCILIATION": ("section-7-financing-reconciliation", "07"), "SUPPLIER-FINANCE-NOTE": ("section-7-supplier-finance-note", "07")}
                reporting_case = None
                if aid in reporting_types:
                    control, section = reporting_types[aid]
                    facts = {key: value for key, value in facts.items() if value["control"] == control and (not route.get("caseId") or key == route["caseId"])}
                    source = command("retrieve", {"domain": "ifrs-accounting", "documentRole": "standard_source", "edition": "February 2025 third edition", "sectionId": section, "question": "Return the required reporting and disclosure source context for " + aid, "limit": 4}, route["token"])
                    for case_id in facts:
                        calculation = command("calculate", {"caseId": case_id, "interactionId": source["interactionId"]}, route["token"])["result"]
                        calculations.append(calculation)
                        if calculation["determination"].get("status") == "ADVISORY" and calculation["determination"].get("documentAssetTypeId") == aid and reporting_case is None:
                            reporting_case = case_id
                    if reporting_case is None:
                        raise ValueError("No resolved scoped reporting case; financial facts must remain unresolved")
                prompt = ("Execute this document task now; do not merely describe work or wait for another agent. "
                          "Use ifrs_document_draft to persist an owned DRAFT. First call ifrs_document_resource with "
                          + json.dumps({"assetTypeId": aid})
                          + ". Read ifrs_document_inspect, retrieve applicable passages using ifrs_source_retrieve with a question and sectionId, then call ifrs_document_draft with assetTypeId, content object, evidenceRefs and expectedRevision. "
                          "A DRAFT may retain explicitly unresolved fields when upstream documents are missing. Missing evidence must stay unresolved, never invented. "
                          "Use only the supplied scoped facts/evidence or independently retrieved source receipts. Do not approve, post, file or release. "
                          "This is the document sidecar; preserve native numeric completion semantics. "
                          "Report the canonical draft revision/hash actually returned by the tool.\n"
                          "expectedRevision must be 0 if headBefore is null; otherwise use its actual revision. "
                          "Use the supplied server-calculated advisory determinations; do not reverse their polarity without recording specific contradictory evidence as unresolved.\n"
                          + json.dumps({"task": task, "entityId": config["context"]["entityId"], "period": config["context"]["period"],
                                        "evidenceClass": config["context"].get("evidenceClass", "synthetic"), "facts": facts,
                                        "supportingRecords": [record for record in config["context"].get("evidenceRecords", []) if record["recordId"] in {ref for case in facts.values() for ref in case["facts"].get("evidenceRefs", [])}],
                                        "verifiedAdvisoryDeterminations": calculations,
                                        "evidenceRefAllowlist": config["context"].get("evidenceRefAllowlist", []), "headBefore": before["head"]}))
                if aid == "ELIGIBILITY-MEMO" and calculations:
                    prompt = ("Execute this scoped document task now. Call ifrs_eligibility_draft with exactly these parameters: "
                              + json.dumps({"caseId": next(iter(facts)), "interactionId": source["interactionId"], "expectedRevision": before["head"]["revision"] if before["head"] else 0, "changeReason": "Render current operator-pinned eligibility case"})
                              + ". This tool computes the advisory result from the pinned facts and verified source and renders the owned template. Do not write free-form financial facts, invent a hash, or approve the draft. Report only the actual canonical revision and hash returned by the tool. "
                              + json.dumps({"taskId": task["taskId"], "entityId": config["context"]["entityId"], "period": config["context"]["period"], "evidenceClass": config["context"].get("evidenceClass"), "advisoryDetermination": calculations}))
                if reporting_case:
                    prompt = ("Execute this scoped reporting task now. Call ifrs_reporting_draft with exactly these parameters: "
                              + json.dumps({"assetTypeId": aid, "caseId": reporting_case, "interactionId": source["interactionId"], "expectedRevision": before["head"]["revision"] if before["head"] else 0, "changeReason": "Render current operator-pinned reporting case"})
                              + ". The server computes decimal financial data and emits an owned DRAFT with server-only provenance. Do not use generic draft, invent financial content, approve, post or release. Report the actual revision/hash returned.")
                response = client.post(config["gatewayURL"].rstrip("/") + "/v1/chat/completions", headers={"Authorization": "Bearer " + config["gatewayToken"]},
                                       json={"model": "openclaw:" + route["agentId"], "user": "ifrs-document-task-" + lease, "stream": False,
                                             "messages": [{"role": "user", "content": prompt}]})
                response.raise_for_status()
                after = command("inspect", {"assetTypeId": aid}, route["token"])
                head = after["head"]
                changed = head is not None and head != before["head"]
                if reporting_case and changed:
                    proof = (after.get("revision") or {}).get("content", {}).get("typedProducerReceipt", {})
                    if head.get("status") != "DRAFT" or proof.get("caseId") != reporting_case or proof.get("sourceInteractionId") != source["interactionId"]:
                        raise ValueError("Changed draft lacks the exact server-owned typed reporting provenance")

                attempt.update(status="ARTIFACT_OBSERVED_REVIEW_REQUIRED" if changed else "UNRESOLVED_NO_ARTIFACT_CHANGE",
                               headBefore=before["head"], headAfter=head, gatewayObservation=response.json())
                if task["action"] == "ASSESS_DOCUMENT_IMPACT":
                    # A new draft is not proof an impact assessment was resolved.
                    attempt["status"] = "ASSESSMENT_REQUIRES_CANONICAL_DISPOSITION"
            except Exception as exc:
                attempt.update(status="FAILED", error=type(exc).__name__ + ": " + str(exc))
            finally:
                db.execute("INSERT INTO attempts VALUES(?,?,?)", (lease, task["taskId"], json.dumps(attempt)))
                db.execute("DELETE FROM leases WHERE task=? AND lease=?", (task["taskId"], lease))
                db.commit()
            results.append(attempt)
    return {"attempts": results, "nativeWrites": 0, "automaticApproval": False}


def dispatch(config, client=None, limit=1, max_attempts=3):
    """Retry unresolved work in new sessions; preserve every failed observation.

    A retry is another actual tool/model attempt, never replayed expected output.
    Only the same selected canonical tasks are retried. Permissions, CAS and
    artifact verification are identical on every attempt.
    """
    if not 1 <= max_attempts <= 5:
        raise ValueError("Choose one to five bounded attempts per task")
    attempts = []
    pending = None
    for _ in range(max_attempts):
        result = dispatch_once(config, client=client, limit=limit if pending is None else len(pending), task_ids=pending)
        attempts.extend(result["attempts"])
        if not result["attempts"]:
            break
        pending = {attempt["taskId"] for attempt in result["attempts"] if attempt["status"] != "ARTIFACT_OBSERVED_REVIEW_REQUIRED"}
        if not pending:
            break
    return {"attempts": attempts, "nativeWrites": 0, "automaticApproval": False, "maxAttemptsPerTask": max_attempts}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args()
    if args.config.stat().st_mode & 0o077:
        raise SystemExit("Worker config must be private (0600)")
    result = dispatch(json.loads(args.config.read_text()), limit=args.limit, max_attempts=args.max_attempts)
    # Full private attempts live in worker.sqlite; stdout is a safe status summary.
    print(json.dumps({"attempts": [{k: a[k] for k in ("attemptId", "taskId", "assetTypeId", "status")} for a in result["attempts"]], "automaticApproval": False}))
    final_by_task = {a["taskId"]: a for a in result["attempts"]}
    return 0 if final_by_task and all(a["status"] == "ARTIFACT_OBSERVED_REVIEW_REQUIRED" for a in final_by_task.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
