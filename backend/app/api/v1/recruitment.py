import base64
import binascii
import hashlib
import hmac
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.api.deps import CurrentUser
from app.core.config import settings
from app.core.database import MongoDatabase
from app.models import (
    APPLICATIONS,
    AUDIT_LOGS,
    CANDIDATES,
    EMAIL_LOGS,
    INTERVIEWS,
    INTERVIEW_PLANS,
    JOBS,
    PROGRESSION_OVERRIDES,
    new_id,
)
from app.schemas import (
    ApplicationRead,
    ApplicationReview,
    AssistantRequest,
    EmailDraftRequest,
    EmailDraftUpdate,
    GoogleFormSubmission,
    JobCreate,
    JobRead,
    PublicApplicationReceipt,
    PublicJobRead,
    SemanticSearchRequest,
    ShortlistRequest,
    SemanticSearchResult,
)
from app.services.application_processing import process_application
from app.services.email_notifications import (
    EmailDeliveryError,
    EmailDeliveryRejected,
    EmailNotConfigured,
    send_email,
)
from app.services.resume_parser import extract_text, parse_resume
from app.services.recruitment_agent import (
    AgentContextNotFound,
    AgentNotConfigured,
    AgentProviderError,
    run_recruitment_agent,
)
from app.services.semantic_matching import MatchingNotConfigured, OpenAIEmbeddingClient, PineconeVectorIndex
from pydantic import EmailStr

router = APIRouter()
logger = logging.getLogger(__name__)
ALLOWED_FILES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}
PUBLIC_ALLOWED_FILES = {".pdf": "application/pdf", ".docx": ALLOWED_FILES[".docx"]}
JOB_FIELDS = (
    "title", "department", "description", "responsibilities", "required_skills",
    "preferred_skills", "minimum_experience", "maximum_experience",
    "education_requirements", "mandatory_criteria", "preferred_qualifications",
    "employment_type", "location", "openings", "opening_date", "deadline",
    "timezone", "questions", "scoring_weights", "status",
    "interview_rounds",
)


def validate_resume(filename: str, content_type: str, content: bytes) -> tuple[str, str]:
    extension = Path(filename).suffix.lower()
    expected_type = ALLOWED_FILES.get(extension)
    if expected_type is None:
        raise HTTPException(status_code=415, detail="Unsupported resume format. Upload a PDF, DOCX, or TXT file.")
    if content_type not in (expected_type, "application/octet-stream"):
        raise HTTPException(status_code=415, detail="Resume file type does not match its extension.")
    if not content or len(content) > settings.max_resume_size_bytes:
        raise HTTPException(status_code=413, detail="Resume must be non-empty and no larger than 5 MB.")
    if extension == ".pdf" and not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="The uploaded file is not a valid PDF.")
    if extension == ".docx" and not content.startswith(b"PK"):
        raise HTTPException(status_code=415, detail="The uploaded file is not a valid DOCX document.")
    if extension == ".txt":
        try:
            content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise HTTPException(status_code=415, detail="Text resumes must use UTF-8 encoding.")
    return extension, expected_type


def save_resume(filename: str, content: bytes, extension: str) -> tuple[str, str]:
    stored_name = f"{new_id().replace('-', '')}{extension}"
    storage = settings.resume_storage_dir.resolve()
    storage.mkdir(parents=True, exist_ok=True)
    target = storage / stored_name
    try:
        target.write_bytes(content)
    except OSError:
        logger.exception("Unable to save resume to configured private storage")
        raise HTTPException(status_code=500, detail="Resume could not be stored. Please try again.")
    return stored_name, str(target)


def job_response(job: dict, db: MongoDatabase | None = None) -> dict:
    result = {
        "id": job["_id"],
        **{key: job.get(key) for key in JOB_FIELDS if key != "interview_rounds"},
        "interview_rounds": job.get("interview_rounds", []),
    }
    result["is_active"] = job.get("status") == "published"
    result["public_url"] = (
        f"{settings.public_application_url.rstrip('/')}/{job['public_token']}"
        if job.get("public_token") else None
    )
    if db is not None:
        result["application_count"] = db[APPLICATIONS].count_documents({"job_id": job["_id"]})
    return result


def public_job_response(job: dict) -> PublicJobRead:
    return PublicJobRead(
        public_token=job["public_token"],
        title=job["title"],
        department=job.get("department"),
        description=job.get("description"),
        responsibilities=job.get("responsibilities", []),
        required_skills=job.get("required_skills", []),
        preferred_skills=job.get("preferred_skills", []),
        minimum_experience=job.get("minimum_experience"),
        education_requirements=job.get("education_requirements", []),
        location=job.get("location"),
        employment_type=job.get("employment_type"),
        opening_date=job.get("opening_date"),
        deadline=job.get("deadline"),
        timezone=job.get("timezone", "UTC"),
        questions=job.get("questions", []),
        status=job["status"],
    )


def application_response(application: dict, candidate: dict, job: dict) -> ApplicationRead:
    payload = {
        "id": application["_id"],
        "stage": application["stage"],
        "parsed_resume": application["parsed_resume"],
        "resume_filename": application["resume_filename"],
        "resume_content_type": application["resume_content_type"],
        "resume_size": application["resume_size"],
        "applied_at": application["applied_at"],
        "candidate": {
            "id": candidate["_id"],
            "name": candidate["name"],
            "email": candidate["email"],
            "phone": candidate.get("phone"),
            "skills": candidate.get("skills", []),
            "experience_years": candidate.get("experience_years"),
            "education": candidate.get("education"),
        },
        "job": {
            "id": job["_id"],
            "title": job["title"],
            "department": job.get("department"),
            "description": job.get("description"),
            "is_active": job.get("status") == "published",
            "status": job.get("status", "draft"),
            "public_url": (
                f"{settings.public_application_url.rstrip('/')}/{job['public_token']}"
                if job.get("public_token") else None
            ),
            **{
                key: job.get(key, [] if key == "interview_rounds" else None)
                for key in JOB_FIELDS
                if key != "status"
            },
        },
        **{
            key: application.get(key, default)
            for key, default in (
                ("pipeline_status", None),
                ("assessment", None),
                ("shortlist_status", None),
                ("shortlist_rank", None),
                ("processing_status", "pending"),
                ("parsing_status", "pending"),
                ("embedding_status", "pending"),
                ("matching_status", "pending"),
                ("ats_score", None),
                ("criterion_scores", {}),
                ("matching_evidence", []),
                ("missing_criteria", []),
                ("mandatory_criteria_results", []),
                ("recommendation", "pending"),
                ("confidence", "unknown"),
                ("scoring_version", None),
                ("hr_notes", ""),
                ("additional_answers", {}),
            )
        },
    }
    return ApplicationRead.model_validate(payload)


