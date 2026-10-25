import hashlib
import logging
import re
from difflib import SequenceMatcher
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, status
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.api.deps import CurrentUser
from app.core.config import settings
from app.core.database import MongoDatabase
from app.models import (
    APPLICATIONS,
    AUDIT_LOGS,
    CANDIDATES,
    INTERVIEWS,
    INTERVIEW_PLANS,
    JOBS,
    PROGRESSION_OVERRIDES,
    new_id,
)
from app.schemas import (
    InterviewRoundConfig,
    InterviewRoundsWrite,
    InterviewSchedulePlanRequest,
    ProgressionOverrideRequest,
)
from app.services.google_calendar import CalendarNotConfigured, CalendarUnavailable, GoogleCalendar
from app.services.semantic_resolver import semantic_scores
from app.services.interview_notifications import notify_interview, summarize as summarize_notifications
from app.services.pipeline import set_pipeline_status
from app.services.evaluation_links import revoke_tokens
from app.services.meetings import invalidate_meeting

router = APIRouter()
logger = logging.getLogger(__name__)
TERMINAL_STAGES = {"rejected", "hired"}
DAY_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
CALENDAR_SETUP_STEPS = [
    "In Google Calendar, open the HR host calendar > Settings and sharing > Share with specific people, and add the service-account email with the permission 'Make changes to events'.",
    "Do the same for every interviewer calendar listed in the round configuration (each interviewer shares their own calendar).",
    "Set GOOGLE_CALENDAR_HOST_ID in .env to that calendar's ID (Settings and sharing > Integrate calendar > Calendar ID; for a main Gmail calendar this is the Gmail address). Do not use 'primary'.",
    "Make sure the Google Calendar API is enabled in the Google Cloud project that owns the service account, then restart the backend.",
    "Sending invitations to candidates from a service account requires Google Workspace domain-wide delegation; on a personal Gmail account, events can be created but attendee invites are rejected by Google.",
]
ROUND_GENERIC_WORDS = {"interview", "round", "stage"}
GENERIC_ROLE_WORDS = {"developer", "engineer", "intern", "analyst", "manager", "specialist", "associate", "executive", "lead", "senior", "junior", "officer", "consultant", "trainee"}
TITLE_STOPWORDS = {"the", "a", "an", "for", "of", "and", "to", "in", "on", "at"}
TITLE_ALIASES = {
    "internship": "intern",
    "internships": "intern",
    "interns": "intern",
    "developer": "developer",
    "developers": "developer",
    "dev": "developer",
    "engineer": "engineer",
    "engineers": "engineer",
    "eng": "engineer",
    "frontend": "frontend",
    "backend": "backend",
    "fullstack": "fullstack",
}


def _normalized_words(value: str, *, round_alias: bool = False, job_title: bool = False) -> list[str]:
    words = re.findall(r"[a-z0-9]+", value.casefold())
    merged: list[str] = []
    index = 0
    while index < len(words):
        pair = (words[index], words[index + 1] if index + 1 < len(words) else "")
        if pair == ("front", "end"):
            merged.append("frontend")
            index += 2
            continue
        if pair == ("back", "end"):
            merged.append("backend")
            index += 2
            continue
        if pair == ("full", "stack"):
            merged.append("fullstack")
            index += 2
            continue
        token = TITLE_ALIASES.get(words[index], words[index]) if job_title else words[index]
        if not (job_title and token in TITLE_STOPWORDS):
            merged.append(token)
        index += 1
    if round_alias:
        merged = [word for word in merged if word not in ROUND_GENERIC_WORDS]
    return merged


def _window_score(title_words: list[str], prompt_words: list[str]) -> tuple[float, int]:
    if not title_words or not prompt_words:
        return 0.0, -1
    title_set = set(title_words)
    title_compact = "".join(title_words)
    title_sorted = "".join(sorted(title_words))
    best = (0.0, -1)
    for size in range(max(1, len(title_words) - 1), len(title_words) + 3):
        for start in range(max(0, len(prompt_words) - size + 1)):
            candidate_words = prompt_words[start:start + size]
            if not candidate_words:
                continue
            candidate_set = set(candidate_words)
            coverage = len(title_set & candidate_set) / len(title_set)
            jaccard = len(title_set & candidate_set) / len(title_set | candidate_set)
            sequence = SequenceMatcher(None, title_compact, "".join(candidate_words)).ratio()
            ordered = SequenceMatcher(None, title_sorted, "".join(sorted(candidate_words))).ratio()
            score = max(sequence, ordered, (coverage * 0.6) + (jaccard * 0.4))
            if title_set == candidate_set:
                score = max(score, 0.99)
            if score > best[0]:
                best = (score, start)
    return best


def _best_title_match(title: str, prompt: str, *, round_alias: bool = False) -> tuple[float, int]:
    return _window_score(
        _normalized_words(title, round_alias=round_alias, job_title=not round_alias),
        _normalized_words(prompt, round_alias=round_alias, job_title=not round_alias),
    )


def _clarification(question: str, matching_jobs: list[dict] | None = None) -> dict:
    payload = {"status": "clarification_required", "clarifying_question": question}
    if matching_jobs:
        payload["matching_jobs"] = [
            {"id": job["_id"], "title": job.get("title") or "Untitled"}
            for job in matching_jobs
        ]
    return payload


def _job_clarification(matches: list[dict], generic: str) -> dict:
    if len(matches) > 1:
        titles = "; ".join(job.get("title") or "Untitled" for job in matches)
        return _clarification(
            f"Multiple job postings match this request: {titles}. Which job should I use?",
            matches,
        )
    return _clarification(generic)


def _semantic_job_candidates(jobs: list[dict], prompt: str) -> tuple[dict | None, list[dict]] | None:
    """Embedding similarity between the instruction and job titles; None when embeddings are unavailable."""
    scores = semantic_scores(
        prompt,
        [f"{job.get('title', '')}. {job.get('department') or ''} {job.get('description') or ''}"[:600] for job in jobs],
    )
    if scores is None:
        return None
    ranked = sorted(zip(scores, jobs), key=lambda item: item[0], reverse=True)
    if not ranked:
        return None
    if ranked[0][0] >= SEMANTIC_JOB_MIN_SCORE and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= SEMANTIC_JOB_MARGIN):
        return ranked[0][1], [ranked[0][1]]
    return None, [job for score, job in ranked[:3] if score >= SEMANTIC_JOB_MIN_SCORE - 0.1]


