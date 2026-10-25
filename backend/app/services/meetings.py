"""Temporary interview meeting links through a configurable provider.

No URL is ever fabricated: if no provider is configured, or the provider call fails, the interview records
``meeting_status`` = ``not_configured`` / ``failed`` and emails say that a link could not be generated.
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx
from pymongo.database import Database

from app.core.config import settings
from app.models import AUDIT_LOGS, INTERVIEWS

logger = logging.getLogger(__name__)
PROVIDER_NAMES = ("none", "zoom", "webhook")


class MeetingNotConfigured(RuntimeError):
    pass


class MeetingProviderError(RuntimeError):
    pass


def validate_meeting_config() -> list[str]:
    provider = (settings.meeting_provider or "none").lower()
    if provider not in PROVIDER_NAMES:
        return [f"MEETING_PROVIDER '{provider}' is not supported. Use one of: {', '.join(PROVIDER_NAMES)}."]
    if provider == "zoom" and not (settings.zoom_account_id and settings.zoom_client_id and settings.zoom_client_secret):
        return ["MEETING_PROVIDER=zoom requires ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID and ZOOM_CLIENT_SECRET."]
    if provider == "webhook" and not (settings.meeting_webhook_url or "").startswith("https://"):
        return ["MEETING_PROVIDER=webhook requires MEETING_WEBHOOK_URL (an https:// endpoint)."]
    return []


def provider_name() -> str:
    return (settings.meeting_provider or "none").lower()


def _zoom_token() -> str:
    try:
        response = httpx.post(
            "https://zoom.us/oauth/token",
            params={"grant_type": "account_credentials", "account_id": settings.zoom_account_id},
            auth=(settings.zoom_client_id or "", settings.zoom_client_secret or ""),
            timeout=20,
        )
        response.raise_for_status()
        return response.json()["access_token"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise MeetingProviderError("Zoom rejected the configured credentials.") from exc


def create_meeting(*, topic: str, start: datetime, duration_minutes: int, timezone_name: str, interview_id: str) -> dict:
    provider = provider_name()
    problems = validate_meeting_config()
    if provider == "none" or problems:
        raise MeetingNotConfigured(problems[0] if problems else "No meeting provider is configured (set MEETING_PROVIDER).")
    start_utc = start.astimezone(timezone.utc)
    expires_at = start_utc + timedelta(minutes=duration_minutes, hours=settings.meeting_link_expiry_hours)
    try:
        if provider == "zoom":
            response = httpx.post(
                "https://api.zoom.us/v2/users/me/meetings",
                headers={"Authorization": f"Bearer {_zoom_token()}"},
                json={
                    "topic": topic[:200], "type": 2,
                    "start_time": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "duration": duration_minutes, "timezone": timezone_name,
                    "settings": {"waiting_room": True, "join_before_host": False, "approval_type": 2},
                },
                timeout=20,
            )
            response.raise_for_status()
            data = response.json()
            url, meeting_id = data["join_url"], str(data["id"])
        else:
            response = httpx.post(
                settings.meeting_webhook_url,
                headers={"X-Webhook-Secret": settings.meeting_webhook_secret or ""},
                json={
                    "action": "create", "interview_id": interview_id, "topic": topic,
                    "start_time": start_utc.isoformat(), "duration_minutes": duration_minutes,
                    "timezone": timezone_name, "expires_at": expires_at.isoformat(),
                },
                timeout=20,
            )
            response.raise_for_status()
            data = response.json()
            url, meeting_id = data["join_url"], str(data.get("meeting_id") or interview_id)
    except MeetingProviderError:
        raise
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise MeetingProviderError("The meeting provider could not create the meeting.") from exc
    if not str(url).startswith("https://"):
        raise MeetingProviderError("The meeting provider returned an unusable link.")
    return {"url": url, "provider": provider, "meeting_id": meeting_id, "expires_at": expires_at}


def delete_meeting(provider: str, meeting_id: str) -> None:
    try:
        if provider == "zoom":
            httpx.delete(
                f"https://api.zoom.us/v2/meetings/{meeting_id}",
                headers={"Authorization": f"Bearer {_zoom_token()}"}, timeout=20,
            ).raise_for_status()
        elif provider == "webhook" and settings.meeting_webhook_url:
            httpx.post(
                settings.meeting_webhook_url, headers={"X-Webhook-Secret": settings.meeting_webhook_secret or ""},
                json={"action": "delete", "meeting_id": meeting_id}, timeout=20,
            ).raise_for_status()
    except (httpx.HTTPError, MeetingProviderError) as exc:
        raise MeetingProviderError("The meeting provider could not delete the meeting.") from exc


def ensure_meeting(db: Database, interview: dict, candidate: dict, job: dict) -> dict:
    """Create the temporary meeting once per scheduled time; safe to call again. Updates and returns ``interview``."""
    now = datetime.now(timezone.utc)
    expires = interview.get("meeting_expires_at")
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if interview.get("meeting_status") == "created" and interview.get("meeting_url") and expires and expires > now:
        return interview
    update: dict
    try:
        meeting = create_meeting(
            topic=f"{interview.get('round_name') or 'Interview'}: {candidate['name']} — {job['title']}",
            start=interview["starts_at"],
            duration_minutes=interview.get("duration_minutes", 60),
            timezone_name=interview.get("timezone") or "UTC",
            interview_id=interview["_id"],
        )
        update = {
            "meeting_status": "created", "meeting_url": meeting["url"], "meeting_provider": meeting["provider"],
            "meeting_id": meeting["meeting_id"], "meeting_expires_at": meeting["expires_at"], "meeting_error": None,
        }
    except MeetingNotConfigured as exc:
        update = {"meeting_status": "not_configured", "meeting_url": None, "meeting_error": str(exc)}
    except MeetingProviderError as exc:
        logger.warning("Meeting creation failed for interview %s", interview["_id"])
        update = {"meeting_status": "failed", "meeting_url": None, "meeting_error": str(exc)}
    db[INTERVIEWS].update_one({"_id": interview["_id"]}, {"$set": update})
    interview.update(update)
    return interview


def invalidate_meeting(db: Database, interview: dict, reason: str) -> None:
    """Disable the interview's meeting link (cancellation, rescheduling or expiry)."""
    if interview.get("meeting_status") != "created":
        return
    status = "expired" if reason == "expired" else "invalidated"
    try:
        delete_meeting(interview.get("meeting_provider", ""), interview.get("meeting_id", ""))
    except MeetingProviderError:
        logger.warning("Provider could not delete meeting for interview %s; link is marked invalid in HRMS", interview["_id"])
        status = "invalidation_failed"
    db[INTERVIEWS].update_one(
        {"_id": interview["_id"]},
        {"$set": {"meeting_status": status, "meeting_url": None, "meeting_invalidated_reason": reason}},
    )
    db[AUDIT_LOGS].insert_one({
        "event": "interview_meeting_invalidated", "interview_id": interview["_id"], "reason": reason,
        "result": status, "created_at": datetime.now(timezone.utc),
    })


def expire_overdue_meetings(db: Database) -> int:
    now = datetime.now(timezone.utc)
    count = 0
    for interview in db[INTERVIEWS].find({"meeting_status": "created", "meeting_expires_at": {"$lt": now}}):
        invalidate_meeting(db, interview, "expired")
        count += 1
    return count