def read_application(db: MongoDatabase, application: dict) -> ApplicationRead:
    candidate = db[CANDIDATES].find_one({"_id": application["candidate_id"]})
    job = db[JOBS].find_one({"_id": application["job_id"]})
    if candidate is None or job is None:
        logger.error("Application references missing candidate or job: %s", application["_id"])
        raise HTTPException(status_code=500, detail="Application data is incomplete. Contact an administrator.")
    return application_response(application, candidate, job)


def create_application(
    *,
    db: MongoDatabase,
    background_tasks: BackgroundTasks,
    name: str,
    email: str,
    phone: str | None,
    job_title: str,
    filename: str,
    content_type: str,
    content: bytes,
) -> ApplicationRead:
    extension, safe_content_type = validate_resume(filename, content_type, content)
    try:
        parsed = parse_resume(extract_text(filename, content))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"Resume could not be parsed: {exc}")

    normalized_email = email.strip().lower()
    job = db[JOBS].find_one({"title_normalized": job_title.strip().lower(), "status": "published"})
    if job is None:
        raise HTTPException(status_code=422, detail="The selected job is not currently accepting applications.")
    candidate = db[CANDIDATES].find_one({"email": normalized_email})
    candidate_id = candidate["_id"] if candidate else new_id()
    if db[APPLICATIONS].find_one({"candidate_id": candidate_id, "job_id": job["_id"]}, {"_id": 1}):
        raise HTTPException(status_code=409, detail="An application for this email and job already exists.")

    now = datetime.now(timezone.utc)
    candidate_data = {
        "_id": candidate_id,
        "name": name.strip(),
        "email": normalized_email,
        "phone": phone.strip() if phone and phone.strip() else parsed["phone"],
        "skills": parsed["skills"],
        "experience_years": parsed["experience_years"],
        "education": parsed["education"],
        "created_at": candidate["created_at"] if candidate else now,
        "updated_at": now,
    }
    stored_name, stored_path = save_resume(filename, content, extension)
    application = {
        "_id": new_id(),
        "candidate_id": candidate_id,
        "job_id": job["_id"],
        "stage": "needs_review",
        "parsed_resume": parsed,
        "resume_path": stored_path,
        "resume_filename": Path(filename).name,
        "resume_content_type": safe_content_type,
        "resume_size": len(content),
        "resume_sha256": hashlib.sha256(content).hexdigest(),
        "applied_at": now,
        "updated_at": now,
        "processing_status": "pending",
        "parsing_status": "pending",
        "embedding_status": "pending",
        "matching_status": "pending",
        "ats_score": None,
        "criterion_scores": {},
        "matching_evidence": [],
        "missing_criteria": [],
        "mandatory_criteria_results": [],
        "recommendation": "pending",
        "confidence": "unknown",
        "scoring_version": None,
        "hr_notes": "",
        "additional_answers": {},
        "candidate_experience_years": parsed["experience_years"],
        "candidate_education": parsed["education"],
        "candidate_skills": [skill.casefold() for skill in parsed["skills"]],
    }
    try:
        if candidate is None:
            db[CANDIDATES].insert_one(candidate_data)
        else:
            db[CANDIDATES].update_one({"_id": candidate_id}, {"$set": candidate_data})
        db[APPLICATIONS].insert_one(application)
    except DuplicateKeyError:
        Path(stored_path).unlink(missing_ok=True)
        if candidate is None:
            db[CANDIDATES].delete_one({"_id": candidate_id})
        raise HTTPException(status_code=409, detail="An application for this email and job already exists.")
    except PyMongoError:
        Path(stored_path).unlink(missing_ok=True)
        if candidate is None:
            db[CANDIDATES].delete_one({"_id": candidate_id})
        logger.exception("Unable to persist candidate application")
        raise HTTPException(status_code=500, detail="Application could not be saved. Please try again.")
    background_tasks.add_task(process_application, db, application["_id"])
    candidate_data["_id"] = candidate_id
    background_tasks.add_task(process_application, db, application["_id"])
    return application_response(application, candidate_data, job)


@router.get("/jobs", response_model=list[JobRead])
def list_jobs(
    db: MongoDatabase,
    user: CurrentUser,
    status_filter: str | None = Query(default=None, alias="status", pattern="^(draft|published|closed|archived)$"),
) -> list[dict]:
    query = {"status": status_filter} if status_filter else {}
    return [job_response(job, db) for job in db[JOBS].find(query).sort("created_at", -1)]


@router.post("/jobs", response_model=JobRead, status_code=status.HTTP_201_CREATED)
def create_job(data: JobCreate, db: MongoDatabase, user: CurrentUser) -> dict:
    title = data.title.strip()
    job = {
        "_id": new_id(),
        "title": title,
        "title_normalized": title.lower(),
        **data.model_dump(),
        "created_by": user["_id"],
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    if data.status == "published":
        _validate_job_publish(job)
        import secrets

        job["public_token"] = secrets.token_urlsafe(24)
    try:
        db[JOBS].insert_one(job)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="A job with this title already exists.")
    return job_response(job, db)


def _validate_job_publish(job: dict) -> None:
    if not job.get("description") and not job.get("responsibilities"):
        raise HTTPException(status_code=422, detail="Add a job description or responsibilities before publishing.")
    opening = job.get("opening_date")
    deadline = job.get("deadline")
    if opening and deadline and deadline <= opening:
        raise HTTPException(status_code=422, detail="Application deadline must be after the opening date.")
    if job.get("minimum_experience") is not None and job.get("maximum_experience") is not None:
        if job["maximum_experience"] < job["minimum_experience"]:
            raise HTTPException(status_code=422, detail="Maximum experience must be greater than or equal to minimum experience.")


@router.get("/jobs/{job_id}", response_model=JobRead)
def get_job(job_id: str, db: MongoDatabase, user: CurrentUser) -> dict:
    job = db[JOBS].find_one({"_id": job_id})
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job_response(job, db)


@router.patch("/jobs/{job_id}", response_model=JobRead)
def update_job(job_id: str, data: JobCreate, db: MongoDatabase, user: CurrentUser) -> dict:
    existing = db[JOBS].find_one({"_id": job_id})
    if existing is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if existing.get("status") in ("closed", "archived"):
        raise HTTPException(status_code=409, detail="Closed or archived jobs cannot be edited.")
    fields = data.model_dump()
    if data.status == "published":
        _validate_job_publish(fields)
    patch = {**fields, "title_normalized": data.title.lower(), "updated_at": datetime.now(timezone.utc)}
    if data.status == "published":
        patch["public_token"] = existing.get("public_token")
        if not patch["public_token"]:
            import secrets

            patch["public_token"] = secrets.token_urlsafe(24)
    try:
        db[JOBS].update_one({"_id": job_id}, {"$set": patch})
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="A job with this title already exists.")
    return job_response(db[JOBS].find_one({"_id": job_id}), db)