def _resolve_job_candidates(db: MongoDatabase, prompt: str, job_id: str | None) -> tuple[dict | None, list[dict]]:
    if job_id:
        job = db[JOBS].find_one({"_id": job_id})
        return (job, [job] if job else [])
    jobs = [job for job in db[JOBS].find({"status": {"$ne": "archived"}})]
    if len(jobs) == 1:
        return jobs[0], jobs
    prompt_words = _normalized_words(prompt, job_title=True)
    prompt_compact = "".join(prompt_words)
    ranked: list[tuple[float, dict]] = []
    exact_matches: list[dict] = []
    for job in jobs:
        title_words = _normalized_words(job.get("title", ""), job_title=True)
        if not title_words:
            continue
        score, _ = _window_score(title_words, prompt_words)
        distinctive = [word for word in title_words if word not in GENERIC_ROLE_WORDS and not word.isdigit()]
        if distinctive and not any(_token_match(word, prompt_words) >= 0.82 for word in distinctive):
            score = min(score, 0.5)  # only a generic word (e.g. 'developer') matched: not enough to pick a job
        title_compact = "".join(title_words)
        if title_compact and title_compact in prompt_compact:
            score = max(score, 0.99)
            exact_matches.append(job)
        ranked.append((score, job))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if len(exact_matches) == 1:
        return exact_matches[0], exact_matches
    if len(exact_matches) > 1:
        return None, exact_matches
    strong = [(score, job) for score, job in ranked if score >= 0.78]
    if not strong:
        semantic = _semantic_job_candidates(jobs, prompt)
        if semantic is not None:
            return semantic
        close = [job for score, job in ranked if score >= 0.62]
        if close:
            return None, close
        shared = set(prompt_words) - ROUND_GENERIC_WORDS - {"schedule", "reschedule", "book", "role", "job", "position", "candidates", "candidate", "technical", "tech"}
        partial = [job for job in jobs if shared & set(_normalized_words(job.get("title", ""), job_title=True))]
        return None, partial if len(partial) > 1 else []
    top_score = strong[0][0]
    contenders = [job for score, job in strong if top_score - score < 0.08]
    if len(contenders) == 1 and (len(strong) == 1 or top_score - strong[1][0] >= 0.08):
        return contenders[0], contenders
    return None, contenders


def _resolve_job(db: MongoDatabase, prompt: str, job_id: str | None) -> dict | None:
    job, _matches = _resolve_job_candidates(db, prompt, job_id)
    return job


_SPOKEN = "one|two|three|four|five|six|seven|eight|nine|ten"
_COUNT_ADJECTIVES = "shortlisted|eligible|selected|qualified|passed|available|remaining"
ORDINAL_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6}
SEMANTIC_ROUND_MIN_SCORE = 0.40
SEMANTIC_ROUND_MARGIN = 0.04
SEMANTIC_JOB_MIN_SCORE = 0.45
SEMANTIC_JOB_MARGIN = 0.06


def _prompt_round_numbers(prompt: str) -> set[int]:
    """Numbers that refer to a round (e.g. 'tech 2', '2nd round'), not counts like '5 candidates'."""
    text = re.sub(
        rf"\b(?:(?:top|first|any)\s+)?(?:\d{{1,3}}|{_SPOKEN})\s+(?:{_COUNT_ADJECTIVES}\s+)?(?:candidates?|applicants?|people|persons|students?|days?)\b",
        " ",
        prompt.casefold(),
    )
    numbers = {int(item) for item in re.findall(r"\b\d{1,2}\b", text)}
    numbers.update(int(item) for item in re.findall(r"\b(\d{1,2})(?:st|nd|rd|th)\b", text))
    numbers.update(value for word, value in ORDINAL_WORDS.items() if re.search(rf"\b{word}\b", text))
    for match in re.finditer(rf"\b(?:round|interview|stage|level|technical|tech)\s+({_SPOKEN})\b", text):
        numbers.add(DAY_WORDS[match.group(1)])
    return numbers


def _token_match(name_token: str, prompt_tokens: list[str]) -> float:
    best = 0.0
    for token in prompt_tokens:
        if token == name_token:
            return 1.0
        if len(token) >= 3 and name_token.startswith(token):
            best = max(best, 0.95)  # abbreviations such as "tech" for "technical"
        elif len(token) >= 4 and len(name_token) >= 4:
            ratio = SequenceMatcher(None, name_token, token).ratio()
            if ratio >= 0.82:
                best = max(best, ratio)
    return best


def _round_name_parts(name: str) -> tuple[list[str], set[int]]:
    words = re.findall(r"[a-z0-9]+", name.casefold())
    numbers = {int(word) for word in words if word.isdigit()}
    tokens = [
        word for word in words
        if not word.isdigit() and word not in ROUND_GENERIC_WORDS and word not in TITLE_STOPWORDS
    ]
    return tokens, numbers


def _lexical_round_scores(rounds: list[InterviewRoundConfig], prompt: str) -> list[float]:
    prompt_tokens = re.findall(r"[a-z]+", prompt.casefold())
    prompt_numbers = _prompt_round_numbers(prompt)
    mentions_round_word = any(word in prompt_tokens for word in ROUND_GENERIC_WORDS)
    scores = []
    numbered_hits = []
    for index, round_item in enumerate(rounds):
        tokens, numbers = _round_name_parts(round_item.name)
        if numbers and prompt_numbers and not numbers <= prompt_numbers:
            scores.append(0.0)
            continue
        number_hit = bool(numbers and prompt_numbers)
        if not tokens:
            scores.append(0.85 if number_hit and mentions_round_word else 0.0)
            continue
        matches = [_token_match(token, prompt_tokens) for token in tokens]
        coverage = sum(matches) / len(matches)
        any_hit = any(item >= 0.82 for item in matches)
        if number_hit:
            scores.append(0.8 + 0.2 * coverage if any_hit else 0.0)
        else:
            scores.append(coverage)
            if numbers and any_hit:
                numbered_hits.append(index)
    if len(numbered_hits) > 1:
        # e.g. "technical interview" with rounds 1 and 2: the round number is the only discriminator.
        for index in numbered_hits:
            scores[index] = 0.0
    return scores


def _confident(scored: list[tuple[float, InterviewRoundConfig]], minimum: float, margin: float) -> InterviewRoundConfig | None:
    ranked = sorted(scored, key=lambda item: item[0], reverse=True)
    if ranked and ranked[0][0] >= minimum and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= margin):
        return ranked[0][1]
    return None


def _resolve_round(job: dict, prompt: str) -> InterviewRoundConfig | None:
    rounds = [InterviewRoundConfig.model_validate(item) for item in job.get("interview_rounds", [])]
    if not rounds:
        return None
    lowered = prompt.casefold()
    positions = [
        (lowered.find(round_item.name.casefold()), round_item)
        for round_item in rounds
        if round_item.name.casefold() in lowered
    ]
    if len(positions) == 1:
        return positions[0][1]
    action = re.search(r"\b(?:schedule|reschedule|book)\b", lowered)
    if action:
        after_action = [(position, item) for position, item in positions if position >= action.end()]
        if after_action:
            after_action.sort(key=lambda pair: pair[0])
            return after_action[0][1]
    if len(rounds) == 1 and re.search(r"\b(?:round|interview|stage)\b", lowered):
        return rounds[0]

    lexical = _confident(list(zip(_lexical_round_scores(rounds, prompt), rounds)), 0.78, 0.08)
    if lexical is not None:
        return lexical

    ordered = sorted(rounds, key=lambda item: item.order)
    positional = re.search(r"\b(first|final|last)\s+(?:interview\s+)?(?:round|stage|interview)\b", lowered)
    if positional:
        return ordered[0] if positional.group(1) == "first" else ordered[-1]

    return _semantic_round(rounds, prompt)


def _semantic_round(rounds: list[InterviewRoundConfig], prompt: str) -> InterviewRoundConfig | None:
    """Embedding similarity between the instruction and each configured round."""
    scores = semantic_scores(
        prompt,
        [f"{item.name}. {item.interview_type} interview, round {item.order}" for item in rounds],
    )
    if scores is None:
        return None
    prompt_numbers = _prompt_round_numbers(prompt)
    scored = []
    for score, round_item in zip(scores, rounds):
        _tokens, numbers = _round_name_parts(round_item.name)
        scored.append((0.0 if numbers and prompt_numbers and not numbers <= prompt_numbers else score, round_item))
    return _confident(scored, SEMANTIC_ROUND_MIN_SCORE, SEMANTIC_ROUND_MARGIN)


