"""HR tools the assistant may call. Every tool validates its input, checks real HRMS data, and reports exactly what happened."""

import re
from datetime import datetime, timezone

from fastapi import HTTPException
from pymongo.database import Database

from app.models import APPLICATIONS, AUDIT_LOGS, CANDIDATES, EMAIL_LOGS, INTERVIEWS, JOBS

STAGES = ["needs_review", "screening", "interview", "offer", "hired", "rejected"]
TOOL_DEFINITIONS = [
    {"type": "function", "function": {
        "name": "list_jobs", "description": "List job postings with status and application counts.",
        "parameters": {"type": "object", "properties": {"status": {"type": "string", "enum": ["draft", "published", "closed", "archived"]}}, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "create_job_draft", "description": "Create a draft job posting when HR explicitly asks to create one. Never publishes.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "maxLength": 180}, "department": {"type": "string"},
            "description": {"type": "string", "maxLength": 10000},
            "required_skills": {"type": "array", "items": {"type": "string"}, "maxItems": 40},
            "preferred_skills": {"type": "array", "items": {"type": "string"}, "maxItems": 40},
            "responsibilities": {"type": "array", "items": {"type": "string"}, "maxItems": 40},
        }, "required": ["title"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "hiring_summary", "description": "Counts of applications by stage, upcoming interviews, pending evaluations and email problems, optionally for one job.",
        "parameters": {"type": "object", "properties": {"job_id": {"type": "string"}}, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "shortlist_candidates",
        "description": "Assess applications against the job description and rank eligible candidates (explainable). Only when HR asks to shortlist.",
        "parameters": {"type": "object", "properties": {
            "job_id": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            "move_to_screening": {"type": "boolean"},
        }, "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "set_application_stage",
        "description": "Change one application's stage after eligibility checks. Only when HR explicitly asks for a status change.",
        "parameters": {"type": "object", "properties": {
            "application_id": {"type": "string"}, "stage": {"type": "string", "enum": STAGES},
        }, "required": ["application_id", "stage"], "additionalProperties": False},
    }},
    {"type": "function", "function": {
        "name": "list_interview_outcomes",
        "description": "List recorded interview outcomes and feedback for a job and optionally a round.",
        "parameters": {"type": "object", "properties": {
            "job_id": {"type": "string"}, "round_name": {"type": "string"},
            "outcome": {"type": "string", "enum": ["pending_evaluation", "passed", "failed", "on_hold"]},
        }, "additionalProperties": False},
    }},
]


def _resolve_job(db: Database, args: dict, context_job_id: str | None, message: str):
    from app.api.v1.interview_workflow import _job_clarification, _resolve_job_candidates

    job_id = args.get("job_id") or context_job_id
    if job_id:
        job = db[JOBS].find_one({"_id": job_id})
        return (job, None) if job else (None, {"error": "Job not found."})
    job, matches = _resolve_job_candidates(db, message, None)
    if job is None:
        return None, _job_clarification(matches, "Which job should I use?")
    return job, None


def list_jobs(db: Database, args: dict) -> dict:
    query = {"status": args["status"]} if args.get("status") else {}
    jobs = [
        {"job_id": j["_id"], "title": j["title"], "status": j.get("status"), "department": j.get("department"),
         "applications": db[APPLICATIONS].count_documents({"job_id": j["_id"]}),
         "rounds": [r["name"] for r in sorted(j.get("interview_rounds", []), key=lambda r: r["order"])]}
        for j in db[JOBS].find(query).sort("created_at", -1).limit(50)
    ]
    return {"count": len(jobs), "jobs": jobs}


def create_job_draft(db: Database, user: dict, args: dict, message: str) -> dict:
    if not re.search(r"\b(create|add|open|draft|post|new)\b", message, re.IGNORECASE):
        return {"error": "An explicit instruction to create a job posting is required."}
    from app.api.v1.recruitment import create_job
    from app.schemas import JobCreate

    try:
        data = JobCreate(**{**{k: v for k, v in args.items() if v not in (None, "", [])}, "status": "draft"})
        created = create_job(data, db, user)
    except HTTPException as exc:
        return {"error": str(exc.detail)}
    except Exception as exc:
        return {"error": f"The job details are invalid: {str(exc)[:200]}"}
    return {"job_id": created["id"], "title": created["title"], "status": created["status"],
            "note": "Saved as a draft. Publish it from Recruitment to open the public application form."}


def hiring_summary(db: Database, args: dict, context_job_id: str | None) -> dict:
    job_id = args.get("job_id") or context_job_id
    match = {"job_id": job_id} if job_id else {}
    stages = {row["_id"]: row["count"] for row in db[APPLICATIONS].aggregate([
        {"$match": match}, {"$group": {"_id": "$stage", "count": {"$sum": 1}}}])}
    now = datetime.now(timezone.utc)
    interview_match = {"job_id": job_id} if job_id else {}
    return {
        "scope": "job" if job_id else "all jobs",
        "applications_by_stage": stages,
        "upcoming_interviews": db[INTERVIEWS].count_documents({**interview_match, "status": "scheduled", "starts_at": {"$gte": now}}),
        "awaiting_evaluation": db[INTERVIEWS].count_documents({**interview_match, "status": {"$in": ["scheduled", "completed"]}, "starts_at": {"$lt": now}, "outcome": "pending_evaluation"}),
        "on_hold": db[INTERVIEWS].count_documents({**interview_match, "outcome": "on_hold"}),
        "emails_not_delivered": db[EMAIL_LOGS].count_documents({"source": "interview_plan", "delivery_status": {"$in": ["not_configured", "failed", "unknown"]}}),
    }


def shortlist_candidates(db: Database, user: dict, args: dict, context_job_id: str | None, message: str) -> dict:
    if not re.search(r"\bshortlist\w*\b|\bscreen\b|\brank\b", message, re.IGNORECASE):
        return {"error": "An explicit shortlisting request is required."}
    job, problem = _resolve_job(db, args, context_job_id, message)
    if problem:
        return problem
    from app.api.v1.recruitment import run_shortlisting

    apply = bool(args.get("move_to_screening")) and bool(re.search(r"\b(move|advance|apply|promote)\b", message, re.IGNORECASE))
    try:
        result = run_shortlisting(db, job, user, args.get("limit"), apply, reassess=True)
    except HTTPException as exc:
        return {"error": str(exc.detail)}
    top = [
        {"rank": r["rank"], "application_id": r["application_id"], "candidate_name": r["candidate_name"], "status": r["status"],
         "score": r["score"], "explanation": r["explanation"], "gaps": [g["requirement"] for g in r["evidence_gaps"]][:6]}
        for r in result["results"][:25]
    ]
    return {"job_id": job["_id"], "job_title": job["title"], "limit": result["limit"], "counts": result["counts"],
            "moved_to_screening": result["moved_to_screening"], "semantic_used": result["semantic_used"],
            "llm_used": result["llm_used"], "results": top, "count": len(top)}


def set_application_stage(db: Database, user: dict, args: dict, message: str) -> dict:
    if not re.search(r"\b(move|mark|set|change|update|advance|reject|shortlist)\b", message, re.IGNORECASE):
        return {"error": "An explicit status-change instruction is required; no status was changed."}
    application = db[APPLICATIONS].find_one({"_id": args["application_id"]})
    if application is None:
        return {"error": "Application not found."}
    stage = args["stage"]
    if stage not in STAGES:
        return {"error": "Unknown stage."}
    if application.get("stage") in ("hired", "rejected") and stage != application.get("stage"):
        return {"error": f"This application is already {application['stage']}. Change it from the candidate page if that was a mistake."}
    job = db[JOBS].find_one({"_id": application["job_id"]}) or {}
    rounds = job.get("interview_rounds", [])
    if stage == "interview":
        return {"error": "Candidates move to the interview stage when an interview is confirmed. Ask me to schedule the round instead."}
    if stage == "rejected" and not re.search(r"\breject\w*\b", message, re.IGNORECASE):
        return {"error": "Rejecting needs an explicit 'reject' instruction from HR."}
    if stage in ("offer", "hired") and rounds:
        for round_item in rounds:
            passed = db[INTERVIEWS].find_one({"application_id": application["_id"], "round_id": round_item["round_id"], "status": "completed", "outcome": "passed"})
            if not passed:
                return {"error": f"Cannot move to {stage}: {round_item['name']} has no recorded Passed outcome."}
    if stage == application.get("stage"):
        return {"error": f"The application is already in {stage}."}
    now = datetime.now(timezone.utc)
    db[APPLICATIONS].update_one({"_id": application["_id"]}, {"$set": {"stage": stage, "updated_at": now}})
    db[AUDIT_LOGS].insert_one({"event": "application_stage_changed_by_assistant", "application_id": application["_id"],
                               "previous_stage": application.get("stage"), "new_stage": stage, "user_id": user["_id"], "created_at": now})
    candidate = db[CANDIDATES].find_one({"_id": application["candidate_id"]}) or {}
    return {"application_id": application["_id"], "candidate_name": candidate.get("name"), "previous_stage": application.get("stage"), "stage": stage}


def list_interview_outcomes(db: Database, args: dict, context_job_id: str | None, message: str) -> dict:
    job, problem = _resolve_job(db, args, context_job_id, message)
    if problem:
        return problem
    from app.api.v1.interview_workflow import _resolve_round

    round_config = _resolve_round(job, args.get("round_name") or message)
    query: dict = {"job_id": job["_id"], "status": "completed"}
    if round_config:
        query["round_id"] = round_config.round_id
    if args.get("outcome"):
        query["outcome"] = args["outcome"]
    rows = []
    for interview in db[INTERVIEWS].find(query).sort("starts_at", -1).limit(50):
        candidate = db[CANDIDATES].find_one({"_id": interview.get("candidate_id")}) or {}
        rows.append({"candidate_name": candidate.get("name"), "round": interview.get("round_name"), "outcome": interview.get("outcome", "pending_evaluation"),
                     "feedback": (interview.get("evaluation_notes") or "")[:300] or None})
    return {"job_title": job["title"], "round": round_config.name if round_config else "all rounds", "count": len(rows), "results": rows}


def execute(name: str, args: dict, *, db: Database, user: dict, context_job_id: str | None, message: str) -> dict | None:
    """Run a tool defined here; returns None when ``name`` is not one of these tools."""
    try:
        if name == "list_jobs":
            return list_jobs(db, args)
        if name == "create_job_draft":
            return create_job_draft(db, user, args, message)
        if name == "hiring_summary":
            return hiring_summary(db, args, context_job_id)
        if name == "shortlist_candidates":
            return shortlist_candidates(db, user, args, context_job_id, message)
        if name == "set_application_stage":
            if not isinstance(args.get("application_id"), str) or not isinstance(args.get("stage"), str):
                return {"error": "An application ID and stage are required."}
            return set_application_stage(db, user, args, message)
        if name == "list_interview_outcomes":
            return list_interview_outcomes(db, args, context_job_id, message)
    except HTTPException as exc:
        return {"error": str(exc.detail)}
    return None
