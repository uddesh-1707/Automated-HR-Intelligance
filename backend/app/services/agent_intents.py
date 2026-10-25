"""Scope and intent guard for the HR assistant: HR tasks only, with conversation-aware follow-ups."""

import re

from pymongo.database import Database

from app.models import CANDIDATES, JOBS

SCOPE_REPLY = (
    "I can only help with HR tasks in this workspace: creating and finding job postings, reviewing applications and resumes, "
    "shortlisting candidates, planning and scheduling interviews, recording interview feedback and outcomes, updating "
    "candidate statuses, interviewer and candidate emails, and hiring summaries. What would you like to do?"
)
GREETING_REPLY = "Hello! " + SCOPE_REPLY

HR_TERMS = re.compile(
    r"\b(job|jobs|role|roles|position|positions|opening|vacancy|posting|hire|hiring|recruit\w*|candidate\w*|applicant\w*|"
    r"application\w*|resume\w*|cv|shortlist\w*|screen\w*|interview\w*|round|rounds|schedul\w*|reschedul\w*|slot\w*|"
    r"feedback|evaluat\w*|outcome\w*|passed|failed|on hold|stage|status|offer|reject\w*|email\w*|notif\w*|invit\w*|"
    r"interviewer\w*|ats|score\w*|rank\w*|pipeline|report|summary|summar\w*|dashboard|hr|technical|tech|jd|"
    r"job description|skills?|experience|intern\w*|eligible|eligib\w*)\b",
    re.IGNORECASE,
)
GREETING = re.compile(r"^\s*(hi|hello|hey|good (morning|afternoon|evening)|thanks|thank you|help|what can you do\??)\W*$", re.IGNORECASE)
FOLLOW_UP = re.compile(
    r"\b(yes|yeah|yep|no|nope|ok|okay|sure|go ahead|proceed|confirm|cancel|these|those|them|they|same|that one|"
    r"this one|the first|the second|the third|first|second|third|last|next|again|instead|also|more|rest|"
    r"round\s*\d+|tech\s*\d+|\d+)\b",
    re.IGNORECASE,
)


def classify_intent(db: Database, message: str, history: list[dict], context: dict | None = None) -> str:
    """Return 'greeting', 'hr' or 'off_topic'. Deterministic so unrelated requests never reach the model."""
    text = message.strip()
    if GREETING.match(text):
        return "greeting"
    if HR_TERMS.search(text):
        return "hr"
    if _mentions_hrms_entity(db, text):
        return "hr"
    in_conversation = bool(history) or bool(context)
    if in_conversation and len(text.split()) <= 8 and FOLLOW_UP.search(text):
        return "hr"
    return "off_topic"


def _mentions_hrms_entity(db: Database, text: str) -> bool:
    lowered = text.casefold()
    for job in db[JOBS].find({}, {"title": 1}):
        title = (job.get("title") or "").casefold()
        if title and title in lowered:
            return True
    words = set(re.findall(r"[a-z]{4,}", lowered))
    for candidate in db[CANDIDATES].find({}, {"name": 1}).limit(2000):
        parts = re.findall(r"[a-z]{4,}", (candidate.get("name") or "").casefold())
        if parts and (all(part in words for part in parts) or (len(parts) > 1 and parts[0] in words)):
            return True
    return False


CONTEXT_REFERENCE = re.compile(r"\b(these|those|them|same|selected|shortlisted|above|previous)\b.*\b(candidates?|applicants?|people)?\b|\bthem\b", re.IGNORECASE)


def refers_to_previous_candidates(message: str) -> bool:
    return bool(re.search(r"\b(these|those|them|the same|same candidates|selected candidates|above candidates)\b", message, re.IGNORECASE))