REQUEST_COUNT_RE = re.compile(
    rf"\b(?:top\s+|first\s+)?(\d{{1,3}}|{_SPOKEN})\s+(?:(?:{_COUNT_ADJECTIVES})\s+)?(?:candidates?|applicants?|people|persons|students?)\b",
    re.IGNORECASE,
)
NAME_STOPWORDS = {
    "schedule", "reschedule", "book", "please", "interview", "interviews", "round", "stage", "candidate",
    "candidates", "applicant", "applicants", "everyone", "all", "who", "passed", "failed", "within", "next",
    "days", "for", "the", "and", "with", "from", "that", "top", "first", "technical", "tech", "hr", "final",
    "shortlisted", "call", "set", "arrange", "them", "this", "these", "those", "their", "his", "her",
}


def _requested_count(prompt: str) -> int | None:
    match = REQUEST_COUNT_RE.search(prompt)
    if not match:
        return None
    raw = match.group(1).casefold()
    count = int(raw) if raw.isdigit() else DAY_WORDS[raw]
    return count if count > 0 else None


def _named_application_ids(
    prompt: str,
    pairs: list[tuple[dict, dict]],
    reserved_words: set[str],
) -> set[str]:
    """Applications whose candidate is named (full name, first/last name, or email) in the instruction."""
    lowered = prompt.casefold()
    prompt_words = set(re.findall(r"[a-z0-9]+", lowered))
    blocked = NAME_STOPWORDS | reserved_words
    matched: set[str] = set()
    for application, candidate in pairs:
        email = str(candidate.get("email") or "").casefold()
        if email and email in lowered:
            matched.add(application["_id"])
            continue
        words = re.findall(r"[a-z0-9]+", str(candidate.get("name") or "").casefold())
        if not words:
            continue
        if len(words) > 1 and all(word in prompt_words for word in words):
            matched.add(application["_id"])
            continue
        distinctive = [word for word in (words[0], words[-1]) if len(word) >= 4 and word not in blocked]
        if any(word in prompt_words for word in distinctive):
            matched.add(application["_id"])
    return matched


def _round_passed_explicitly(job: dict, prompt: str) -> list[str]:
    passed_ids = []
    for raw in job.get("interview_rounds", []):
        name = re.escape(raw["name"])
        if re.search(rf"\bpassed?\s+(?:the\s+)?{name}\b|\b{name}\s+(?:was\s+)?passed\b", prompt, re.IGNORECASE):
            passed_ids.append(raw["round_id"])
    return passed_ids


def _window(request: InterviewSchedulePlanRequest, timezone_name: str = "UTC") -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    if request.window_start_date or request.window_end_date:
        if not (request.window_start_date and request.window_end_date):
            raise HTTPException(status_code=422, detail="Choose both a first and last interview date.")
        try:
            zone = ZoneInfo(timezone_name)
        except Exception:
            zone = ZoneInfo("UTC")
        if request.window_end_date < request.window_start_date:
            raise HTTPException(status_code=422, detail="The last interview date must not be before the first.")
        start = datetime.combine(request.window_start_date, time(0), tzinfo=zone).astimezone(timezone.utc)
        end = datetime.combine(request.window_end_date + timedelta(days=1), time(0), tzinfo=zone).astimezone(timezone.utc)
        if end <= now:
            raise HTTPException(status_code=422, detail="The selected dates are in the past. Choose today or a later date.")
        # A start date of today means "from now"; earlier days are rejected below.
        if start < now <= start + timedelta(days=1):
            start = now
    elif request.window_start and request.window_end:
        start = request.window_start.astimezone(timezone.utc)
        end = request.window_end.astimezone(timezone.utc)
    elif request.window_start or request.window_end:
        raise HTTPException(status_code=422, detail="Provide both scheduling window start and end.")
    else:
        prompt_match = re.search(
            r"\b(?:next|within|over)\s+(?:the\s+)?(?:next\s+)?(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s+days?\b",
            request.instruction,
            re.IGNORECASE,
        )
        day_count = 7
        if prompt_match:
            raw_days = prompt_match.group(1).casefold()
            day_count = int(raw_days) if raw_days.isdigit() else DAY_WORDS[raw_days]
        if day_count > 30:
            raise HTTPException(status_code=422, detail="Scheduling windows from instructions are limited to 30 days.")
        start, end = now, now + timedelta(days=day_count)
    if end - start > timedelta(days=30):
        raise HTTPException(status_code=422, detail="Scheduling windows are limited to 30 days.")
    if start < now - timedelta(minutes=1):
        raise HTTPException(status_code=422, detail="The scheduling window must not start in the past.")
    return start, end


def _latest_round_interview(db: MongoDatabase, application_id: str, round_id: str) -> dict | None:
    return db[INTERVIEWS].find_one(
        {"application_id": application_id, "round_id": round_id},
        sort=[("starts_at", -1)],
    )


def _eligibility(
    db: MongoDatabase,
    application: dict,
    target_round: InterviewRoundConfig,
    explicit_passes: list[str],
    *,
    allow_reschedule: bool = False,
) -> tuple[str, str | None]:
    if application.get("stage") in TERMINAL_STAGES:
        return "ineligible", f"Application is already {application['stage']}."
    existing = _latest_round_interview(db, application["_id"], target_round.round_id)
    if existing and existing.get("status") == "completed":
        return "ineligible", "This round is already completed."
    if existing and existing.get("status") == "scheduled" and not allow_reschedule:
        return "already_scheduled", "This round already has a scheduled interview."
    progression_override = db[PROGRESSION_OVERRIDES].find_one({
        "application_id": application["_id"],
        "round_id": target_round.round_id,
        "active": True,
    })
    if progression_override:
        return "eligible", None
    job_doc = db[JOBS].find_one({"_id": application["job_id"]}) or {}
    job_rounds = sorted(job_doc.get("interview_rounds", []), key=lambda item: item["order"])
    # Only recorded Passed outcomes unlock a later round; wording in an HR instruction never counts as a pass.
    # With no explicit prerequisites, the immediately preceding configured round is required.
    prerequisites = list(dict.fromkeys(target_round.prerequisite_round_ids))
    if not prerequisites:
        earlier = [item for item in job_rounds if item["order"] < target_round.order]
        if earlier:
            prerequisites = [earlier[-1]["round_id"]]
        elif job_doc.get("shortlist_run_at") and application.get("shortlist_status") != "shortlisted":
            return "ineligible", "Not in this job's resume shortlist. Review the shortlisting result first."
    for prerequisite_id in prerequisites:
        prerequisite = next((item for item in job_rounds if item["round_id"] == prerequisite_id), None)
        if prerequisite is None:
            return "ineligible", "A required interview round is no longer configured."
        previous = _latest_round_interview(db, application["_id"], prerequisite_id)
        if previous is None:
            return "ineligible", f"Has not completed {prerequisite['name']}."
        if previous.get("status") != "completed":
            return "ineligible", f"{prerequisite['name']} is not completed."
        outcome = previous.get("outcome", "pending_evaluation")
        if outcome == "pending_evaluation":
            return "awaiting_evaluation", f"Awaiting evaluation for {prerequisite['name']}."
        if outcome != "passed":
            return "ineligible", f"Outcome for {prerequisite['name']} is {outcome.replace('_', ' ')}."
    return "eligible", None


