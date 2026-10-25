import hashlib
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query, status
from pymongo.database import Database

from app.api.deps import CurrentUser
from app.core.config import settings
from app.core.database import MongoDatabase
from app.models import APPLICATIONS, AUDIT_LOGS, CANDIDATES, EMAIL_LOGS, INTERVIEWS, JOBS, PROGRESSION_OVERRIDES, new_id
from app.schemas import InterviewOutcomeUpdate, InterviewRead, InterviewWrite, InterviewRoundConfig
from app.services.google_calendar import CalendarNotConfigured, CalendarUnavailable, GoogleCalendar
from app.services.pipeline import sync_pipeline_from_interview
from app.services.evaluation_links import revoke_tokens
from app.services.interview_notifications import notify_interview
from app.services.meetings import expire_overdue_meetings, invalidate_meeting

router = APIRouter()
logger = logging.getLogger(__name__)


def _check_external_availability(emails: list[str], start: datetime, duration_minutes: int, requires_hr: bool) -> None:
    """Read-only Google Calendar availability check. Skipped when no calendar is connected."""
    try:
        calendar = GoogleCalendar()
    except CalendarNotConfigured:
        return
    calendars = [*emails, calendar.host_calendar_id]
    if requires_hr and settings.google_calendar_hr_id not in (None, "", "primary"):
        calendars.append(settings.google_calendar_hr_id)
    try:
        available, conflicting = calendar.is_available(calendars, start, start + timedelta(minutes=duration_minutes))
    except CalendarUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if not available:
        raise HTTPException(status_code=409, detail=f"Selected time is unavailable on calendar {conflicting}.")


def _notify_scheduled(db: Database, interview: dict | None, user_id: str) -> None:
    """Email the candidate and interviewers about a scheduled round. Failures never undo the saved schedule."""
    if not interview:
        return
    try:
        application = db[APPLICATIONS].find_one({"_id": interview["application_id"]})
        candidate = db[CANDIDATES].find_one({"_id": application["candidate_id"]})
        job = db[JOBS].find_one({"_id": application["job_id"]})
        notify_interview(db, interview=interview, candidate=candidate, job=job, user_id=user_id)
    except Exception:
        logger.exception("Interview notification failed for interview %s", interview.get("_id"))


def _email_status(db: Database, interview_id: str) -> str | None:
    """Delivery state of the automatic notifications for this interview (None when none were attempted)."""
    statuses = [
        item.get("delivery_status")
        for item in db[EMAIL_LOGS].find({"communication_context_id": interview_id, "source": "interview_plan"})
    ]
    if not statuses:
        return None
    if all(item == "sent" for item in statuses):
        return "sent"
    if any(item == "sent" for item in statuses):
        return "partial"
    return "not_configured" if all(item == "not_configured" for item in statuses) else "failed"


def serialize_interview(db: Database, interview: dict) -> InterviewRead:
    application = db[APPLICATIONS].find_one({"_id": interview["application_id"]})
    candidate = db[CANDIDATES].find_one({"_id": application["candidate_id"]}) if application else None
    job = db[JOBS].find_one({"_id": application["job_id"]}) if application else None
    if application is None or candidate is None or job is None:
        raise HTTPException(status_code=404, detail="The candidate application linked to this interview no longer exists.")
    starts_at = interview["starts_at"]
    if starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=timezone.utc)
    return InterviewRead(
        id=interview["_id"],
        application_id=interview["application_id"],
        starts_at=starts_at,
        timezone=interview["timezone"],
        interview_type=interview["interview_type"],
        interviewers=interview["interviewers"],
        round_id=interview.get("round_id"),
        round_name=interview.get("round_name"),
        duration_minutes=interview.get("duration_minutes", 60),
        interviewer_emails=interview.get("interviewer_emails", []),
        location=interview.get("location"),
        notes=interview.get("notes"),
        status=interview["status"],
        outcome=interview.get("outcome", "pending_evaluation"),
        evaluation_notes=interview.get("evaluation_notes", ""),
        progression_override_id=interview.get("progression_override_id"),
        candidate_name=candidate["name"],
        candidate_email=candidate["email"],
        job_title=job["title"],
        pipeline_status=application.get("pipeline_status"),
        meeting_status=interview.get("meeting_status"),
        meeting_url=interview.get("meeting_url") if interview.get("meeting_status") == "created" else None,
        meeting_error=interview.get("meeting_error"),
        email_status=_email_status(db, interview["_id"]),
    )