@router.post("/jobs/{job_id}/publish", response_model=JobRead)
def publish_job(job_id: str, db: MongoDatabase, user: CurrentUser) -> dict:
    job = db[JOBS].find_one({"_id": job_id})
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.get("status") in ("closed", "archived"):
        raise HTTPException(status_code=409, detail="Closed or archived jobs cannot be published.")
    _validate_job_publish(job)
    import secrets

    token = job.get("public_token") or secrets.token_urlsafe(24)
    db[JOBS].update_one(
        {"_id": job_id},
        {"$set": {"status": "published", "public_token": token, "updated_at": datetime.now(timezone.utc)}},
    )
    db[AUDIT_LOGS].insert_one({"event": "job_published", "job_id": job_id, "user_id": user["_id"], "created_at": datetime.now(timezone.utc)})
    return job_response(db[JOBS].find_one({"_id": job_id}), db)


@router.post("/jobs/{job_id}/close", response_model=JobRead)
def close_job(job_id: str, db: MongoDatabase, user: CurrentUser) -> dict:
    result = db[JOBS].update_one(
        {"_id": job_id, "status": {"$in": ["draft", "published"]}},
        {"$set": {"status": "closed", "updated_at": datetime.now(timezone.utc)}},
    )
    if not result.matched_count:
        raise HTTPException(status_code=404, detail="Open job not found.")
    job = db[JOBS].find_one({"_id": job_id})
    db[AUDIT_LOGS].insert_one({"event": "job_closed", "job_id": job_id, "user_id": user["_id"], "created_at": datetime.now(timezone.utc)})
    return job_response(job, db)


@router.post("/jobs/{job_id}/archive", response_model=JobRead)
def archive_job(job_id: str, db: MongoDatabase, user: CurrentUser) -> dict:
    result = db[JOBS].update_one(
        {"_id": job_id, "status": {"$in": ["closed", "draft"]}},
        {"$set": {"status": "archived", "updated_at": datetime.now(timezone.utc)}},
    )
    if not result.matched_count:
        raise HTTPException(status_code=409, detail="Close a published job before archiving it.")
    return job_response(db[JOBS].find_one({"_id": job_id}), db)


def _shortlist_rows(db: MongoDatabase, job_id: str) -> list[dict]:
    rows = []
    for application in db[APPLICATIONS].find({"job_id": job_id, "shortlist_status": {"$exists": True}}):
        candidate = db[CANDIDATES].find_one({"_id": application["candidate_id"]}) or {}
        assessment = application.get("assessment") or {}
        rows.append({
            "application_id": application["_id"],
            "candidate_name": candidate.get("name"),
            "candidate_email": candidate.get("email"),
            "rank": application.get("shortlist_rank"),
            "status": application.get("shortlist_status"),
            "score": assessment.get("overall"),
            "explanation": assessment.get("explanation"),
            "required_skills": assessment.get("required_skills", []),
            "evidence_gaps": assessment.get("evidence_gaps", []),
            "stage": application.get("stage"),
        })
    rows.sort(key=lambda row: (row["rank"] is None, row["rank"] or 0, -(row["score"] or 0)))
    return rows


@router.get("/jobs/{job_id}/shortlist")
def get_shortlist(job_id: str, db: MongoDatabase, user: CurrentUser) -> dict:
    job = db[JOBS].find_one({"_id": job_id})
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return {"job_id": job_id, "limit": job.get("shortlist_limit"), "ran_at": job.get("shortlist_run_at"), "results": _shortlist_rows(db, job_id)}


def run_shortlisting(db: MongoDatabase, job: dict, user: dict, limit: int | None, apply: bool, reassess: bool) -> dict:
    """Assess every application against the job description, rank the eligible ones and persist the result."""
    from app.services import shortlisting

    job_id = job["_id"]
    limit = limit or job.get("shortlist_limit") or settings.shortlist_default_limit
    applications = [a for a in db[APPLICATIONS].find({"job_id": job_id}) if a.get("stage") not in ("hired", "rejected")]
    if not applications:
        raise HTTPException(status_code=409, detail="This job has no applications to shortlist yet.")
    embedder, llm = shortlisting.default_services()
    assessed = []
    for application in applications:
        assessment = application.get("assessment")
        if reassess or not assessment or assessment.get("version") != shortlisting.VERSION:
            candidate = db[CANDIDATES].find_one({"_id": application["candidate_id"]}) or {}
            assessment = shortlisting.assess_resume_file(job, candidate, application, embedder, llm)
        assessed.append((application, assessment))
    rows = shortlisting.rank_and_status(assessed, limit)
    now = datetime.now(timezone.utc)
    moved = 0
    for row in rows:
        application = row["application"]
        update = {
            "assessment": row["assessment"], "shortlist_status": row["status"], "shortlist_rank": row["rank"],
            "shortlist_assessed_at": now, "updated_at": now,
        }
        if apply and row["status"] == "shortlisted" and application.get("stage") == "needs_review":
            update.update(stage="screening", pipeline_status="Shortlisted from resume screening")
            moved += 1
            db[AUDIT_LOGS].insert_one({
                "event": "application_shortlisted_from_resume", "application_id": application["_id"], "job_id": job_id,
                "rank": row["rank"], "score": row["assessment"]["overall"], "user_id": user["_id"], "created_at": now,
            })
        db[APPLICATIONS].update_one({"_id": application["_id"]}, {"$set": update})
    db[JOBS].update_one({"_id": job_id}, {"$set": {"shortlist_limit": limit, "shortlist_run_at": now}})
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {
        "job_id": job_id, "limit": limit, "ran_at": now, "counts": counts, "moved_to_screening": moved,
        "semantic_used": any(row["assessment"].get("semantic_used") for row in rows),
        "llm_used": any(row["assessment"].get("llm_used") for row in rows),
        "results": _shortlist_rows(db, job_id),
    }


@router.post("/jobs/{job_id}/shortlist")
def run_shortlist(job_id: str, data: ShortlistRequest, db: MongoDatabase, user: CurrentUser) -> dict:
    job = db[JOBS].find_one({"_id": job_id})
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return run_shortlisting(db, job, user, data.limit, data.apply, data.reassess)


def _delete_application_data(db: MongoDatabase, application: dict) -> None:
    """Remove one application and its dependent records; external clean-up is best effort."""
    application_id = application["_id"]
    scheduled = list(db[INTERVIEWS].find({
        "application_id": application_id,
        "status": "scheduled",
        "calendar_event_id": {"$exists": True, "$ne": None},
    }))
    if scheduled:
        try:
            from app.services.google_calendar import GoogleCalendar

            calendar = GoogleCalendar()
            for interview in scheduled:
                calendar.delete_event(interview["calendar_event_id"])
        except Exception:
            logger.warning("Could not cancel Google Calendar events for deleted application %s", application_id)
    try:
        path = Path(application.get("resume_path") or "").resolve()
        if settings.resume_storage_dir.resolve() in path.parents:
            path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not remove resume file for deleted application %s", application_id)
    if settings.pinecone_api_key:
        try:
            PineconeVectorIndex().delete_application_vectors(application_id)
        except Exception:
            logger.warning("Could not remove resume vectors for deleted application %s", application_id)
    db[INTERVIEWS].delete_many({"application_id": application_id})
    db[EMAIL_LOGS].delete_many({"application_id": application_id})
    db[PROGRESSION_OVERRIDES].delete_many({"application_id": application_id})
    db[APPLICATIONS].delete_one({"_id": application_id})
    if db[APPLICATIONS].count_documents({"candidate_id": application["candidate_id"]}) == 0:
        db[CANDIDATES].delete_one({"_id": application["candidate_id"]})