def _busy_events_conflict(
    events: list[dict],
    start: datetime,
    end: datetime,
    ignore_event_id: str | None = None,
) -> bool:
    for event in events:
        if ignore_event_id and event.get("id") == ignore_event_id:
            continue
        if event.get("status") == "cancelled":
            continue
        start_value = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
        end_value = event.get("end", {}).get("dateTime") or event.get("end", {}).get("date")
        if not start_value or not end_value:
            continue
        busy_start = datetime.fromisoformat(start_value.replace("Z", "+00:00"))
        busy_end = datetime.fromisoformat(end_value.replace("Z", "+00:00"))
        if busy_start.tzinfo is None:
            busy_start = busy_start.replace(tzinfo=timezone.utc)
        if busy_end.tzinfo is None:
            busy_end = busy_end.replace(tzinfo=timezone.utc)
        if start < busy_end and end > busy_start:
            return True
    return False


def _local_conflict(
    db: MongoDatabase,
    emails: list[str],
    start: datetime,
    end: datetime,
    *,
    ignore_interview_id: str | None = None,
) -> bool:
    query = {
        "status": "scheduled",
        "starts_at": {"$lt": end},
        "interviewer_emails": {"$in": emails},
    }
    if ignore_interview_id:
        query["_id"] = {"$ne": ignore_interview_id}
    for interview in db[INTERVIEWS].find(query, {"starts_at": 1, "duration_minutes": 1}):
        interview_start = interview["starts_at"]
        if interview_start.tzinfo is None:
            interview_start = interview_start.replace(tzinfo=timezone.utc)
        interview_end = interview_start + timedelta(minutes=interview.get("duration_minutes", 60))
        if start < interview_end and end > interview_start:
            return True
    return False


def _optional_calendar() -> tuple[GoogleCalendar | None, str | None]:
    """Read-only Google Calendar availability source. Not configured is fine: HRMS bookings are always checked."""
    try:
        return GoogleCalendar(), None
    except CalendarNotConfigured:
        return None, None


def _round_calendar_ids(calendar: GoogleCalendar | None, round_config: InterviewRoundConfig) -> list[str]:
    if calendar is None:
        return []
    calendar_ids = [*round_config.required_interviewers, calendar.host_calendar_id]
    if round_config.requires_hr and settings.google_calendar_hr_id not in (None, "", "primary"):
        calendar_ids.append(settings.google_calendar_hr_id)
    return list(dict.fromkeys(calendar_ids))


def _hr_attendee(round_config: InterviewRoundConfig) -> list[str]:
    hr = settings.google_calendar_hr_id
    return [hr] if round_config.requires_hr and hr and hr != "primary" and "@" in hr else []


def _notification_summary(items: list[dict]) -> dict:
    scheduled = [item for item in items if item.get("status") == "scheduled"]
    statuses = [item.get("email_status") for item in scheduled]
    return {
        "sent": statuses.count("sent"),
        "partial": statuses.count("partial"),
        "not_configured": statuses.count("not_configured"),
        "failed": statuses.count("failed"),
    }


def _infer_round(
    db: MongoDatabase,
    applications: list[dict],
    rounds: list[InterviewRoundConfig],
    prompt: str,
) -> InterviewRoundConfig | None:
    """Pick the earliest configured round that the in-scope candidates are eligible for."""
    ordered = sorted(rounds, key=lambda item: item.order)
    if len(ordered) == 1:
        return ordered[0]
    lowered = prompt.casefold()
    scoped = applications
    if "shortlisted" in lowered:
        scoped = [item for item in applications if item.get("stage") in ("screening", "interview")]
    next_rounds: set[str] = set()
    for application in scoped:
        for configured in ordered:
            eligibility, _ = _eligibility(db, application, configured, [])
            if eligibility == "eligible":
                next_rounds.add(configured.round_id)
                break
    if not next_rounds:
        return None
    if len(next_rounds) > 1 and "next round" in lowered:
        return None
    return next(item for item in ordered if item.round_id in next_rounds)


def _find_slot(
    db: MongoDatabase,
    calendar: GoogleCalendar,
    round_config: InterviewRoundConfig,
    timezone_name: str,
    window_start: datetime,
    window_end: datetime,
    reservations: list[tuple[datetime, datetime]],
    ignore_event_id: str | None = None,
    ignore_interview_id: str | None = None,
    ignore_starts_at: datetime | None = None,
    ignore_duration_minutes: int | None = None,
    calendar_events_cache: dict[str, list[dict]] | None = None,
) -> tuple[datetime | None, str | None]:
    zone = ZoneInfo(timezone_name)
    calendar_ids = _round_calendar_ids(calendar, round_config)
    busy_by_calendar = calendar_events_cache if calendar_events_cache is not None else {}
    if calendar is not None and not busy_by_calendar:
        try:
            busy_by_calendar.update({
                calendar_id: calendar.list_events(calendar_id, window_start, window_end)
                for calendar_id in dict.fromkeys(calendar_ids)
            })
        except CalendarUnavailable as exc:
            return None, str(exc)

    local_start = window_start.astimezone(zone).date()
    local_end = window_end.astimezone(zone).date()
    day = local_start
    while day <= local_end:
        slot_time = datetime.combine(day, time(round_config.working_day_start_hour), tzinfo=zone)
        day_end = datetime.combine(day, time(round_config.working_day_end_hour), tzinfo=zone)
        while slot_time + timedelta(minutes=round_config.duration_minutes) <= day_end:
            slot_end = slot_time + timedelta(minutes=round_config.duration_minutes)
            start_utc, end_utc = slot_time.astimezone(timezone.utc), slot_end.astimezone(timezone.utc)
            if ignore_starts_at is not None and ignore_starts_at.tzinfo is None:
                ignore_starts_at = ignore_starts_at.replace(tzinfo=timezone.utc)
            if (
                ignore_starts_at is not None
                and ignore_duration_minutes is not None
                and start_utc == ignore_starts_at.astimezone(timezone.utc)
                and round_config.duration_minutes == ignore_duration_minutes
            ):
                slot_time += timedelta(minutes=round_config.slot_interval_minutes)
                continue
            if start_utc >= window_start and end_utc <= window_end:
                reserved_overlap = any(start_utc < reserved_end and end_utc > reserved_start for reserved_start, reserved_end in reservations)
                if not reserved_overlap and not _local_conflict(
                    db,
                    list(round_config.required_interviewers),
                    start_utc,
                    end_utc,
                    ignore_interview_id=ignore_interview_id,
                ):
                    if not any(
                        _busy_events_conflict(busy_by_calendar[calendar_id], start_utc, end_utc, ignore_event_id)
                        for calendar_id in busy_by_calendar
                    ):
                        return start_utc, None
            slot_time += timedelta(minutes=round_config.slot_interval_minutes)
        day += timedelta(days=1)
    return None, "No slot is available within the requested window and configured working hours."


