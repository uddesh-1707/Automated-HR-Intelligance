import logging
import time
from datetime import datetime, timezone
import hashlib
from email.utils import make_msgid
from pathlib import Path

from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.core.config import settings
from app.models import APPLICATIONS, AUDIT_LOGS, CANDIDATES, EMAIL_LOGS, JOBS, new_id
from app.services.email_notifications import EmailNotConfigured, send_email
from app.services.resume_parser import extract_text, parse_resume
from app.services.semantic_matching import (
    MatchingNotConfigured,
    OpenAIEmbeddingClient,
    PineconeVectorIndex,
    calculate_ats_result,
)

logger = logging.getLogger(__name__)
PARSER_VERSION = "deterministic-v2"


def send_application_confirmation(db: Database, application_id: str) -> None:
    if not settings.smtp_host or not settings.smtp_from_email:
        return
    application = db[APPLICATIONS].find_one({"_id": application_id})
    if application is None:
        return
    candidate = db[CANDIDATES].find_one({"_id": application["candidate_id"]})
    job = db[JOBS].find_one({"_id": application["job_id"]})
    if candidate is None or job is None:
        return
    key = hashlib.sha256(f"{application_id}:application_confirmation".encode()).hexdigest()
    log = {
        "_id": new_id(),
        "application_id": application_id,
        "candidate_id": candidate["_id"],
        "job_id": job["_id"],
        "email_type": "application_confirmation",
        "recipient": candidate["email"],
        "approval_status": "not_required",
        "delivery_status": "sending",
        "idempotency_key": key,
        "created_at": datetime.now(timezone.utc),
    }
    try:
        db[EMAIL_LOGS].insert_one(log)
    except DuplicateKeyError:
        return
    try:
        send_email(
            recipient=candidate["email"],
            subject=f"Application received: {job['title']}",
            body=(
                f"Hello {candidate['name']},\n\n"
                f"We received your application for {job['title']}. The hiring team will review it.\n\n"
                "Thank you,\nThe hiring team"
            ),
            message_id=make_msgid(idstring=key, domain="peopleflow.local"),
        )
    except EmailNotConfigured:
        db[EMAIL_LOGS].update_one({"_id": log["_id"]}, {"$set": {"delivery_status": "not_configured"}})
    except Exception as exc:
        logger.exception("Application confirmation email failed for application %s (%s)", application_id, type(exc).__name__)
        db[EMAIL_LOGS].update_one(
            {"_id": log["_id"]},
            {"$set": {"delivery_status": "failed", "error_message": "SMTP delivery failed."}},
        )
    else:
        db[EMAIL_LOGS].update_one(
            {"_id": log["_id"]},
            {"$set": {"delivery_status": "sent", "sent_at": datetime.now(timezone.utc)}},
        )


def _store_assessment(db: Database, application: dict, parsed: dict, text: str) -> None:
    """Explainable JD-based assessment, independent of Pinecone. Failures must not break application intake."""
    try:
        from app.services import shortlisting

        job = db[JOBS].find_one({"_id": application["job_id"]})
        if job is None:
            return
        embedder, llm = shortlisting.default_services()
        assessment = shortlisting.assess_candidate(job, parsed, text, embedder=embedder, llm=llm)
        db[APPLICATIONS].update_one(
            {"_id": application["_id"]},
            {"$set": {"assessment": assessment, "assessment_status": assessment["eligibility"]}},
        )
    except Exception:
        logger.exception("Shortlisting assessment failed for application %s", application["_id"])