def validate_interview(data: InterviewWrite) -> None:
    try:
        ZoneInfo(data.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(status_code=422, detail="Enter a valid IANA timezone, such as America/New_York.")
    if data.status == "scheduled" and data.starts_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="A scheduled interview must be in the future.")
    if not any(name.strip() for name in data.interviewers):
        raise HTTPException(status_code=422, detail="Add at least one interviewer.")
    if data.interview_type == "video" and not data.location:
        raise HTTPException(status_code=422, detail="Add a meeting link for a video interview.")
    if data.interview_type == "onsite" and not data.location:
        raise HTTPException(status_code=422, detail="Add a location for an onsite interview.")


@router.get("", response_model=list[InterviewRead])
def list_interviews(
    db: MongoDatabase,
    user: CurrentUser,
    upcoming_only: bool = Query(default=False),
) -> list[InterviewRead]:
    expire_overdue_meetings(db)
    query: dict = {}
    if upcoming_only:
        query = {"status": "scheduled", "starts_at": {"$gte": datetime.now(timezone.utc)}}
    return [serialize_interview(db, item) for item in db[INTERVIEWS].find(query).sort("starts_at", 1)]


@router.get("/applications/{application_id}", response_model=list[InterviewRead])
def list_application_interviews(
    application_id: str,
    db: MongoDatabase,
    user: CurrentUser,
) -> list[InterviewRead]:
    if db[APPLICATIONS].find_one({"_id": application_id}, {"_id": 1}) is None:
        raise HTTPException(status_code=404, detail="Candidate application not found.")
    return [
        serialize_interview(db, item)
        for item in db[INTERVIEWS].find({"application_id": application_id}).sort("starts_at", 1)
    ]


@router.post("", response_model=InterviewRead, status_code=status.HTTP_201_CREATED)
def create_interview(data: InterviewWrite, db: MongoDatabase, user: CurrentUser) -> InterviewRead:
    validate_interview(data)
    if db[APPLICATIONS].find_one({"_id": data.application_id}, {"_id": 1}) is None:
        raise HTTPException(status_code=404, detail="Candidate application not found.")
    application = db[APPLICATIONS].find_one({"_id": data.application_id})
    if application is None:
        raise HTTPException(status_code=404, detail="Candidate application not found.")
    job = db[JOBS].find_one({"_id": application["job_id"]})
    if job is None:
        raise HTTPException(status_code=404, detail="The job linked to this interview no longer exists.")
    if data.round_id:
        configured_round = next(
            (item for item in (job or {}).get("interview_rounds", []) if item.get("round_id") == data.round_id),
            None,
        )
        if configured_round is None:
            raise HTTPException(status_code=422, detail="Choose a configured interview round for this job.")
        if data.round_name and data.round_name != configured_round["name"]:
            raise HTTPException(status_code=422, detail="The interview round name does not match this job's configuration.")
        if data.status == "scheduled":
            from app.api.v1.interview_workflow import _eligibility, _local_conflict

            round_config = InterviewRoundConfig.model_validate(configured_round)
            eligibility, reason = _eligibility(db, application, round_config, [])
            if eligibility != "eligible":
                raise HTTPException(status_code=409, detail=reason or "Candidate is not eligible for this round.")
            if data.duration_minutes != round_config.duration_minutes:
                raise HTTPException(status_code=422, detail="Interview duration must match the job's round configuration.")
            if not set(round_config.required_interviewers).issubset(set(data.interviewer_emails)):
                raise HTTPException(status_code=422, detail="Include every required interviewer calendar email for this round.")
            if _local_conflict(
                db,
                list(data.interviewer_emails),
                data.starts_at.astimezone(timezone.utc),
                data.starts_at.astimezone(timezone.utc) + timedelta(minutes=data.duration_minutes),
            ):
                raise HTTPException(status_code=409, detail="A required interviewer already has an HRMS booking at this time.")
    if data.status != "completed" and data.outcome != "pending_evaluation":
        raise HTTPException(status_code=422, detail="Record an interview outcome only after marking the interview completed.")
    if data.round_id and db[INTERVIEWS].find_one({"application_id": data.application_id, "round_id": data.round_id}):
        raise HTTPException(status_code=409, detail="An interview record already exists for this candidate and round. Edit or reschedule the existing record.")
    interview = {"_id": new_id(), **data.model_dump()}
    interview.update({
        "candidate_id": application["candidate_id"],
        "job_id": application["job_id"],
        "round_name": data.round_name or (
            configured_round["name"] if data.round_id else None
        ),
    })
    if data.round_id:
        override = db[PROGRESSION_OVERRIDES].find_one({
            "application_id": data.application_id,
            "round_id": data.round_id,
            "active": True,
        })
        if override:
            interview["progression_override_id"] = override["_id"]
    interview["starts_at"] = data.starts_at.astimezone(timezone.utc)
    if data.round_id and data.status == "scheduled":
        _check_external_availability(
            list(data.interviewer_emails), interview["starts_at"], data.duration_minutes,
            bool(configured_round.get("requires_hr")),
        )
    try:
        db[INTERVIEWS].insert_one(interview)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="An interview record already exists for this candidate and round.")
    db[AUDIT_LOGS].insert_one({
        "event": "interview_scheduled",
        "application_id": data.application_id,
        "job_id": application["job_id"],
        "interview_id": interview["_id"],
        "round_id": data.round_id,
        "progression_override_id": interview.get("progression_override_id"),
        "user_id": user["_id"],
        "created_at": datetime.now(timezone.utc),
    })
    sync_pipeline_from_interview(db, interview, user["_id"])
    if data.round_id and data.status == "scheduled":
        _notify_scheduled(db, interview, user["_id"])
    return serialize_interview(db, interview)