def _create_plan(db: MongoDatabase, request: InterviewSchedulePlanRequest, user: dict) -> dict:
    prompt = request.instruction.strip()
    job, matches = _resolve_job_candidates(db, prompt, request.job_id)
    if job is None:
        return _job_clarification(matches, "Which job is this scheduling request for?")
    rounds = [InterviewRoundConfig.model_validate(item) for item in job.get("interview_rounds", [])]
    if not rounds:
        return {
            **_clarification(f"No interview rounds are configured for {job['title']}. Configure rounds before scheduling."),
            "job_id": job["_id"],
        }
    round_config = None
    if request.round_id:
        round_config = next((item for item in rounds if item.round_id == request.round_id), None)
        if round_config is None:
            raise HTTPException(status_code=404, detail="The selected interview round is not configured for this job.")
    if round_config is None:
        round_config = _resolve_round(job, prompt)

    applications = list(db[APPLICATIONS].find({"job_id": job["_id"]}).sort("applied_at", 1))
    candidates_by_id = {
        item["_id"]: item
        for item in db[CANDIDATES].find({"_id": {"$in": [app["candidate_id"] for app in applications]}})
    }
    reserved_words = set(_normalized_words(job.get("title", "")))
    for round_item in rounds:
        reserved_words.update(_normalized_words(round_item.name))
    if request.application_ids:
        applications = [app for app in applications if app["_id"] in set(request.application_ids)]
    named_ids = _named_application_ids(
        prompt,
        [(app, candidates_by_id[app["candidate_id"]]) for app in applications if app["candidate_id"] in candidates_by_id],
        reserved_words,
    )
    if named_ids:
        applications = [app for app in applications if app["_id"] in named_ids]

    round_inferred = False
    if round_config is None:
        round_config = _infer_round(db, applications, rounds, prompt)
        round_inferred = round_config is not None
    if round_config is None:
        names = ", ".join(item.name for item in sorted(rounds, key=lambda item: item.order))
        return _clarification(f"Which configured interview round should be scheduled for {job['title']}? Configured rounds: {names}.")

    window_start, window_end = _window(request, job.get("timezone", "UTC"))
    explicit_passes: list[str] = []  # instruction wording never substitutes for a recorded outcome
    is_rescheduling = "reschedule" in prompt.casefold()
    limit = _requested_count(prompt)
    if limit and not named_ids:
        applications.sort(key=lambda app: (-(app.get("ats_score") or 0), str(app.get("applied_at") or "")))
    calendar, calendar_error = _optional_calendar()

    items = []
    eligible_count = 0
    awaiting_evaluation_count = 0
    awaiting_confirmation_count = 0
    unable_to_schedule_count = 0
    reservations: list[tuple[datetime, datetime]] = []
    calendar_events_cache: dict[str, list[dict]] = {}
    selected_count = 0
    for application in applications:
        candidate = candidates_by_id.get(application["candidate_id"])
        if candidate is None:
            continue
        existing_interview = _latest_round_interview(db, application["_id"], round_config.round_id)
        eligibility, reason = _eligibility(
            db,
            application,
            round_config,
            explicit_passes,
            allow_reschedule=is_rescheduling,
        )
        if limit and eligibility == "eligible":
            if selected_count >= limit:
                continue
            selected_count += 1
        progression_override = db[PROGRESSION_OVERRIDES].find_one({
            "application_id": application["_id"],
            "round_id": round_config.round_id,
            "active": True,
        })
        item = {
            "application_id": application["_id"],
            "candidate_id": candidate["_id"],
            "candidate_name": candidate["name"],
            "candidate_email": candidate["email"],
            "eligibility": eligibility,
            "status": eligibility,
            "reason": reason,
            "starts_at": None,
            "ends_at": None,
            "reschedule_interview_id": (
                existing_interview["_id"]
                if is_rescheduling and existing_interview and existing_interview.get("status") == "scheduled"
                else None
            ),
            "progression_override_id": progression_override.get("_id") if progression_override else None,
            "progression_override_reason": progression_override.get("reason") if progression_override else None,
        }
        if eligibility == "eligible":
            eligible_count += 1
            if calendar is not None and not calendar_events_cache and not calendar_error:
                try:
                    calendar_events_cache.update({
                        calendar_id: calendar.list_events(calendar_id, window_start, window_end)
                        for calendar_id in _round_calendar_ids(calendar, round_config)
                    })
                except CalendarUnavailable as exc:
                    calendar_error = str(exc)
            if calendar_error:
                item.update({"status": "unable_to_schedule", "reason": calendar_error})
                unable_to_schedule_count += 1
            else:
                starts_at, slot_error = _find_slot(
                    db,
                    calendar,
                    round_config,
                    job.get("timezone", "UTC"),
                    window_start,
                    window_end,
                    reservations,

                    ignore_interview_id=item["reschedule_interview_id"],
                    ignore_starts_at=existing_interview.get("starts_at") if item["reschedule_interview_id"] and existing_interview else None,
                    ignore_duration_minutes=existing_interview.get("duration_minutes", 60) if item["reschedule_interview_id"] and existing_interview else None,
                    calendar_events_cache=calendar_events_cache,
                )
                if starts_at is None:
                    item.update({"status": "unable_to_schedule", "reason": slot_error})
                    unable_to_schedule_count += 1
                else:
                    item.update({
                        "status": "awaiting_confirmation",
                        "starts_at": starts_at,
                        "ends_at": starts_at + timedelta(minutes=round_config.duration_minutes),
                    })
                    reservations.append((starts_at, starts_at + timedelta(minutes=round_config.duration_minutes)))
                    awaiting_confirmation_count += 1
        elif eligibility == "awaiting_evaluation":
            awaiting_evaluation_count += 1
        items.append(item)

    plan = {
        "_id": new_id(),
        "status": "awaiting_confirmation",
        "job_id": job["_id"],
        "job_title": job["title"],
        "round": round_config.model_dump(mode="json"),
        "timezone": job.get("timezone", "UTC"),
        "window_start": window_start,
        "window_end": window_end,
        "instruction": prompt,
        "items": items,
        "eligible_count": eligible_count,
        "scheduled_count": 0,
        "awaiting_confirmation_count": awaiting_confirmation_count,
        "awaiting_evaluation_count": awaiting_evaluation_count,
        "unable_to_schedule_count": unable_to_schedule_count,
        "created_by": user["_id"],
        "created_at": datetime.now(timezone.utc),
    }
    # Only one open proposal per job and round: older ones can no longer be confirmed (prevents double booking).
    db[INTERVIEW_PLANS].update_many(
        {"job_id": job["_id"], "round.round_id": round_config.round_id, "status": "awaiting_confirmation"},
        {"$set": {"status": "superseded", "superseded_at": datetime.now(timezone.utc)}},
    )
    db[INTERVIEW_PLANS].insert_one(plan)
    return {
        "status": plan["status"],
        "plan_id": plan["_id"],
        "job_id": plan["job_id"],
        "job_title": plan["job_title"],
        "round": plan["round"],
        "timezone": plan["timezone"],
        "window_start": plan["window_start"],
        "window_end": plan["window_end"],
        "counts": {
            "eligible": eligible_count,
            "scheduled": 0,
            "awaiting_confirmation": awaiting_confirmation_count,
            "awaiting_evaluation": awaiting_evaluation_count,
            "unable_to_schedule": unable_to_schedule_count,
        },
        "items": items,
        "round_inferred": round_inferred,
        "requested_count": limit,
        "named_candidates": len(named_ids),
        "calendar_warning": calendar_error if unable_to_schedule_count else None,
        "availability_sources": {"hrms_bookings": True, "google_calendar_read_only": calendar is not None},
        "availability_notice": (
            None if calendar is not None else
            "Interviewer availability was checked against HRMS bookings only. Connect a read-only Google Calendar to include external calendars."
        ),
        "candidate_calendar_policy": "Candidate calendar availability is not checked. Interviews are confirmed by email; no calendar invitations are created.",
    }