@router.delete("/jobs/{job_id}")
def delete_job(
    job_id: str,
    db: MongoDatabase,
    user: CurrentUser,
    delete_applications: bool = Query(default=False),
) -> dict:
    job = db[JOBS].find_one({"_id": job_id})
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    applications = list(db[APPLICATIONS].find({"job_id": job_id}))
    if applications and not delete_applications:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This form has {len(applications)} application{'s' if len(applications) != 1 else ''}. "
                "Confirm that you also want to delete them."
            ),
        )
    for application in applications:
        _delete_application_data(db, application)
    db[INTERVIEW_PLANS].delete_many({"job_id": job_id})
    db[JOBS].delete_one({"_id": job_id})
    db[AUDIT_LOGS].insert_one({
        "event": "job_deleted",
        "job_id": job_id,
        "job_title": job.get("title"),
        "deleted_applications": len(applications),
        "user_id": user["_id"],
        "created_at": datetime.now(timezone.utc),
    })
    return {"status": "deleted", "job_id": job_id, "deleted_applications": len(applications)}


@router.get("/public/jobs/{public_token}", response_model=PublicJobRead)
def get_public_job(public_token: str, db: MongoDatabase) -> PublicJobRead:
    job = db[JOBS].find_one({"public_token": public_token, "status": "published"})
    if job is None:
        raise HTTPException(status_code=404, detail="This job is not accepting public applications.")
    return public_job_response(job)