@router.put("/{interview_id}", response_model=InterviewRead)
def update_interview(
    interview_id: str,
    data: InterviewWrite,
    db: MongoDatabase,
    user: CurrentUser,
) -> InterviewRead:
    validate_interview(data)
    existing = db[INTERVIEWS].find_one({"_id": interview_id})
    if existing is None:
        raise HTTPException(status_code=404, detail="Interview not found.")
    application = db[APPLICATIONS].find_one({"_id": data.application_id})
    if application is None:
        raise HTTPException(status_code=404, detail="Candidate application not found.")
    job = db[JOBS].find_one({"_id": application["job_id"]})
    if job is None:
        raise HTTPException(status_code=404, detail="The job linked to this interview no longer exists.")
    if data.status != "completed" and data.outcome != "pending_evaluation":
        raise HTTPException(status_code=422, detail="Record an interview outcome only after marking the interview completed.")
    if existing.get("status") == "completed" and data.status == "scheduled":
        raise HTTPException(status_code=409, detail="A completed interview cannot be rescheduled; create a follow-up round.")
    if data.round_id and (existing.get("round_id") != data.round_id or existing.get("application_id") != data.application_id):
        duplicate = db[INTERVIEWS].find_one({
            "_id": {"$ne": interview_id},
            "application_id": data.application_id,
            "round_id": data.round_id,
        })
        if duplicate:
            raise HTTPException(status_code=409, detail="An interview record already exists for this candidate and round.")
    interview = data.model_dump()
    interview["starts_at"] = data.starts_at.astimezone(timezone.utc)
    interview["job_id"] = application["job_id"]
    interview["candidate_id"] = application["candidate_id"]
    configured_round = None
    if data.round_id:
        configured_round = next(
            (item for item in (job or {}).get("interview_rounds", []) if item.get("round_id") == data.round_id),
            None,
        )
        if configured_round is None:
            raise HTTPException(status_code=422, detail="Choose a configured interview round for this job.")
        if data.status == "scheduled":
            if data.duration_minutes != configured_round["duration_minutes"]:
                raise HTTPException(status_code=422, detail="Interview duration must match the job's round configuration.")
            if not set(configured_round["required_interviewers"]).issubset(set(data.interviewer_emails)):
                raise HTTPException(status_code=422, detail="Include every required interviewer calendar email for this round.")
        interview["round_name"] = configured_round["name"]
    schedule_changed = (
        interview["starts_at"] != existing.get("starts_at")
        or data.duration_minutes != existing.get("duration_minutes", 60)
        or data.interviewer_emails != existing.get("interviewer_emails", [])
    )
    if data.round_id and data.status == "scheduled":
        from app.api.v1.interview_workflow import _local_conflict

        if _local_conflict(
            db,
            list(data.interviewer_emails),
            interview["starts_at"],
            interview["starts_at"] + timedelta(minutes=data.duration_minutes),
            ignore_interview_id=interview_id,
        ):
            raise HTTPException(status_code=409, detail="A required interviewer already has an HRMS booking at this time.")
        if schedule_changed or existing.get("status") != "scheduled":
            _check_external_availability(
                list(data.interviewer_emails), interview["starts_at"], data.duration_minutes,
                bool(configured_round and configured_round.get("requires_hr")),
            )
    if data.round_id:
        interview.setdefault("round_name", data.round_name)
        override = db[PROGRESSION_OVERRIDES].find_one({
            "application_id": data.application_id,
            "round_id": data.round_id,
            "active": True,
        })
        if override:
            interview["progression_override_id"] = override["_id"]
    rescheduled = existing.get("status") == "scheduled" and data.status == "scheduled" and schedule_changed
    if data.status == "cancelled" or rescheduled:
        reason = "cancelled" if data.status == "cancelled" else "rescheduled"
        invalidate_meeting(db, existing, reason)
        revoke_tokens(db, interview_id, reason)
        if rescheduled:
            interview.update({"meeting_status": None, "meeting_url": None, "meeting_id": None, "meeting_expires_at": None})
    db[INTERVIEWS].update_one({"_id": interview_id}, {"$set": interview})
    sync_pipeline_from_interview(db, db[INTERVIEWS].find_one({"_id": interview_id}), user["_id"])
    if rescheduled and data.round_id:
        _notify_scheduled(db, db[INTERVIEWS].find_one({"_id": interview_id}), user["_id"])
    if (
        existing.get("status") != data.status
        or existing.get("outcome", "pending_evaluation") != data.outcome
        or existing.get("starts_at") != interview["starts_at"]
    ):
        db[AUDIT_LOGS].insert_one({
            "event": "interview_rescheduled_or_updated",
            "application_id": data.application_id,
            "job_id": application["job_id"],
            "interview_id": interview_id,
            "previous_status": existing.get("status"),
            "new_status": data.status,
            "previous_outcome": existing.get("outcome", "pending_evaluation"),
            "new_outcome": data.outcome,
            "previous_starts_at": existing.get("starts_at"),
            "new_starts_at": interview["starts_at"],
            "progression_override_id": interview.get("progression_override_id"),
            "user_id": user["_id"],
            "created_at": datetime.now(timezone.utc),
        })
    return serialize_interview(db, {"_id": interview_id, **interview})