@router.put("/jobs/{job_id}/rounds")
def save_job_rounds(
    job_id: str,
    data: InterviewRoundsWrite,
    db: MongoDatabase,
    user: CurrentUser,
) -> dict:
    job = db[JOBS].find_one({"_id": job_id})
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    previous = job.get("interview_rounds", [])
    rounds = [item.model_dump(mode="json") for item in data.rounds]
    db[JOBS].update_one(
        {"_id": job_id},
        {"$set": {"interview_rounds": rounds, "updated_at": datetime.now(timezone.utc)}},
    )
    db[AUDIT_LOGS].insert_one({
        "event": "interview_rounds_configured",
        "job_id": job_id,
        "user_id": user["_id"],
        "previous_rounds": previous,
        "new_rounds": rounds,
        "created_at": datetime.now(timezone.utc),
    })
    return {"job_id": job_id, "rounds": rounds}


@router.get("/jobs/{job_id}/rounds")
def get_job_rounds(job_id: str, db: MongoDatabase, user: CurrentUser) -> dict:
    job = db[JOBS].find_one({"_id": job_id}, {"interview_rounds": 1})
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return {"job_id": job_id, "rounds": job.get("interview_rounds", [])}


@router.get("/calendar-status")
def calendar_status(db: MongoDatabase, user: CurrentUser, job_id: str | None = None) -> dict:
    """Check the Google Calendar connection and every calendar the scheduler needs, with fix instructions."""
    try:
        calendar = GoogleCalendar()
    except CalendarNotConfigured as exc:
        return {
            "configured": False,
            "ok": False,
            "service_account_email": None,
            "host_calendar_id": None,
            "checks": [],
            "problem": str(exc),
            "steps": CALENDAR_SETUP_STEPS,
        }
    wanted: list[tuple[str, str]] = [(calendar.host_calendar_id, "HR host calendar (events are created here)")]
    if settings.google_calendar_hr_id not in (None, "", "primary"):
        wanted.append((settings.google_calendar_hr_id, "HR attendance calendar"))
    jobs = [db[JOBS].find_one({"_id": job_id})] if job_id else list(db[JOBS].find({"status": {"$ne": "archived"}}))
    for job in filter(None, jobs):
        for round_item in job.get("interview_rounds", []):
            for email in round_item.get("required_interviewers", []):
                wanted.append((email, f"Interviewer for {job.get('title')} · {round_item.get('name')}"))
    checks = []
    seen: set[str] = set()
    for calendar_id, role in wanted:
        if calendar_id in seen:
            continue
        seen.add(calendar_id)
        ok, problem = calendar.check_calendar(calendar_id)
        checks.append({"calendar_id": calendar_id, "role": role, "ok": ok, "problem": problem})
    all_ok = all(item["ok"] for item in checks)
    return {
        "configured": True,
        "ok": all_ok,
        "service_account_email": calendar.service_account_email or None,
        "host_calendar_id": calendar.host_calendar_id,
        "checks": checks,
        "problem": None if all_ok else "At least one calendar is not reachable by the service account.",
        "steps": [] if all_ok else CALENDAR_SETUP_STEPS,
    }


@router.post("/applications/{application_id}/progression-overrides", status_code=status.HTTP_201_CREATED)
def authorize_progression_override(
    application_id: str,
    data: ProgressionOverrideRequest,
    db: MongoDatabase,
    user: CurrentUser,
) -> dict:
    application = db[APPLICATIONS].find_one({"_id": application_id})
    if application is None:
        raise HTTPException(status_code=404, detail="Candidate application not found.")
    job = db[JOBS].find_one({"_id": application["job_id"]})
    round_config = next(
        (item for item in (job or {}).get("interview_rounds", []) if item.get("round_id") == data.round_id),
        None,
    )
    if round_config is None:
        raise HTTPException(status_code=404, detail="Configured interview round not found.")
    if application.get("stage") in TERMINAL_STAGES:
        raise HTTPException(status_code=409, detail="Progression override cannot change a hired or rejected application.")
    existing = db[PROGRESSION_OVERRIDES].find_one({
        "application_id": application_id,
        "round_id": data.round_id,
        "active": True,
    })
    if existing:
        raise HTTPException(status_code=409, detail="An active progression override already exists for this round.")
    override = {
        "_id": new_id(),
        "application_id": application_id,
        "job_id": application["job_id"],
        "round_id": data.round_id,
        "round_name": round_config["name"],
        "reason": data.reason.strip(),
        "explicitly_authorized": True,
        "active": True,
        "authorized_by": user["_id"],
        "created_at": datetime.now(timezone.utc),
    }
    try:
        db[PROGRESSION_OVERRIDES].insert_one(override)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="An active progression override already exists for this round.")
    db[AUDIT_LOGS].insert_one({
        "event": "interview_progression_override_authorized",
        "application_id": application_id,
        "job_id": application["job_id"],
        "round_id": data.round_id,
        "override_id": override["_id"],
        "reason": override["reason"],
        "user_id": user["_id"],
        "created_at": datetime.now(timezone.utc),
    })
    return {key: value for key, value in override.items() if key != "authorized_by"}


@router.get("/applications/{application_id}/progression-overrides")
def list_progression_overrides(
    application_id: str,
    db: MongoDatabase,
    user: CurrentUser,
) -> list[dict]:
    if db[APPLICATIONS].find_one({"_id": application_id}, {"_id": 1}) is None:
        raise HTTPException(status_code=404, detail="Candidate application not found.")
    return [
        {
            "id": item["_id"],
            "round_id": item["round_id"],
            "round_name": item["round_name"],
            "reason": item["reason"],
            "active": item["active"],
            "created_at": item["created_at"],
            "revoked_at": item.get("revoked_at"),
        }
        for item in db[PROGRESSION_OVERRIDES].find({"application_id": application_id}).sort("created_at", -1)
    ]


@router.delete("/applications/{application_id}/progression-overrides/{round_id}")
def revoke_progression_override(
    application_id: str,
    round_id: str,
    db: MongoDatabase,
    user: CurrentUser,
) -> dict:
    override = db[PROGRESSION_OVERRIDES].find_one_and_update(
        {"application_id": application_id, "round_id": round_id, "active": True},
        {"$set": {"active": False, "revoked_by": user["_id"], "revoked_at": datetime.now(timezone.utc)}},
        return_document=ReturnDocument.AFTER,
    )
    if override is None:
        raise HTTPException(status_code=404, detail="Active progression override not found.")
    db[AUDIT_LOGS].insert_one({
        "event": "interview_progression_override_revoked",
        "application_id": application_id,
        "job_id": override["job_id"],
        "round_id": round_id,
        "override_id": override["_id"],
        "user_id": user["_id"],
        "created_at": datetime.now(timezone.utc),
    })
    return {"status": "revoked", "override_id": override["_id"]}


@router.post("/scheduling-plans", status_code=status.HTTP_201_CREATED)
def create_scheduling_plan(
    data: InterviewSchedulePlanRequest,
    db: MongoDatabase,
    user: CurrentUser,
) -> dict:
    if not any(term in data.instruction.casefold() for term in ("schedule", "reschedule", "book")):
        raise HTTPException(status_code=422, detail="Give an explicit instruction to schedule or reschedule interviews.")
    return _create_plan(db, data, user)


