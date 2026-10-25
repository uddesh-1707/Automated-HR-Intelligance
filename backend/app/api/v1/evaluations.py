"""Token-protected interviewer evaluation pages. No login: the unguessable token is the credential."""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.database import MongoDatabase
from app.models import APPLICATIONS, AUDIT_LOGS, CANDIDATES, INTERVIEWS, JOBS
from app.services.evaluation_links import lookup_token, resume_url
from app.services.pipeline import sync_pipeline_from_interview

router = APIRouter()
logger = logging.getLogger(__name__)


class EvaluationSubmit(BaseModel):
    outcome: Literal["passed", "failed", "on_hold"]
    feedback: str = Field(min_length=10, max_length=5000)
    rating: int | None = Field(default=None, ge=1, le=5)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _authorize(db: MongoDatabase, token: str) -> tuple[dict, dict, dict, dict, dict]:
    document, problem = lookup_token(db, token)
    if problem == "unknown":
        raise HTTPException(status_code=404, detail="This evaluation link is not valid.")
    if problem == "revoked":
        raise HTTPException(status_code=410, detail="This link is no longer active because the interview was rescheduled or cancelled. Ask HR for the new link.")
    if problem == "expired":
        raise HTTPException(status_code=410, detail="This evaluation link has expired. Ask HR to resend it.")
    interview = db[INTERVIEWS].find_one({"_id": document["interview_id"]})
    application = db[APPLICATIONS].find_one({"_id": document["application_id"]}) if interview else None
    candidate = db[CANDIDATES].find_one({"_id": application["candidate_id"]}) if application else None
    job = db[JOBS].find_one({"_id": application["job_id"]}) if application else None
    if not (interview and application and candidate and job):
        raise HTTPException(status_code=404, detail="This interview no longer exists.")
    return document, interview, application, candidate, job


def _evaluation_state(interview: dict) -> dict:
    outcome = interview.get("outcome", "pending_evaluation")
    submitted = outcome != "pending_evaluation"
    return {
        "submitted": submitted,
        "outcome": outcome if submitted else None,
        "feedback": interview.get("evaluation_notes") or None,
        "submitted_at": interview.get("evaluated_at"),
        "can_submit": interview.get("status") in ("scheduled", "completed") and (not submitted or outcome == "on_hold"),
        "resolves_on_hold": outcome == "on_hold",
    }


@router.get("/{token}")
def view_evaluation(token: str, db: MongoDatabase) -> dict:
    document, interview, application, candidate, job = _authorize(db, token)
    parsed = application.get("parsed_resume") or {}
    assessment = application.get("assessment") or {}
    return {
        "interviewer_email": document["interviewer_email"],
        "candidate": {
            "name": candidate["name"],
            "email": candidate["email"],
            "phone": candidate.get("phone"),
            "skills": candidate.get("skills", []),
            "education": candidate.get("education"),
            "experience_years": candidate.get("experience_years"),
            "experience": (parsed.get("experience") or "")[:3000],
            "projects": (parsed.get("projects") or "")[:3000],
        },
        "job": {"title": job["title"], "description": job.get("description")},
        "round": {"name": interview.get("round_name") or interview.get("interview_type")},
        "interview": {
            "starts_at": _aware(interview["starts_at"]),
            "timezone": interview["timezone"],
            "duration_minutes": interview.get("duration_minutes", 60),
            "status": interview["status"],
            "meeting_url": interview.get("meeting_url") if interview.get("meeting_status") == "created" else None,
            "location": interview.get("location"),
        },
        "screening_summary": assessment.get("explanation"),
        "resume_url": resume_url(token),
        "evaluation": _evaluation_state(interview),
        "expires_at": _aware(document["expires_at"]),
    }


@router.get("/{token}/resume")
def download_evaluation_resume(token: str, db: MongoDatabase) -> FileResponse:
    _document, _interview, application, _candidate, _job = _authorize(db, token)
    path = Path(application.get("resume_path") or "").resolve()
    if settings.resume_storage_dir.resolve() not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="The resume file is no longer available.")
    return FileResponse(path, media_type=application["resume_content_type"], filename=application["resume_filename"])


@router.post("/{token}")
def submit_evaluation(token: str, data: EvaluationSubmit, db: MongoDatabase) -> dict:
    document, interview, application, candidate, job = _authorize(db, token)
    if interview["status"] == "cancelled":
        raise HTTPException(status_code=409, detail="This interview was cancelled, so it cannot be evaluated.")
    start = _aware(interview["starts_at"])
    if not settings.evaluation_allow_before_start and start > datetime.now(timezone.utc):
        raise HTTPException(status_code=409, detail="The evaluation opens when the interview starts.")
    now = datetime.now(timezone.utc)
    entry = {
        "outcome": data.outcome, "feedback": data.feedback.strip(), "rating": data.rating,
        "interviewer_email": document["interviewer_email"], "submitted_at": now,
    }
    claimed = db[INTERVIEWS].find_one_and_update(
        {
            "_id": interview["_id"],
            "status": {"$in": ["scheduled", "completed"]},
            "$or": [{"outcome": "pending_evaluation"}, {"outcome": {"$exists": False}}, {"outcome": "on_hold"}],
        },
        {
            "$set": {
                "status": "completed",
                "outcome": data.outcome,
                "evaluation_notes": entry["feedback"],
                "evaluation_rating": data.rating,
                "evaluated_by": f"interviewer:{document['interviewer_email']}",
                "evaluation_source": "interviewer_link",
                "evaluated_at": now,
            },
            "$push": {"evaluation_history": entry},
        },
    )
    if claimed is None:
        current = db[INTERVIEWS].find_one({"_id": interview["_id"]}) or interview
        raise HTTPException(
            status_code=409,
            detail=(
                f"An evaluation was already recorded for this interview ({str(current.get('outcome')).replace('_', ' ')}). "
                "It cannot be changed from this link; contact HR if it needs correcting."
            ),
        )
    updated = db[INTERVIEWS].find_one({"_id": interview["_id"]})
    sync_pipeline_from_interview(db, updated)
    db[AUDIT_LOGS].insert_one({
        "event": "interview_evaluation_submitted",
        "application_id": application["_id"],
        "job_id": job["_id"],
        "interview_id": interview["_id"],
        "round_id": interview.get("round_id"),
        "previous_outcome": interview.get("outcome", "pending_evaluation"),
        "new_outcome": data.outcome,
        "interviewer_email": document["interviewer_email"],
        "created_at": now,
    })
    application = db[APPLICATIONS].find_one({"_id": application["_id"]}) or application
    return {
        "status": "recorded",
        "outcome": data.outcome,
        "candidate_status": application.get("pipeline_status"),
        "message": {
            "passed": "Recorded as Passed. The candidate is now eligible for the next configured round.",
            "failed": "Recorded as Failed. The candidate will not proceed to further rounds.",
            "on_hold": "Recorded as On Hold. The application stays pending until you or HR resolve it with a final outcome.",
        }[data.outcome],
    }