def process_application(db: Database, application_id: str) -> None:
    application = db[APPLICATIONS].find_one({"_id": application_id})
    if application is None:
        logger.warning("Skipped processing for unknown application %s", application_id)
        return
    send_application_confirmation(db, application_id)
    now = datetime.now(timezone.utc)
    db[APPLICATIONS].update_one(
        {"_id": application_id},
        {"$set": {"processing_status": "processing", "parsing_status": "processing", "updated_at": now}},
    )
    try:
        content = Path(application["resume_path"]).read_bytes()
        text = extract_text(application["resume_filename"], content)
        parsed = parse_resume(text)
        candidate = db[CANDIDATES].find_one({"_id": application["candidate_id"]})
        if candidate is None:
            raise ValueError("Candidate record is missing.")
        db[CANDIDATES].update_one(
            {"_id": candidate["_id"]},
            {
                "$set": {
                    "phone": candidate.get("phone") or parsed["phone"],
                    "skills": parsed["skills"],
                    "experience_years": parsed["experience_years"],
                    "education": parsed["education"],
                    "experience": parsed["experience"],
                    "projects": parsed["projects"],
                    "certifications": parsed["certifications"],
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
        db[APPLICATIONS].update_one(
            {"_id": application_id},
            {
                "$set": {
                    "parsed_resume": parsed,
                    "parser_version": PARSER_VERSION,
                    "parsing_status": "completed",
                    "processing_status": "processing",
                    "embedding_status": "pending",
                    "matching_status": "pending",
                    "updated_at": datetime.now(timezone.utc),
                },
                "$unset": {"processing_error": ""},
            },
        )
    except (OSError, ValueError) as exc:
        logger.warning("Resume parsing failed for application %s: %s", application_id, exc)
        db[APPLICATIONS].update_one(
            {"_id": application_id},
            {
                "$set": {
                    "parsing_status": "failed",
                    "embedding_status": "pending",
                    "matching_status": "pending",
                    "processing_status": "failed",
                    "processing_error": "Resume parsing failed. HR can review the original file and retry.",
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
        return

    _store_assessment(db, application, parsed, text)
    if not settings.embedding_api_key or not settings.pinecone_api_key:
        missing = []
        if not settings.embedding_api_key:
            missing.append("EMBEDDING_API_KEY")
        if not settings.pinecone_api_key:
            missing.append("PINECONE_API_KEY")
        db[APPLICATIONS].update_one(
            {"_id": application_id},
            {
                "$set": {
                    "processing_status": "pending",
                    "embedding_status": "pending",
                    "matching_status": "pending",
                    "processing_error": f"Semantic matching is pending configuration: {', '.join(missing)}.",
                    "embedding_model": settings.embedding_model,
                    "embedding_model_version": settings.embedding_model_version,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
        return

    job = db[JOBS].find_one({"_id": application["job_id"]})
    if job is None:
        db[APPLICATIONS].update_one(
            {"_id": application_id},
            {
                "$set": {
                    "processing_status": "failed",
                    "matching_status": "failed",
                    "processing_error": "Job criteria are no longer available.",
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
        return

    db[APPLICATIONS].update_one(
        {"_id": application_id},
        {
            "$set": {
                "embedding_status": "processing",
                "matching_status": "processing",
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    last_error: Exception | None = None
    for attempt in range(settings.background_task_retries + 1):
        try:
            result = calculate_ats_result(
                job=job,
                    parsed={**parsed, "candidate_id": application["candidate_id"], "candidate_name": candidate["name"]},
                resume_text=text,
                embeddings=OpenAIEmbeddingClient(),
                vectors=PineconeVectorIndex(),
                application_id=application_id,
            )
            db[APPLICATIONS].update_one(
                {"_id": application_id},
                {
                    "$set": {
                        **result,
                        "processing_status": "completed",
                        "embedding_status": "completed",
                        "matching_status": "completed",
                        "processing_attempts": attempt + 1,
                        "processing_error": None,
                        "updated_at": datetime.now(timezone.utc),
                    }
                },
            )
            db[AUDIT_LOGS].insert_one({
                "event": "matching_completed",
                "application_id": application_id,
                "job_id": job["_id"],
                "scoring_version": result["scoring_version"],
                "created_at": datetime.now(timezone.utc),
            })
            return
        except MatchingNotConfigured as exc:
            db[APPLICATIONS].update_one(
                {"_id": application_id},
                {
                    "$set": {
                        "processing_status": "pending",
                        "embedding_status": "pending",
                        "matching_status": "pending",
                        "processing_error": str(exc),
                        "updated_at": datetime.now(timezone.utc),
                    }
                },
            )
            return
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Semantic processing attempt %s failed for application %s (%s)",
                attempt + 1,
                application_id,
                type(exc).__name__,
            )
            if attempt < settings.background_task_retries:
                time.sleep(min(0.25 * (2 ** attempt), 2))

    logger.error(
        "Semantic processing failed for application %s after retries (%s)",
        application_id,
        type(last_error).__name__ if last_error else "unknown",
    )
    db[APPLICATIONS].update_one(
        {"_id": application_id},
        {
            "$set": {
                "processing_status": "failed",
                "embedding_status": "failed",
                "matching_status": "failed",
                "processing_attempts": settings.background_task_retries + 1,
                "processing_error": "Semantic processing failed. HR can retry matching after checking provider configuration.",
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