@router.post("/assistant")
def interview_assistant(
    data: InterviewSchedulePlanRequest,
    db: MongoDatabase,
    user: CurrentUser,
) -> dict:
    prompt = data.instruction.casefold()
    job, matches = _resolve_job_candidates(db, data.instruction, data.job_id)
    if any(term in prompt for term in ("awaiting evaluation", "pending evaluation", "not evaluated")):
        if job is None:
            return _job_clarification(matches, "Which job should I check?")
        round_config = _resolve_round(job, data.instruction)
        if round_config is None:
            return _clarification(f"Which interview round should I check for {job['title']}?")
        results = []
        for interview in db[INTERVIEWS].find({
            "round_id": round_config.round_id,
            "status": "completed",
            "$or": [{"outcome": "pending_evaluation"}, {"outcome": {"$exists": False}}],
        }).sort("starts_at", -1):
            application = db[APPLICATIONS].find_one({"_id": interview["application_id"], "job_id": job["_id"]})
            candidate = db[CANDIDATES].find_one({"_id": application["candidate_id"]}) if application else None
            if application and candidate:
                results.append({
                    "application_id": application["_id"],
                    "candidate_name": candidate["name"],
                    "candidate_email": candidate["email"],
                    "round_name": round_config.name,
                    "interview_id": interview["_id"],
                    "completed_at": interview.get("starts_at"),
                    "outcome": interview.get("outcome", "pending_evaluation"),
                })
        return {
            "status": "results",
            "message": f"{len(results)} completed {round_config.name} interviews are awaiting evaluation.",
            "results": results,
            "source": "mongodb",
        }
    if any(term in prompt for term in ("schedule", "reschedule", "book")):
        return _create_plan(db, data, user)
    return _clarification("Would you like to schedule a round or list completed interviews awaiting evaluation?")


@router.get("/rounds/{round_id}/outcomes")
def list_round_outcomes(
    round_id: str,
    job_id: str,
    db: MongoDatabase,
    user: CurrentUser,
    outcome: str = Query(default="pending_evaluation", pattern="^(pending_evaluation|passed|failed|on_hold)$"),
) -> list[dict]:
    job = db[JOBS].find_one({"_id": job_id})
    if job is None or not any(item.get("round_id") == round_id for item in job.get("interview_rounds", [])):
        raise HTTPException(status_code=404, detail="Configured interview round not found.")
    query = {
        "round_id": round_id,
        "status": "completed",
        "$or": [{"outcome": outcome}, {"outcome": {"$exists": False}}] if outcome == "pending_evaluation" else [{"outcome": outcome}],
    }
    matches = []
    for interview in db[INTERVIEWS].find(query).sort("starts_at", -1):
        application = db[APPLICATIONS].find_one({"_id": interview["application_id"], "job_id": job_id})
        candidate = db[CANDIDATES].find_one({"_id": application["candidate_id"]}) if application else None
        if application and candidate:
            matches.append({
                "application_id": application["_id"],
                "candidate_name": candidate["name"],
                "candidate_email": candidate["email"],
                "interview_id": interview["_id"],
                "round_name": interview.get("round_name"),
                "outcome": interview.get("outcome", "pending_evaluation"),
                "starts_at": interview["starts_at"],
            })
    return matches