@router.patch("/{interview_id}/outcome", response_model=InterviewRead)
def record_interview_outcome(
    interview_id: str,
    data: InterviewOutcomeUpdate,
    db: MongoDatabase,
    user: CurrentUser,
) -> InterviewRead:
    interview = db[INTERVIEWS].find_one({"_id": interview_id})
    if interview is None:
        raise HTTPException(status_code=404, detail="Interview not found.")
    if interview.get("status") != "completed":
        raise HTTPException(status_code=409, detail="Mark the interview completed before recording its outcome.")
    previous_outcome = interview.get("outcome", "pending_evaluation")
    db[INTERVIEWS].update_one(
        {"_id": interview_id, "status": "completed"},
        {"$set": {
            "outcome": data.outcome,
            "evaluation_notes": data.evaluation_notes,
            "evaluated_by": user["_id"],
            "evaluated_at": datetime.now(timezone.utc),
        }},
    )
    db[AUDIT_LOGS].insert_one({
        "event": "interview_outcome_recorded",
        "application_id": interview["application_id"],
        "job_id": interview.get("job_id"),
        "interview_id": interview_id,
        "round_id": interview.get("round_id"),
        "previous_outcome": previous_outcome,
        "new_outcome": data.outcome,
        "user_id": user["_id"],
        "created_at": datetime.now(timezone.utc),
    })
    updated = db[INTERVIEWS].find_one({"_id": interview_id})
    sync_pipeline_from_interview(db, updated, user["_id"])
    return serialize_interview(db, updated)
