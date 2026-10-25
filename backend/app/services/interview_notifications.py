"""Idempotent interview emails: candidate shortlisting/next-round message and interviewer briefing.

Every email is recorded in ``email_logs`` *before* it is sent, keyed by a deterministic idempotency key, so a
retry or a repeated confirmation never sends the same message twice. A message is reported as ``sent`` only
after the SMTP server accepted it. Scheduling itself is email-based: no calendar events or invitations are made.
"""

import hashlib
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from app.core.config import settings
from app.models import APPLICATIONS, AUDIT_LOGS, EMAIL_LOGS, EVALUATION_TOKENS, INTERVIEWS, JOBS, new_id
from app.services.email_notifications import (
    EmailDeliveryError,
    EmailDeliveryRejected,
    EmailNotConfigured,
    send_email,
)
from app.services.evaluation_links import evaluation_url, issue_token, resume_url
from app.services.meetings import ensure_meeting

logger = logging.getLogger(__name__)
RETRYABLE = ("not_sent", "not_configured")
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024


def _zone(interview: dict) -> ZoneInfo:
    try:
        return ZoneInfo(interview.get("timezone") or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _local_times(interview: dict) -> tuple[str, str]:
    start = interview["starts_at"]
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    local = start.astimezone(_zone(interview))
    end = local + timedelta(minutes=interview.get("duration_minutes", 60))
    return (
        local.strftime("%A, %d %B %Y"),
        f"{local.strftime('%I:%M %p')} \u2013 {end.strftime('%I:%M %p')} ({interview.get('timezone') or 'UTC'})",
    )


def previous_passed_round(db: Database, interview: dict, job: dict) -> str | None:
    """Name of the latest earlier round this candidate passed (recorded outcomes only)."""
    rounds = sorted(job.get("interview_rounds", []), key=lambda item: item["order"])
    current = next((item for item in rounds if item["round_id"] == interview.get("round_id")), None)
    if current is None:
        return None
    for earlier in reversed([item for item in rounds if item["order"] < current["order"]]):
        previous = db[INTERVIEWS].find_one({
            "application_id": interview["application_id"], "round_id": earlier["round_id"],
            "status": "completed", "outcome": "passed",
        })
        if previous:
            return earlier["name"]
    return None


def _meeting_lines(interview: dict) -> list[str]:
    if interview.get("meeting_status") == "created" and interview.get("meeting_url"):
        expires = interview.get("meeting_expires_at")
        lines = [f"  Meeting link (temporary): {interview['meeting_url']}"]
        if expires:
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            lines.append(
                f"  The link is only for this interview, is valid until {expires.astimezone(_zone(interview)).strftime('%d %B %Y, %I:%M %p')} "
                "and stops working if the interview is cancelled or rescheduled."
            )
        return lines
    lines = ["  Meeting link: a link could not be generated automatically; HR will share joining details separately."]
    if interview.get("location"):
        lines.append(f"  Location / venue: {interview['location']}")
    return lines


def contact_line() -> str:
    contact = settings.hr_contact_email or settings.smtp_from_email
    return f"Questions or need to reschedule? Contact the hiring team at {contact}." if contact else "Questions or need to reschedule? Reply to this email."


def candidate_message(candidate: dict, job: dict, interview: dict, previous_round: str | None = None) -> tuple[str, str]:
    round_name = interview.get("round_name") or "the next interview round"
    day, clock = _local_times(interview)
    if previous_round:
        headline = f"Congratulations! You passed {previous_round} and have been shortlisted for {round_name} for the {job['title']} position."
    else:
        headline = f"Congratulations! You have been shortlisted for {round_name} for the {job['title']} position."
    lines = [
        f"Hello {candidate['name']},", "", headline, "",
        "Interview details",
        f"  Round: {round_name}",
        f"  Date: {day}",
        f"  Time: {clock}",
        *_meeting_lines(interview),
    ]
    if interview.get("interviewers"):
        lines.append(f"  Interviewer(s): {', '.join(interview['interviewers'])}")
    lines += [
        "", "How to prepare",
        "  - Join a few minutes early with a stable connection and your resume to hand.",
        "  - Be ready to discuss the projects and experience listed in your application.",
        "", contact_line(), "", f"Regards,\n{job.get('department') or 'The hiring team'}",
    ]
    return f"Shortlisted: {round_name} \u2014 {job['title']}", "\n".join(lines)


def interviewer_message(candidate: dict, job: dict, interview: dict, token: str) -> tuple[str, str]:
    round_name = interview.get("round_name") or "Interview"
    day, clock = _local_times(interview)
    description = (job.get("description") or "No description recorded.").strip()
    if len(description) > 1500:
        description = description[:1500].rstrip() + "\u2026"
    lines = [
        "Hello,", "",
        f"You are assigned to conduct {round_name} for the {job['title']} role.", "",
        "Candidate",
        f"  Name: {candidate['name']}",
        f"  Email: {candidate['email']}",
        f"  Phone: {candidate.get('phone') or 'not provided'}",
        "", "Role", f"  {job['title']}", f"  {description}", "",
        "Interview",
        f"  Round: {round_name}",
        f"  Date: {day}",
        f"  Time: {clock}",
        *_meeting_lines(interview),
        "", "Resume",
        f"  Attached to this email when possible, and available securely at: {resume_url(token)}",
        "", "Record your evaluation",
        f"  {evaluation_url(token)}",
        "  This personal link works only for this candidate and round. After the interview, submit your feedback and choose",
        "  Passed, Failed or On Hold. Do not forward the link.",
        "", "Regards,\nHRMS Automated Intelligence",
    ]
    return f"Interview assignment: {candidate['name']} \u2014 {round_name}", "\n".join(lines)


def _key(interview: dict, recipient: str, kind: str) -> str:
    start = interview["starts_at"]
    start = start.astimezone(timezone.utc).isoformat() if start.tzinfo else start.isoformat()
    return hashlib.sha256(f"interview-email:{interview['_id']}:{start}:{recipient.casefold()}:{kind}".encode()).hexdigest()


def _attachments(log: dict) -> list[tuple[str, bytes, str]]:
    path = log.get("attachment_path")
    if not path:
        return []
    try:
        resolved = Path(path).resolve()
        if settings.resume_storage_dir.resolve() not in resolved.parents or resolved.stat().st_size > MAX_ATTACHMENT_BYTES:
            return []
        return [(log.get("attachment_name") or resolved.name, resolved.read_bytes(), log.get("attachment_type") or "application/octet-stream")]
    except OSError:
        return []


def _deliver(db: Database, log: dict) -> str:
    """Send one already-recorded email exactly once; returns its delivery_status."""
    claimed = db[EMAIL_LOGS].find_one_and_update(
        {"_id": log["_id"], "$or": [
            {"delivery_status": {"$in": list(RETRYABLE)}},
            {"delivery_status": "failed", "delivery_failure_type": "explicit_rejection"},
        ]},
        {"$set": {"delivery_status": "sending", "updated_at": datetime.now(timezone.utc)}},
        return_document=ReturnDocument.AFTER,
    )
    if claimed is None:
        return db[EMAIL_LOGS].find_one({"_id": log["_id"]}).get("delivery_status", "unknown")
    try:
        send_email(
            recipient=claimed["recipient"], subject=claimed["subject"], body=claimed["body"],
            message_id=f"<{claimed['idempotency_key']}@hrms.local>", attachments=_attachments(claimed),
        )
    except EmailNotConfigured as exc:
        result = {"delivery_status": "not_configured", "error_message": str(exc)}
    except EmailDeliveryRejected as exc:
        result = {"delivery_status": "failed", "delivery_failure_type": "explicit_rejection", "error_message": str(exc)}
    except EmailDeliveryError as exc:
        logger.warning("SMTP outcome unknown for interview email %s", claimed["_id"])
        result = {"delivery_status": "unknown", "delivery_failure_type": "unknown", "error_message": str(exc)}
    else:
        result = {"delivery_status": "sent", "sent_at": datetime.now(timezone.utc), "error_message": None}
    db[EMAIL_LOGS].update_one({"_id": claimed["_id"]}, {"$set": {**result, "updated_at": datetime.now(timezone.utc)}})
    return result["delivery_status"]


def _record_and_send(
    db: Database, *, interview, candidate, job, recipient, role, kind,
    build: Callable[[], tuple[str, str]], user_id, attachment: dict | None = None,
) -> dict:
    key = _key(interview, recipient, kind)
    log = db[EMAIL_LOGS].find_one({"idempotency_key": key})
    if log is not None and log.get("delivery_status") in RETRYABLE:
        # Not sent yet: refresh the content (e.g. a meeting link generated since) before retrying.
        if role == "interviewer":
            db[EVALUATION_TOKENS].update_many(
                {"interview_id": interview["_id"], "interviewer_email": recipient.casefold(), "revoked": False},
                {"$set": {"revoked": True, "revoked_reason": "superseded"}},
            )
        subject, body = build()
        db[EMAIL_LOGS].update_one({"_id": log["_id"]}, {"$set": {"subject": subject, "body": body}})
        log = db[EMAIL_LOGS].find_one({"_id": log["_id"]})
    elif log is None:
        subject, body = build()
        log = {
            "_id": new_id(), "application_id": interview["application_id"], "candidate_id": candidate["_id"],
            "job_id": job["_id"], "email_type": kind, "communication_context_id": interview["_id"],
            "recipient": recipient, "subject": subject, "body": body, "approval_status": "approved",
            "approved_by": user_id, "delivery_status": "not_sent", "provider_message_id": None,
            "idempotency_key": key, "created_by": user_id, "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc), "source": "interview_plan",
        }
        if attachment:
            log.update(attachment)
        try:
            db[EMAIL_LOGS].insert_one(log)
        except DuplicateKeyError:
            log = db[EMAIL_LOGS].find_one({"idempotency_key": key})
    status = _deliver(db, log)
    return {"recipient": recipient, "role": role, "status": status, "email_log_id": log["_id"]}


def notify_interview(
    db: Database,
    *,
    interview: dict,
    candidate: dict,
    job: dict,
    user_id: str | None,
    extra_interviewers: list[str] | None = None,
) -> list[dict]:
    """Email the candidate and each interviewer. Safe to call repeatedly."""
    interview = ensure_meeting(db, interview, candidate, job)
    application = db[APPLICATIONS].find_one({"_id": interview["application_id"]}) or {}
    previous_round = previous_passed_round(db, interview, job)
    attachment = None
    if application.get("resume_path"):
        attachment = {
            "attachment_path": application["resume_path"],
            "attachment_name": application.get("resume_filename"),
            "attachment_type": application.get("resume_content_type"),
        }
    results = [_record_and_send(
        db, interview=interview, candidate=candidate, job=job, recipient=candidate["email"], role="candidate",
        kind="shortlist_notification", user_id=user_id,
        build=lambda: candidate_message(candidate, job, interview, previous_round),
    )]
    recipients = [*(interview.get("interviewer_emails") or []), *(extra_interviewers or [])]
    for email in dict.fromkeys(item for item in recipients if item):
        results.append(_record_and_send(
            db, interview=interview, candidate=candidate, job=job, recipient=email, role="interviewer",
            kind="interviewer_notification", user_id=user_id, attachment=attachment,
            build=lambda email=email: interviewer_message(candidate, job, interview, issue_token(db, interview, email)),
        ))
    failed = [item for item in results if item["status"] != "sent"]
    db[AUDIT_LOGS].insert_one({
        "event": "interview_notifications_attempted", "application_id": interview["application_id"],
        "interview_id": interview["_id"], "sent": len(results) - len(failed), "not_sent": len(failed),
        "user_id": user_id, "created_at": datetime.now(timezone.utc),
    })
    return results


def summarize(results: list[dict]) -> str:
    """Overall status of one interview's notifications: sent | partial | not_configured | failed."""
    statuses = {item["status"] for item in results}
    if statuses == {"sent"}:
        return "sent"
    if "sent" in statuses:
        return "partial"
    if statuses == {"not_configured"}:
        return "not_configured"
    return "failed"