@router.post("/scheduling-plans/{plan_id}/confirm")
def confirm_scheduling_plan(
    plan_id: str,
    db: MongoDatabase,
    user: CurrentUser,
) -> dict:
    plan = db[INTERVIEW_PLANS].find_one_and_update(
        {"_id": plan_id, "status": "awaiting_confirmation"},
        {"$set": {"status": "confirming", "confirmed_by": user["_id"], "confirmed_at": datetime.now(timezone.utc)}},
        return_document=ReturnDocument.AFTER,
    )
    if plan is None:
        existing = db[INTERVIEW_PLANS].find_one({"_id": plan_id})
        if existing is None:
            raise HTTPException(status_code=404, detail="Scheduling plan not found.")
        if existing.get("status") in ("confirmed", "partially_confirmed"):
            return {
                "status": existing["status"],
                "counts": {
                    "eligible": existing["eligible_count"],
                    "scheduled": existing["scheduled_count"],
                    "awaiting_confirmation": existing["awaiting_confirmation_count"],
                    "awaiting_evaluation": existing["awaiting_evaluation_count"],
                    "unable_to_schedule": existing["unable_to_schedule_count"],
                },
                "items": existing["items"],
                "notifications": _notification_summary(existing["items"]),
            }
        if existing.get("status") == "superseded":
            raise HTTPException(status_code=409, detail="A newer plan replaced this one. Prepare the plan again and confirm the latest version.")
        raise HTTPException(status_code=409, detail="This scheduling plan is already being confirmed or cannot be confirmed.")

    job = db[JOBS].find_one({"_id": plan["job_id"]})
    if job is None:
        db[INTERVIEW_PLANS].update_one({"_id": plan_id}, {"$set": {"status": "failed"}})
        raise HTTPException(status_code=404, detail="The job associated with this plan no longer exists.")
    round_config = InterviewRoundConfig.model_validate(plan["round"])
    calendar, _calendar_problem = _optional_calendar()

    now = datetime.now(timezone.utc)
    scheduled_count = 0
    unable_count = plan["unable_to_schedule_count"]
    for item in plan["items"]:
        if item["status"] != "awaiting_confirmation":
            continue
        app = db[APPLICATIONS].find_one({"_id": item["application_id"], "job_id": plan["job_id"]})
        candidate = db[CANDIDATES].find_one({"_id": item["candidate_id"]})
        if app is None or candidate is None:
            item.update({"status": "unable_to_schedule", "reason": "Application or candidate no longer exists."})
            unable_count += 1
            continue
        if item.get("progression_override_id") and db[PROGRESSION_OVERRIDES].find_one({
            "_id": item["progression_override_id"],
            "application_id": app["_id"],
            "round_id": round_config.round_id,
            "active": True,
        }) is None:
            item.update({
                "status": "ineligible",
                "reason": "The progression override used for this plan was revoked before confirmation.",
                "starts_at": None,
                "ends_at": None,
            })
            continue
        eligibility, reason = _eligibility(
            db,
            app,
            round_config,
            _round_passed_explicitly(job, plan["instruction"]),
            allow_reschedule=bool(item.get("reschedule_interview_id")),
        )
        if eligibility != "eligible":
            item.update({"status": eligibility, "reason": reason, "starts_at": None, "ends_at": None})
            if eligibility == "awaiting_evaluation":
                plan["awaiting_evaluation_count"] += 1
            continue
        start = item["starts_at"]
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        end = start + timedelta(minutes=round_config.duration_minutes)
        if start <= now or start < plan["window_start"] or end > plan["window_end"]:
            item.update({"status": "unable_to_schedule", "reason": "The approved slot is no longer within the valid scheduling window."})
            unable_count += 1
            continue
        existing = _latest_round_interview(db, app["_id"], round_config.round_id)
        ignore_interview_id = existing["_id"] if existing and item.get("reschedule_interview_id") else None
        available, conflicting_calendar = True, None
        if calendar is not None:  # read-only availability check; HRMS bookings are checked below regardless
            try:
                available, conflicting_calendar = calendar.is_available(_round_calendar_ids(calendar, round_config), start, end)
            except CalendarUnavailable as exc:
                item.update({"status": "unable_to_schedule", "reason": str(exc)})
                unable_count += 1
                continue
        if not available or _local_conflict(
            db,
            list(round_config.required_interviewers),
            start,
            end,
            ignore_interview_id=ignore_interview_id,
        ):
            item.update({
                "status": "unable_to_schedule",
                "reason": f"Availability changed after plan review{f' for {conflicting_calendar}' if conflicting_calendar else ''}; create a new plan.",
            })
            unable_count += 1
            continue
        if existing and existing.get("status") == "completed":
            item.update({"status": "ineligible", "reason": "This round was completed after the plan was prepared."})
            continue

        previous_interview = existing if existing and existing.get("status") == "scheduled" else None
        try:
            interview = {
                "_id": existing["_id"] if existing else new_id(),
                "application_id": app["_id"],
                "job_id": job["_id"],
                "starts_at": start,
                "timezone": plan["timezone"],
                "interview_type": round_config.interview_type,
                "interviewers": list(round_config.required_interviewers),
                "interviewer_emails": list(round_config.required_interviewers),
                "round_id": round_config.round_id,
                "round_name": round_config.name,
                "duration_minutes": round_config.duration_minutes,
                "location": round_config.location,
                "notes": "Scheduled by HR; details were sent by email.",
                "status": "scheduled",
                "outcome": "pending_evaluation",
                "progression_override_id": item.get("progression_override_id"),
                "updated_at": now,
            }
            if previous_interview:
                invalidate_meeting(db, previous_interview, "rescheduled")
                revoke_tokens(db, previous_interview["_id"], "rescheduled")
                interview.update({"meeting_status": None, "meeting_url": None, "meeting_id": None, "meeting_expires_at": None})
            if existing:
                updated = db[INTERVIEWS].update_one(
                    {"_id": existing["_id"], "status": existing.get("status")},
                    {"$set": interview},
                )
                if updated.matched_count != 1:
                    raise DuplicateKeyError("Interview changed while booking.")
            else:
                db[INTERVIEWS].insert_one(interview)
            db[AUDIT_LOGS].insert_one({
                "event": "interview_round_rescheduled" if existing and existing.get("status") == "scheduled" else "interview_round_scheduled",
                "job_id": job["_id"],
                "application_id": app["_id"],
                "interview_id": interview["_id"],
                "round_id": round_config.round_id,
                "round_name": round_config.name,
                "progression_override_id": item.get("progression_override_id"),
                "user_id": user["_id"],
                "created_at": now,
            })
            pipeline_saved = set_pipeline_status(
                db,
                app["_id"],
                f"Shortlisted for {round_config.name}",
                round_id=round_config.round_id,
                user_id=user["_id"],
                move_to_interview_stage=True,
            )
            item.update({
                "status": "scheduled",
                "reason": None,
                "starts_at": start,
                "ends_at": end,
                "interview_id": interview["_id"],
                "pipeline_status": f"Shortlisted for {round_config.name}" if pipeline_saved else None,
                "meeting": round_config.location,
                "interviewers": list(round_config.required_interviewers),
            })
            scheduled_count += 1
            try:
                results = notify_interview(
                    db,
                    interview=interview,
                    candidate=candidate,
                    job=job,
                    user_id=user["_id"],
                    extra_interviewers=_hr_attendee(round_config),
                )
                item.update({"notifications": results, "email_status": summarize_notifications(results)})
            except Exception:
                logger.exception("Interview notification step failed for application %s", app["_id"])
                item.update({"notifications": [], "email_status": "failed"})
            item.update({
                "meeting_status": interview.get("meeting_status"),
                "meeting_url": interview.get("meeting_url"),
                "meeting_error": interview.get("meeting_error"),
            })
        except DuplicateKeyError:
            item.update({"status": "unable_to_schedule", "reason": "A booking for this candidate and round already exists."})
            unable_count += 1
        except Exception as exc:
            logger.exception("Interview booking failed for application %s (%s)", app["_id"], type(exc).__name__)
            item.update({"status": "unable_to_schedule", "reason": "Booking failed while saving the interview. Nothing was sent; try again."})
            unable_count += 1

    remaining = sum(item["status"] == "awaiting_confirmation" for item in plan["items"])
    final_status = "confirmed" if not remaining and not unable_count else "partially_confirmed"
    db[INTERVIEW_PLANS].update_one(
        {"_id": plan_id},
        {"$set": {
            "status": final_status,
            "items": plan["items"],
            "scheduled_count": scheduled_count,
            "awaiting_confirmation_count": remaining,
            "awaiting_evaluation_count": plan["awaiting_evaluation_count"],
            "unable_to_schedule_count": unable_count,
            "completed_at": datetime.now(timezone.utc),
        }},
    )
    db[AUDIT_LOGS].insert_one({
        "event": "interview_scheduling_plan_confirmed",
        "job_id": plan["job_id"],
        "plan_id": plan_id,
        "user_id": user["_id"],
        "scheduled_count": scheduled_count,
        "unable_to_schedule_count": unable_count,
        "created_at": datetime.now(timezone.utc),
    })
    return {
        "status": final_status,
        "counts": {
            "eligible": plan["eligible_count"],
            "scheduled": scheduled_count,
            "awaiting_confirmation": remaining,
            "awaiting_evaluation": plan["awaiting_evaluation_count"],
            "unable_to_schedule": unable_count,
        },
        "items": plan["items"],
        "notifications": _notification_summary(plan["items"]),
    }


@router.get("/scheduling-plans/{plan_id}")
def get_scheduling_plan(plan_id: str, db: MongoDatabase, user: CurrentUser) -> dict:
    plan = db[INTERVIEW_PLANS].find_one({"_id": plan_id})
    if plan is None:
        raise HTTPException(status_code=404, detail="Scheduling plan not found.")
    return {
        "status": plan["status"],
        "plan_id": plan_id,
        "job_title": plan["job_title"],
        "round": plan["round"],
        "timezone": plan["timezone"],
        "counts": {
            "eligible": plan["eligible_count"],
            "scheduled": plan["scheduled_count"],
            "awaiting_confirmation": plan["awaiting_confirmation_count"],
            "awaiting_evaluation": plan["awaiting_evaluation_count"],
            "unable_to_schedule": plan["unable_to_schedule_count"],
        },
        "items": plan["items"],
        "notifications": _notification_summary(plan["items"]),
    }


@router.post("/scheduling-plans/{plan_id}/resend-notifications")
def resend_plan_notifications(plan_id: str, db: MongoDatabase, user: CurrentUser) -> dict:
    """Retry emails that were not sent. Already-sent emails are never sent again (idempotency keys)."""
    plan = db[INTERVIEW_PLANS].find_one({"_id": plan_id})
    if plan is None:
        raise HTTPException(status_code=404, detail="Scheduling plan not found.")
    if plan["status"] not in ("confirmed", "partially_confirmed"):
        raise HTTPException(status_code=409, detail="Confirm the plan before sending notifications.")
    job = db[JOBS].find_one({"_id": plan["job_id"]})
    round_config = InterviewRoundConfig.model_validate(plan["round"])
    for item in plan["items"]:
        if item.get("status") != "scheduled" or item.get("email_status") == "sent":
            continue
        interview = db[INTERVIEWS].find_one({"_id": item.get("interview_id"), "status": "scheduled"})
        candidate = db[CANDIDATES].find_one({"_id": item["candidate_id"]})
        if not (job and interview and candidate):
            continue
        results = notify_interview(
            db, interview=interview, candidate=candidate, job=job, user_id=user["_id"],
            extra_interviewers=_hr_attendee(round_config),
        )
        item.update({"notifications": results, "email_status": summarize_notifications(results)})
    db[INTERVIEW_PLANS].update_one({"_id": plan_id}, {"$set": {"items": plan["items"]}})
    return get_scheduling_plan(plan_id, db, user)