@router.post(
    "/public/jobs/{public_token}/applications",
    response_model=PublicApplicationReceipt,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_public_application(
    public_token: str,
    background_tasks: BackgroundTasks,
    db: MongoDatabase,
    name: str = Form(min_length=1, max_length=180),
    email: EmailStr = Form(...),
    phone: str | None = Form(default=None, max_length=40),
    education: str | None = Form(default=None, max_length=5000),
    experience_years: float | None = Form(default=None, ge=0, le=80),
    answers: str = Form(default="{}"),
    resume: UploadFile = File(...),
) -> PublicApplicationReceipt:
    job = db[JOBS].find_one({"public_token": public_token, "status": "published"})
    if job is None:
        raise HTTPException(status_code=404, detail="This job is not accepting public applications.")
    now = datetime.now(timezone.utc)
    opening = job.get("opening_date")
    deadline = job.get("deadline")
    if opening and opening > now:
        localized_opening = opening.astimezone(ZoneInfo(job.get("timezone", "UTC"))).isoformat()
        raise HTTPException(status_code=409, detail=f"Applications open on {localized_opening}.")
    if deadline and deadline <= now:
        raise HTTPException(status_code=410, detail="The application deadline for this job has passed.")
    if job.get("education_requirements") and not (education or "").strip():
        raise HTTPException(status_code=422, detail="Education details are required for this position.")
    try:
        import json

        answers_data = json.loads(answers)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="Additional answers must be valid JSON.")
    if not isinstance(answers_data, dict) or any(not isinstance(value, str) for value in answers_data.values()):
        raise HTTPException(status_code=422, detail="Additional answers must be a JSON object of text answers.")
    questions = {question["key"]: question for question in job.get("questions", [])}
    for key, question in questions.items():
        if question.get("required") and not str(answers_data.get(key, "")).strip():
            raise HTTPException(status_code=422, detail=f"Answer required: {question['label']}")
    if set(answers_data) - set(questions):
        raise HTTPException(status_code=422, detail="Application contains an unknown job-specific question.")

    extension = Path(resume.filename or "").suffix.lower()
    expected_type = PUBLIC_ALLOWED_FILES.get(extension)
    if expected_type is None:
        raise HTTPException(status_code=415, detail="Upload a PDF or DOCX resume.")
    if resume.content_type not in (expected_type, "application/octet-stream"):
        raise HTTPException(status_code=415, detail="Resume file type does not match its extension.")
    content = await resume.read(settings.max_resume_size_bytes + 1)
    if not content or len(content) > settings.max_resume_size_bytes:
        raise HTTPException(status_code=413, detail="Resume must be non-empty and no larger than 5 MB.")
    if extension == ".pdf" and not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="The uploaded file is not a valid PDF.")
    if extension == ".docx" and not content.startswith(b"PK"):
        raise HTTPException(status_code=415, detail="The uploaded file is not a valid DOCX document.")
    try:
        resume_text = extract_text(resume.filename or "resume", content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Resume could not be read: {exc}")
    parsed = parse_resume(resume_text)
    normalized_email = email.strip().lower()
    candidate = db[CANDIDATES].find_one({"email": normalized_email})
    candidate_id = candidate["_id"] if candidate else new_id()
    if db[APPLICATIONS].find_one({"candidate_id": candidate_id, "job_id": job["_id"]}, {"_id": 1}):
        raise HTTPException(status_code=409, detail="You have already applied for this job.")

    stored_name, stored_path = save_resume(resume.filename or f"resume{extension}", content, extension)
    application_id = new_id()
    candidate_data = {
        "_id": candidate_id,
        "name": name.strip(),
        "email": normalized_email,
        "phone": phone.strip() if phone and phone.strip() else parsed["phone"],
        "skills": parsed["skills"],
        "experience_years": experience_years if experience_years is not None else parsed["experience_years"],
        "education": education or parsed["education"],
        "experience": parsed["experience"],
        "projects": parsed["projects"],
        "certifications": parsed["certifications"],
        "created_at": candidate.get("created_at", now) if candidate else now,
        "updated_at": now,
    }
    application = {
        "_id": application_id,
        "candidate_id": candidate_id,
        "job_id": job["_id"],
        "stage": "needs_review",
        "parsed_resume": parsed,
        "resume_path": stored_path,
        "resume_filename": Path(resume.filename or "resume").name,
        "resume_content_type": expected_type,
        "resume_size": len(content),
        "resume_sha256": hashlib.sha256(content).hexdigest(),
        "applied_at": now,
        "updated_at": now,
        "processing_status": "pending",
        "parsing_status": "completed",
        "embedding_status": "pending",
        "matching_status": "pending",
        "ats_score": None,
        "criterion_scores": {},
        "matching_evidence": [],
        "missing_criteria": [],
        "mandatory_criteria_results": [],
        "recommendation": "pending",
        "confidence": "unknown",
        "scoring_version": None,
        "hr_notes": "",
        "additional_answers": answers_data,
        "candidate_experience_years": experience_years,
        "candidate_education": education.casefold() if education else (parsed["education"] or "").casefold(),
        "candidate_skills": [skill.casefold() for skill in parsed["skills"]],
    }
    try:
        if candidate:
            db[CANDIDATES].update_one({"_id": candidate_id}, {"$set": candidate_data})
        else:
            db[CANDIDATES].insert_one(candidate_data)
        db[APPLICATIONS].insert_one(application)
    except DuplicateKeyError:
        Path(stored_path).unlink(missing_ok=True)
        if candidate is None:
            db[CANDIDATES].delete_one({"_id": candidate_id})
        raise HTTPException(status_code=409, detail="You have already applied for this job.")
    except PyMongoError:
        Path(stored_path).unlink(missing_ok=True)
        if candidate is None:
            db[CANDIDATES].delete_one({"_id": candidate_id})
        logger.exception("Unable to save public application")
        raise HTTPException(status_code=500, detail="Application could not be saved. Please try again.")
    background_tasks.add_task(process_application, db, application_id)
    return PublicApplicationReceipt(
        application_id=application_id,
        status="submitted",
        message="Application received. The HR team will review your application.",
    )


@router.get("/applications", response_model=list[ApplicationRead])
def list_applications(
    db: MongoDatabase,
    user: CurrentUser,
    search: str | None = Query(default=None, max_length=200),
    stage: str | None = Query(default=None, pattern="^(needs_review|screening|interview|offer|hired|rejected)$"),
    minimum_score: float | None = Query(default=None, ge=0, le=100),
    skill: str | None = Query(default=None, max_length=100),
    minimum_experience: float | None = Query(default=None, ge=0, le=80),
    education: str | None = Query(default=None, max_length=200),
) -> list[ApplicationRead]:
    filters: dict = {}
    if stage:
        filters["stage"] = stage
    if minimum_score is not None:
        filters["ats_score"] = {"$gte": minimum_score}
    if minimum_experience is not None:
        filters["candidate_experience_years"] = {"$gte": minimum_experience}
    if education:
        filters["candidate_education"] = {"$regex": re.escape(education), "$options": "i"}
    if search and search.strip():
        pattern = re.escape(search.strip())
        candidate_ids = [
            candidate["_id"]
            for candidate in db[CANDIDATES].find(
                {"$or": [{"name": {"$regex": pattern, "$options": "i"}}, {"email": {"$regex": pattern, "$options": "i"}}]},
                {"_id": 1},
            )
        ]
        job_ids = [
            job["_id"]
            for job in db[JOBS].find({"title": {"$regex": pattern, "$options": "i"}}, {"_id": 1})
        ]
        filters["$or"] = [{"candidate_id": {"$in": candidate_ids}}, {"job_id": {"$in": job_ids}}]
    if skill and skill.strip():
        filters["candidate_skills"] = {"$in": [skill.strip().casefold()]}
    applications = db[APPLICATIONS].find(filters).sort(
        [("ats_score", -1), ("applied_at", -1)] if minimum_score is not None else [("applied_at", -1)]
    )
    return [read_application(db, application) for application in applications]


@router.get("/dashboard/summary")
def dashboard_summary(db: MongoDatabase, user: CurrentUser) -> dict:
    stages = list(db[APPLICATIONS].aggregate([{"$group": {"_id": "$stage", "count": {"$sum": 1}}}]))
    counts = {stage["_id"]: stage["count"] for stage in stages}
    return {
        "total_applications": sum(counts.values()),
        "needs_review": counts.get("needs_review", 0),
        "in_interview": counts.get("interview", 0),
        "open_jobs": db[JOBS].count_documents({"is_active": True}),
    }


@router.get("/applications/{application_id}", response_model=ApplicationRead)
def get_application(application_id: str, db: MongoDatabase, user: CurrentUser) -> ApplicationRead:
    application = db[APPLICATIONS].find_one({"_id": application_id})
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    return read_application(db, application)


@router.get("/jobs/{job_id}/applications", response_model=list[ApplicationRead])
def list_job_applications(
    job_id: str,
    db: MongoDatabase,
    user: CurrentUser,
    stage: str | None = Query(default=None, pattern="^(needs_review|screening|interview|offer|hired|rejected)$"),
    minimum_score: float | None = Query(default=None, ge=0, le=100),
    skill: str | None = Query(default=None, max_length=100),
    minimum_experience: float | None = Query(default=None, ge=0, le=80),
    education: str | None = Query(default=None, max_length=200),
) -> list[ApplicationRead]:
    if db[JOBS].find_one({"_id": job_id}, {"_id": 1}) is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    query: dict = {"job_id": job_id}
    if stage:
        query["stage"] = stage
    if minimum_score is not None:
        query["ats_score"] = {"$gte": minimum_score}
    if minimum_experience is not None:
        query["candidate_experience_years"] = {"$gte": minimum_experience}
    if education:
        query["candidate_education"] = {"$regex": re.escape(education), "$options": "i"}
    if skill:
        query["candidate_skills"] = {"$in": [skill.strip().casefold()]}
    applications = db[APPLICATIONS].find(query).sort(
        [("ats_score", -1), ("applied_at", -1)] if minimum_score is not None else [("applied_at", -1)]
    )
    return [read_application(db, application) for application in applications]


@router.get("/applications/{application_id}/match-details", response_model=ApplicationRead)
def get_match_details(application_id: str, db: MongoDatabase, user: CurrentUser) -> ApplicationRead:
    return get_application(application_id, db, user)


@router.post("/applications/{application_id}/rerun-matching", status_code=status.HTTP_202_ACCEPTED)
def rerun_matching(
    application_id: str,
    background_tasks: BackgroundTasks,
    db: MongoDatabase,
    user: CurrentUser,
) -> dict:
    application = db[APPLICATIONS].find_one({"_id": application_id})
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    db[APPLICATIONS].update_one(
        {"_id": application_id},
        {
            "$set": {
                "processing_status": "pending",
                "embedding_status": "pending",
                "matching_status": "pending",
                "updated_at": datetime.now(timezone.utc),
            },
            "$unset": {"processing_error": ""},
        },
    )
    db[AUDIT_LOGS].insert_one({
        "event": "matching_rerun_requested",
        "application_id": application_id,
        "user_id": user["_id"],
        "created_at": datetime.now(timezone.utc),
    })
    background_tasks.add_task(process_application, db, application_id)
    return {"status": "pending", "message": "Matching was queued. Check the application status shortly."}


@router.get("/jobs/{job_id}/matching-status")
def get_matching_status(job_id: str, db: MongoDatabase, user: CurrentUser) -> dict:
    if db[JOBS].find_one({"_id": job_id}, {"_id": 1}) is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    summary = list(db[APPLICATIONS].aggregate([
        {"$match": {"job_id": job_id}},
        {"$group": {"_id": "$matching_status", "count": {"$sum": 1}}},
    ]))
    return {"job_id": job_id, "statuses": {item["_id"]: item["count"] for item in summary}}


@router.post("/assistant")
def recruitment_assistant(data: AssistantRequest, db: MongoDatabase, user: CurrentUser) -> dict:
    try:
        result = run_recruitment_agent(
            message=data.message.strip(),
            history=[item.model_dump() for item in data.history],
            job_id=data.job_id,
            application_id=data.application_id,
            db=db,
            user=user,
            context=data.context,
        )
    except AgentNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except AgentContextNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except AgentProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return result


@router.post("/jobs/{job_id}/semantic-search", response_model=list[SemanticSearchResult])
def semantic_search(
    job_id: str,
    data: SemanticSearchRequest,
    db: MongoDatabase,
    user: CurrentUser,
) -> list[SemanticSearchResult]:
    job = db[JOBS].find_one({"_id": job_id})
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    try:
        embedding = OpenAIEmbeddingClient().embed([data.query])[0]
        matches = PineconeVectorIndex().query(embedding, job_id=job_id, top_k=data.limit * 3)
    except MatchingNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("Semantic search failed for job %s (%s)", job_id, type(exc).__name__)
        raise HTTPException(status_code=502, detail="Semantic search is temporarily unavailable.")
    results = []
    seen = set()
    for match in matches:
        metadata = match["metadata"]
        application_id = metadata.get("application_id")
        if not application_id or application_id in seen:
            continue
        application = db[APPLICATIONS].find_one({"_id": application_id, "job_id": job_id})
        candidate = db[CANDIDATES].find_one({"_id": application["candidate_id"]}) if application else None
        if application is None or candidate is None:
            continue
        seen.add(application_id)
        results.append(SemanticSearchResult(
            application_id=application_id,
            candidate_name=candidate["name"],
            job_title=job["title"],
            score=round(match["score"] * 100, 1),
            evidence=str(metadata.get("text", ""))[:700],
        ))
        if len(results) >= data.limit:
            break
    return results


@router.post("/applications/{application_id}/email-drafts", status_code=status.HTTP_201_CREATED)
def create_email_draft(
    application_id: str,
    data: EmailDraftRequest,
    db: MongoDatabase,
    user: CurrentUser,
) -> dict:
    application = db[APPLICATIONS].find_one({"_id": application_id})
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    candidate = db[CANDIDATES].find_one({"_id": application["candidate_id"]})
    job = db[JOBS].find_one({"_id": application["job_id"]})
    if candidate is None or job is None:
        raise HTTPException(status_code=404, detail="Candidate or job details are unavailable.")
    if data.email_type == "rejection" and application.get("stage") != "rejected":
        raise HTTPException(status_code=409, detail="Record the authorized rejection decision before preparing a rejection email.")
    communication_context_id = None
    if data.email_type == "shortlist":
        subject = f"Next steps for {job['title']}"
        greeting = "We would like to continue the conversation about your application."
    elif data.email_type == "rejection":
        subject = f"An update on your {job['title']} application"
        greeting = "Thank you for taking the time to apply. We will not be moving forward with your application at this time."
    elif data.email_type == "follow_up":
        subject = f"Following up on your {job['title']} application"
        greeting = "We are following up regarding your application."
    else:
        interview = db[INTERVIEWS].find_one(
            {"application_id": application_id, "status": "scheduled", "starts_at": {"$gte": datetime.now(timezone.utc)}},
            sort=[("starts_at", 1)],
        )
        if interview is None:
            raise HTTPException(status_code=409, detail="Schedule an interview before preparing an invitation.")
        communication_context_id = interview["_id"]
        local_start = interview["starts_at"].astimezone(ZoneInfo(interview["timezone"]))
        local_end = local_start + timedelta(minutes=interview.get("duration_minutes", 60))
        subject = f"{interview.get('round_name') or 'Interview'} invitation — {job['title']}"
        greeting = (
            f"We would like to invite you to a {interview.get('round_name') or 'scheduled'} interview "
            f"for {job['title']} on {local_start.strftime('%A, %B %d, %Y')} from "
            f"{local_start.strftime('%I:%M %p')} to {local_end.strftime('%I:%M %p')} "
            f"({interview['timezone']})."
        )
        if interview.get("location"):
            greeting += f"\nLocation or meeting link: {interview['location']}"
    body = f"Hello {candidate['name']},\n\n{greeting}\n\n"
    if data.message.strip():
        body += f"{data.message.strip()}\n\n"
    body += f"Regards,\n{job.get('department') or 'The hiring team'}"
    key = hashlib.sha256(
        f"{application_id}:{data.email_type}:{communication_context_id or ''}".encode()
    ).hexdigest()
    draft = {
        "_id": new_id(),
        "application_id": application_id,
        "candidate_id": candidate["_id"],
        "job_id": job["_id"],
        "email_type": data.email_type,
        "communication_context_id": communication_context_id,
        "recipient": candidate["email"],
        "subject": subject,
        "body": body,
        "approval_status": "draft",
        "delivery_status": "not_sent",
        "provider_message_id": None,
        "idempotency_key": key,
        "created_by": user["_id"],
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    existing = db["email_logs"].find_one({"idempotency_key": key})
    if existing:
        if existing["approval_status"] != "draft" or existing["delivery_status"] != "not_sent":
            raise HTTPException(status_code=409, detail="An email send attempt already exists for this application and type.")
        updated = db["email_logs"].update_one(
            {
                "_id": existing["_id"],
                "approval_status": "draft",
                "delivery_status": "not_sent",
            },
            {"$set": {"subject": subject, "body": body, "updated_at": datetime.now(timezone.utc)}},
        )
        if updated.matched_count != 1:
            raise HTTPException(status_code=409, detail="This email draft is already being sent.")
        draft = db["email_logs"].find_one({"_id": existing["_id"]})
    else:
        try:
            db["email_logs"].insert_one(draft)
        except DuplicateKeyError:
            existing = db["email_logs"].find_one({"idempotency_key": key})
            if not existing or existing["approval_status"] != "draft" or existing["delivery_status"] != "not_sent":
                raise HTTPException(status_code=409, detail="An email send attempt already exists.")
            updated = db["email_logs"].update_one(
                {
                    "_id": existing["_id"],
                    "approval_status": "draft",
                    "delivery_status": "not_sent",
                },
                {"$set": {"subject": subject, "body": body, "updated_at": datetime.now(timezone.utc)}},
            )
            if updated.matched_count != 1:
                raise HTTPException(status_code=409, detail="This email draft is already being sent.")
            draft = db["email_logs"].find_one({"_id": existing["_id"]})
    db[AUDIT_LOGS].insert_one({
        "event": "email_draft_saved",
        "application_id": application_id,
        "email_log_id": draft["_id"],
        "user_id": user["_id"],
        "created_at": datetime.now(timezone.utc),
    })
    return {key: value for key, value in draft.items() if key != "created_by"}


@router.get("/applications/{application_id}/email-drafts")
def list_application_email_drafts(
    application_id: str,
    db: MongoDatabase,
    user: CurrentUser,
) -> list[dict]:
    if db[APPLICATIONS].find_one({"_id": application_id}, {"_id": 1}) is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    return [
        {key: value for key, value in draft.items() if key != "created_by"}
        for draft in db["email_logs"].find({
            "application_id": application_id,
            "approval_status": {"$ne": "deleted"},
        }).sort("created_at", -1)
    ]


@router.get("/email-drafts/{draft_id}")
def get_email_draft(draft_id: str, db: MongoDatabase, user: CurrentUser) -> dict:
    draft = db["email_logs"].find_one({"_id": draft_id, "approval_status": {"$ne": "deleted"}})
    if draft is None:
        raise HTTPException(status_code=404, detail="Email draft not found.")
    return {key: value for key, value in draft.items() if key != "created_by"}


@router.get("/email-drafts/{draft_id}/status")
def get_email_status(draft_id: str, db: MongoDatabase, user: CurrentUser) -> dict:
    draft = db["email_logs"].find_one(
        {"_id": draft_id},
        {"approval_status": 1, "delivery_status": 1, "sent_at": 1, "error_message": 1},
    )
    if draft is None:
        raise HTTPException(status_code=404, detail="Email record not found.")
    return {
        "id": draft_id,
        "approval_status": draft.get("approval_status"),
        "delivery_status": draft.get("delivery_status"),
        "sent_at": draft.get("sent_at"),
        "error_message": draft.get("error_message"),
        "delivery_failure_type": draft.get("delivery_failure_type"),
    }


@router.patch("/email-drafts/{draft_id}")
def update_email_draft(
    draft_id: str,
    data: EmailDraftUpdate,
    db: MongoDatabase,
    user: CurrentUser,
) -> dict:
    draft = db["email_logs"].find_one_and_update(
        {
            "_id": draft_id,
            "approval_status": "draft",
            "delivery_status": "not_sent",
        },
        {"$set": {
            "subject": data.subject.strip(),
            "body": data.body.strip(),
            "updated_at": datetime.now(timezone.utc),
            "last_edited_by": user["_id"],
        }},
        return_document=ReturnDocument.AFTER,
    )
    if draft is None:
        existing = db["email_logs"].find_one({"_id": draft_id})
        if existing is None or existing.get("approval_status") == "deleted":
            raise HTTPException(status_code=404, detail="Email draft not found.")
        raise HTTPException(status_code=409, detail="Only an unsent draft can be edited.")
    db[AUDIT_LOGS].insert_one({
        "event": "email_draft_edited",
        "application_id": draft["application_id"],
        "email_log_id": draft_id,
        "user_id": user["_id"],
        "created_at": datetime.now(timezone.utc),
    })
    return {key: value for key, value in draft.items() if key != "created_by"}


@router.delete("/email-drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_email_draft(draft_id: str, db: MongoDatabase, user: CurrentUser) -> None:
    draft = db["email_logs"].find_one_and_update(
        {
            "_id": draft_id,
            "approval_status": "draft",
            "delivery_status": "not_sent",
        },
        {"$set": {
            "approval_status": "deleted",
            "deleted_by": user["_id"],
            "deleted_at": datetime.now(timezone.utc),
            "idempotency_key": f"deleted:{draft_id}",
            "updated_at": datetime.now(timezone.utc),
        }},
        return_document=ReturnDocument.AFTER,
    )
    if draft is None:
        existing = db["email_logs"].find_one({"_id": draft_id})
        if existing is None or existing.get("approval_status") == "deleted":
            raise HTTPException(status_code=404, detail="Email draft not found.")
        raise HTTPException(status_code=409, detail="Only an unsent draft can be deleted.")
    db[AUDIT_LOGS].insert_one({
        "event": "email_draft_deleted",
        "application_id": draft["application_id"],
        "email_log_id": draft_id,
        "user_id": user["_id"],
        "created_at": datetime.now(timezone.utc),
    })


@router.post("/email-drafts/{draft_id}/retry", status_code=status.HTTP_201_CREATED)
def prepare_safe_email_retry(draft_id: str, db: MongoDatabase, user: CurrentUser) -> dict:
    original = db["email_logs"].find_one({"_id": draft_id})
    if original is None:
        raise HTTPException(status_code=404, detail="Email record not found.")
    if (
        original.get("delivery_status") != "failed"
        or original.get("delivery_failure_type") != "explicit_rejection"
    ):
        raise HTTPException(
            status_code=409,
            detail="A retry is allowed only when the SMTP provider explicitly rejected delivery; ambiguous outcomes must not be retried.",
        )
    previous_retry = db["email_logs"].find_one({"retry_of": draft_id})
    if previous_retry:
        if previous_retry.get("delivery_status") in ("not_sent", "not_configured"):
            return {key: value for key, value in previous_retry.items() if key != "created_by"}
        raise HTTPException(status_code=409, detail="A retry has already been attempted for this email.")
    retry_id = new_id()
    retry = {
        key: value for key, value in original.items()
        if key not in {"_id", "sent_at", "error_message", "delivery_failure_type", "deleted_at", "deleted_by"}
    }
    retry.update({
        "_id": retry_id,
        "approval_status": "approved",
        "delivery_status": "not_sent",
        "idempotency_key": hashlib.sha256(
            f"{original['idempotency_key']}:retry:{retry_id}".encode()
        ).hexdigest(),
        "retry_of": draft_id,
        "created_by": user["_id"],
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "approved_by": original.get("approved_by", user["_id"]),
        "approved_at": original.get("approved_at", datetime.now(timezone.utc)),
    })
    try:
        db["email_logs"].insert_one(retry)
    except DuplicateKeyError:
        previous_retry = db["email_logs"].find_one({"retry_of": draft_id})
        if previous_retry and previous_retry.get("delivery_status") in ("not_sent", "not_configured"):
            return {key: value for key, value in previous_retry.items() if key != "created_by"}
        raise HTTPException(status_code=409, detail="A retry has already been attempted for this email.")
    db[AUDIT_LOGS].insert_one({
        "event": "email_retry_prepared",
        "application_id": original["application_id"],
        "email_log_id": retry_id,
        "retry_of": draft_id,
        "user_id": user["_id"],
        "created_at": datetime.now(timezone.utc),
    })
    return {key: value for key, value in retry.items() if key != "created_by"}


@router.post("/email-drafts/{draft_id}/approve-and-send")
def approve_and_send_email(draft_id: str, db: MongoDatabase, user: CurrentUser) -> dict:
    draft = db["email_logs"].find_one_and_update(
        {
            "_id": draft_id,
            "approval_status": {"$in": ["draft", "approved"]},
            "delivery_status": {"$in": ["not_sent", "not_configured"]},
        },
        {"$set": {
            "approval_status": "approved",
            "approved_by": user["_id"],
            "approved_at": datetime.now(timezone.utc),
            "delivery_status": "sending",
        }},
        return_document=ReturnDocument.AFTER,
    )
    if draft is None:
        current = db["email_logs"].find_one({"_id": draft_id})
        if current is None:
            raise HTTPException(status_code=404, detail="Email draft not found.")
        raise HTTPException(status_code=409, detail="This email is already approved or has a delivery attempt.")
    db[AUDIT_LOGS].insert_one({
        "event": "email_approved",
        "application_id": draft["application_id"],
        "email_log_id": draft_id,
        "user_id": user["_id"],
        "created_at": datetime.now(timezone.utc),
    })
    try:
        send_email(
            recipient=draft["recipient"],
            subject=draft["subject"],
            body=draft["body"],
            message_id=f"<{draft['idempotency_key']}@peopleflow.local>",
        )
    except EmailNotConfigured as exc:
        db["email_logs"].update_one(
            {"_id": draft_id},
            {"$set": {"delivery_status": "not_configured", "error_message": str(exc)}},
        )
        raise HTTPException(status_code=503, detail=str(exc))
    except EmailDeliveryRejected as exc:
        logger.warning("SMTP explicitly rejected approved email draft %s", draft_id)
        db["email_logs"].update_one(
            {"_id": draft_id},
            {"$set": {
                "delivery_status": "failed",
                "delivery_failure_type": "explicit_rejection",
                "error_message": str(exc),
            }},
        )
        db[AUDIT_LOGS].insert_one({
            "event": "email_delivery_rejected",
            "application_id": draft["application_id"],
            "email_log_id": draft_id,
            "user_id": user["_id"],
            "created_at": datetime.now(timezone.utc),
        })
        raise HTTPException(status_code=502, detail="The SMTP provider rejected this recipient.")
    except EmailDeliveryError as exc:
        logger.exception("SMTP delivery outcome is unknown for approved email draft %s", draft_id)
        db["email_logs"].update_one(
            {"_id": draft_id},
            {"$set": {
                "delivery_status": "unknown",
                "delivery_failure_type": "unknown",
                "error_message": str(exc),
            }},
        )
        db[AUDIT_LOGS].insert_one({
            "event": "email_delivery_outcome_unknown",
            "application_id": draft["application_id"],
            "email_log_id": draft_id,
            "user_id": user["_id"],
            "created_at": datetime.now(timezone.utc),
        })
        raise HTTPException(
            status_code=502,
            detail="The email provider did not confirm delivery. Check the email status before taking any retry action.",
        )
    sent_at = datetime.now(timezone.utc)
    db["email_logs"].update_one(
        {"_id": draft_id},
        {"$set": {
            "delivery_status": "sent",
            "sent_at": sent_at,
            "error_message": None,
            "delivery_failure_type": None,
        }},
    )
    db[AUDIT_LOGS].insert_one({
        "event": "email_sent",
        "application_id": draft["application_id"],
        "email_log_id": draft_id,
        "user_id": user["_id"],
        "created_at": datetime.now(timezone.utc),
    })
    return {"id": draft_id, "approval_status": "approved", "delivery_status": "sent", "sent_at": sent_at}


@router.patch("/applications/{application_id}", response_model=ApplicationRead)
def update_application(
    application_id: str,
    data: ApplicationReview,
    db: MongoDatabase,
    user: CurrentUser,
) -> ApplicationRead:
    application = db[APPLICATIONS].find_one({"_id": application_id})
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    candidate_data = data.model_dump(exclude={"stage"})
    candidate_data["name"] = candidate_data["name"].strip()
    candidate_data["email"] = candidate_data["email"].lower()
    candidate_data["phone"] = candidate_data["phone"].strip() if candidate_data["phone"] else None
    candidate_data["skills"] = [skill.strip() for skill in candidate_data["skills"] if skill.strip()]
    candidate_data["updated_at"] = datetime.now(timezone.utc)
    try:
        db[CANDIDATES].update_one({"_id": application["candidate_id"]}, {"$set": candidate_data})
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="A candidate with this email already exists.")
    previous_stage = application["stage"]
    db[APPLICATIONS].update_one(
        {"_id": application_id},
        {"$set": {
            "stage": data.stage,
            "hr_notes": data.hr_notes,
            "candidate_experience_years": data.experience_years,
            "candidate_education": data.education,
            "candidate_skills": [skill.casefold() for skill in data.skills],
            "updated_at": datetime.now(timezone.utc),
        }},
    )
    if previous_stage != data.stage or data.hr_notes != application.get("hr_notes", ""):
        db[AUDIT_LOGS].insert_one({
            "event": "application_review_updated",
            "application_id": application_id,
            "user_id": user["_id"],
            "previous_stage": previous_stage,
            "new_stage": data.stage,
            "created_at": datetime.now(timezone.utc),
        })
    application["stage"] = data.stage
    candidate = db[CANDIDATES].find_one({"_id": application["candidate_id"]})
    job = db[JOBS].find_one({"_id": application["job_id"]})
    return application_response(application, candidate, job)


@router.get("/applications/{application_id}/resume")
def download_resume(application_id: str, db: MongoDatabase, user: CurrentUser) -> FileResponse:
    application = db[APPLICATIONS].find_one({"_id": application_id}, {"resume_path": 1, "resume_filename": 1, "resume_content_type": 1})
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    path = Path(application["resume_path"]).resolve()
    root = settings.resume_storage_dir.resolve()
    if root not in path.parents or not path.is_file():
        logger.error("Resume file missing from private storage for application %s", application_id)
        raise HTTPException(status_code=404, detail="Resume file is no longer available.")
    return FileResponse(path, media_type=application["resume_content_type"], filename=application["resume_filename"])


@router.post("/google-forms/applications", response_model=ApplicationRead, status_code=status.HTTP_201_CREATED)
def google_form_intake(
    data: GoogleFormSubmission,
    db: MongoDatabase,
    background_tasks: BackgroundTasks,
    integration_key: str | None = Header(default=None, alias="X-Integration-Key"),
) -> ApplicationRead:
    configured_secret = settings.google_forms_integration_secret
    if not configured_secret:
        raise HTTPException(status_code=503, detail="Google Forms intake is not configured.")
    if not integration_key or not hmac.compare_digest(integration_key, configured_secret):
        raise HTTPException(status_code=401, detail="Invalid integration credentials.")
    if len(data.resume_base64) > ((settings.max_resume_size_bytes + 2) // 3) * 4:
        raise HTTPException(status_code=413, detail="Resume must be no larger than 5 MB.")
    try:
        content = base64.b64decode(data.resume_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail="Resume payload is not valid base64.")
    return create_application(
        db=db,
        background_tasks=background_tasks,
        name=data.name,
        email=data.email,
        phone=data.phone,
        job_title=data.job_title,
        filename=data.resume_filename,
        content_type=data.resume_content_type,
        content=content,
    )
