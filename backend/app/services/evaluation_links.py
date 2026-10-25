"""Unguessable, expiring, single-interview evaluation tokens for interviewers.

Only a SHA-256 hash of each token is stored for lookup. The raw token appears solely in the email sent to
the interviewer. A token grants access to exactly one interview (one candidate, one round) and one interviewer.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from pymongo.database import Database

from app.core.config import settings
from app.models import EVALUATION_TOKENS, new_id


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def evaluation_url(token: str) -> str:
    return f"{settings.frontend_origin.rstrip('/')}/evaluate/{token}"


def resume_url(token: str) -> str:
    return f"{settings.api_public_url.rstrip('/')}/evaluations/{token}/resume"


def issue_token(db: Database, interview: dict, interviewer_email: str) -> str:
    token = secrets.token_urlsafe(32)
    interview_end = _aware(interview["starts_at"]) + timedelta(minutes=interview.get("duration_minutes", 60))
    now = datetime.now(timezone.utc)
    db[EVALUATION_TOKENS].insert_one({
        "_id": new_id(),
        "token_hash": _hash(token),
        "interview_id": interview["_id"],
        "application_id": interview["application_id"],
        "round_id": interview.get("round_id"),
        "interviewer_email": interviewer_email.casefold(),
        "expires_at": max(now, interview_end) + timedelta(days=settings.evaluation_link_expiry_days),
        "revoked": False,
        "created_at": now,
    })
    return token


def revoke_tokens(db: Database, interview_id: str, reason: str) -> int:
    result = db[EVALUATION_TOKENS].update_many(
        {"interview_id": interview_id, "revoked": False},
        {"$set": {"revoked": True, "revoked_reason": reason, "revoked_at": datetime.now(timezone.utc)}},
    )
    return result.modified_count


def lookup_token(db: Database, token: str) -> tuple[dict | None, str | None]:
    """Return (token document, None) when usable, else (document or None, 'unknown' | 'revoked' | 'expired')."""
    if not token or len(token) > 128:
        return None, "unknown"
    document = db[EVALUATION_TOKENS].find_one({"token_hash": _hash(token)})
    if document is None:
        return None, "unknown"
    if document.get("revoked"):
        return document, "revoked"
    if _aware(document["expires_at"]) <= datetime.now(timezone.utc):
        return document, "expired"
    return document, None
