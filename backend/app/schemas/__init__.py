"""Pydantic request and response schemas for the recruitment API."""

from datetime import date, datetime
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class UserRead(BaseModel):
    id: str
    email: EmailStr
    role: str

    model_config = ConfigDict(from_attributes=True)


class TokenRead(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead


class JobQuestion(BaseModel):
    key: str = Field(min_length=1, max_length=60, pattern="^[a-zA-Z0-9_-]+$")
    label: str = Field(min_length=1, max_length=200)
    required: bool = True


class InterviewRoundConfig(BaseModel):
    round_id: str = Field(default_factory=lambda: uuid4().hex)
    name: str = Field(min_length=2, max_length=120)
    order: int = Field(ge=1, le=50)
    duration_minutes: int = Field(default=60, ge=15, le=240)
    interview_type: Literal["phone", "video", "onsite", "technical", "other"] = "technical"
    required_interviewers: list[EmailStr] = Field(min_length=1, max_length=20)
    requires_hr: bool = False
    prerequisite_round_ids: list[str] = Field(default_factory=list, max_length=20)
    location: str | None = Field(default=None, max_length=500)
    working_day_start_hour: int = Field(default=9, ge=0, le=23)
    working_day_end_hour: int = Field(default=17, ge=1, le=24)
    slot_interval_minutes: int = Field(default=30, ge=15, le=120)

    @model_validator(mode="after")
    def validate_working_hours(self) -> "InterviewRoundConfig":
        if self.working_day_end_hour <= self.working_day_start_hour:
            raise ValueError("Working day end must be after its start.")
        if self.interview_type in ("video", "onsite") and not self.location:
            raise ValueError("Video and on-site rounds require a meeting link or location.")
        return self


class InterviewRoundsWrite(BaseModel):
    rounds: list[InterviewRoundConfig] = Field(max_length=50)

    @model_validator(mode="after")
    def validate_rounds(self) -> "InterviewRoundsWrite":
        ids = [item.round_id for item in self.rounds]
        orders = [item.order for item in self.rounds]
        if len(ids) != len(set(ids)):
            raise ValueError("Interview round IDs must be unique.")
        if len(orders) != len(set(orders)):
            raise ValueError("Interview round order values must be unique.")
        known_ids = set(ids)
        for item in self.rounds:
            if item.round_id in item.prerequisite_round_ids:
                raise ValueError("An interview round cannot require itself.")
            if not set(item.prerequisite_round_ids).issubset(known_ids):
                raise ValueError("Every prerequisite must refer to a configured round.")
            if any(
                next(round_item for round_item in self.rounds if round_item.round_id == prerequisite).order
                >= item.order
                for prerequisite in item.prerequisite_round_ids
            ):
                raise ValueError("Prerequisite rounds must come earlier in the configured order.")
        return self


class InterviewSchedulePlanRequest(BaseModel):
    instruction: str = Field(min_length=5, max_length=1000)
    job_id: str | None = None
    round_id: str | None = None
    application_ids: list[str] | None = Field(default=None, max_length=500)
    window_start: datetime | None = None
    window_end: datetime | None = None
    window_start_date: date | None = None
    window_end_date: date | None = None

    @model_validator(mode="after")
    def validate_window(self) -> "InterviewSchedulePlanRequest":
        if bool(self.window_start_date) != bool(self.window_end_date):
            raise ValueError("Choose both a first and last interview date.")
        if self.window_start_date and self.window_end_date and self.window_end_date < self.window_start_date:
            raise ValueError("The last interview date must not be before the first.")
        for value in (self.window_start, self.window_end):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError("Scheduling window dates must include a timezone.")
        if self.window_start and self.window_end and self.window_end <= self.window_start:
            raise ValueError("Scheduling window end must be after its start.")
        return self


class InterviewOutcomeUpdate(BaseModel):
    outcome: Literal["pending_evaluation", "passed", "failed", "on_hold"]
    evaluation_notes: str = Field(default="", max_length=5000)


class ProgressionOverrideRequest(BaseModel):
    round_id: str
    reason: str = Field(min_length=10, max_length=1000)
    explicitly_authorized: Literal[True]


DEFAULT_SCORING_WEIGHTS = {
    "skills": 30.0,
    "experience_projects": 25.0,
    "responsibilities": 25.0,
    "education": 10.0,
    "preferred": 10.0,
}


class JobCreate(BaseModel):
    title: str = Field(min_length=2, max_length=180)
    department: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=10000)
    responsibilities: list[str] = Field(default_factory=list, max_length=50)
    required_skills: list[str] = Field(default_factory=list, max_length=100)
    preferred_skills: list[str] = Field(default_factory=list, max_length=100)
    minimum_experience: float | None = Field(default=None, ge=0, le=80)
    maximum_experience: float | None = Field(default=None, ge=0, le=80)
    education_requirements: list[str] = Field(default_factory=list, max_length=30)
    mandatory_criteria: list[str] = Field(default_factory=list, max_length=50)
    preferred_qualifications: list[str] = Field(default_factory=list, max_length=50)
    employment_type: str | None = Field(default=None, max_length=40)
    location: str | None = Field(default=None, max_length=180)
    openings: int = Field(default=1, ge=1, le=1000)
    opening_date: datetime | None = None
    deadline: datetime | None = None
    timezone: str = Field(default="UTC", min_length=1, max_length=80)
    questions: list[JobQuestion] = Field(default_factory=list, max_length=20)
    interview_rounds: list[InterviewRoundConfig] = Field(default_factory=list, max_length=50)
    scoring_weights: dict[str, float] = Field(default_factory=lambda: DEFAULT_SCORING_WEIGHTS.copy())
    status: Literal["draft", "published", "closed", "archived"] = "draft"

    @field_validator("scoring_weights")
    @classmethod
    def validate_scoring_weights(cls, values: dict[str, float]) -> dict[str, float]:
        if set(values) != set(DEFAULT_SCORING_WEIGHTS):
            raise ValueError(f"Scoring weights must use exactly: {', '.join(DEFAULT_SCORING_WEIGHTS)}")
        if any(weight < 0 or weight > 100 for weight in values.values()):
            raise ValueError("Scoring weights must be between 0 and 100.")
        if round(sum(values.values()), 4) != 100:
            raise ValueError("Scoring weights must total 100.")
        return values

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Enter a valid IANA timezone.")
        return value

    @field_validator("opening_date", "deadline")
    @classmethod
    def require_aware_job_dates(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Opening and deadline values must include a timezone.")
        return value

    @model_validator(mode="after")
    def validate_date_order(self) -> "JobCreate":
        if self.minimum_experience is not None and self.maximum_experience is not None:
            if self.maximum_experience < self.minimum_experience:
                raise ValueError("Maximum experience must be greater than or equal to minimum experience.")
        if self.opening_date and self.deadline and self.deadline <= self.opening_date:
            raise ValueError("Application deadline must be after the opening date.")
        InterviewRoundsWrite(rounds=self.interview_rounds)
        return self

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        return value.strip()

class JobRead(JobCreate):
    id: str
    is_active: bool
    public_url: str | None = None
    application_count: int = 0


class PublicJobRead(BaseModel):
    public_token: str
    title: str
    department: str | None
    description: str | None
    responsibilities: list[str]
    required_skills: list[str]
    preferred_skills: list[str]
    minimum_experience: float | None
    education_requirements: list[str]
    location: str | None
    employment_type: str | None
    opening_date: datetime | None
    deadline: datetime | None
    timezone: str
    questions: list[JobQuestion]
    status: str

    model_config = ConfigDict(from_attributes=True)


class CandidateRead(BaseModel):
    id: str
    name: str
    email: EmailStr
    phone: str | None
    skills: list[str]
    experience_years: float | None
    education: str | None

    model_config = ConfigDict(from_attributes=True)


class ApplicationReview(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=40)
    skills: list[str] = Field(default_factory=list, max_length=100)
    experience_years: float | None = Field(default=None, ge=0, le=80)
    education: str | None = Field(default=None, max_length=5000)
    stage: Literal["needs_review", "screening", "interview", "offer", "hired", "rejected"]
    hr_notes: str = Field(default="", max_length=10000)


class ShortlistRequest(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=500)
    apply: bool = False
    reassess: bool = True


class ApplicationRead(BaseModel):
    id: str
    stage: str
    parsed_resume: dict
    resume_filename: str
    resume_content_type: str
    resume_size: int
    applied_at: datetime
    candidate: CandidateRead
    job: JobRead
    pipeline_status: str | None = None
    assessment: dict | None = None
    shortlist_status: str | None = None
    shortlist_rank: int | None = None
    processing_status: str = "pending"
    parsing_status: str = "pending"
    embedding_status: str = "pending"
    matching_status: str = "pending"
    parser_version: str | None = None
    ats_score: float | None = None
    criterion_scores: dict = Field(default_factory=dict)
    matching_evidence: list[dict] = Field(default_factory=list)
    missing_criteria: list[str] = Field(default_factory=list)
    mandatory_criteria_results: list[dict] = Field(default_factory=list)
    recommendation: str = "pending"
    confidence: str = "unknown"
    scoring_version: str | None = None
    hr_notes: str = ""
    additional_answers: dict[str, str] = Field(default_factory=dict)


class PublicApplicationReceipt(BaseModel):
    application_id: str
    status: str
    message: str


class PublicApplicationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=40)
    education: str | None = Field(default=None, max_length=5000)
    experience_years: float | None = Field(default=None, ge=0, le=80)


class SemanticSearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=1000)
    limit: int = Field(default=10, ge=1, le=50)


class AssistantMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2500)


class AssistantRequest(BaseModel):
    message: str = Field(min_length=2, max_length=1000)
    history: list[AssistantMessage] = Field(default_factory=list, max_length=20)
    application_id: str | None = None
    job_id: str | None = None
    context: dict | None = None  # e.g. {"plan_id": "..."} so "these candidates" resolves to the last proposal

    @field_validator("message")
    @classmethod
    def require_nonempty_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Enter a message for the recruitment assistant.")
        return value.strip()


class EmailDraftRequest(BaseModel):
    email_type: Literal["shortlist", "rejection", "follow_up", "interview_invitation"]
    message: str = Field(default="", max_length=5000)


class EmailDraftUpdate(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=10000)

    @field_validator("subject", "body")
    @classmethod
    def trim_nonempty_email_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Email subject and message must not be empty.")
        return value


class SemanticSearchResult(BaseModel):
    application_id: str
    candidate_name: str
    job_title: str
    score: float
    evidence: str


class GoogleFormSubmission(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=40)
    job_title: str = Field(min_length=2, max_length=180)
    resume_filename: str = Field(min_length=1, max_length=255)
    resume_content_type: str
    resume_base64: str = Field(min_length=1, max_length=7_000_508)


class InterviewWrite(BaseModel):
    application_id: str
    starts_at: datetime
    timezone: str = Field(min_length=1, max_length=80)
    interview_type: Literal["phone", "video", "onsite", "technical", "other"]
    interviewers: list[str] = Field(min_length=1, max_length=20)
    round_id: str | None = None
    round_name: str | None = Field(default=None, max_length=120)
    duration_minutes: int = Field(default=60, ge=15, le=240)
    interviewer_emails: list[EmailStr] = Field(default_factory=list, max_length=20)
    location: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=5000)
    status: Literal["scheduled", "completed", "cancelled"] = "scheduled"
    outcome: Literal["pending_evaluation", "passed", "failed", "on_hold"] = "pending_evaluation"
    evaluation_notes: str = Field(default="", max_length=5000)

    @field_validator("starts_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Interview date/time must include a timezone")
        return value


class InterviewRead(InterviewWrite):
    id: str
    progression_override_id: str | None = None
    candidate_name: str
    candidate_email: EmailStr
    job_title: str
    pipeline_status: str | None = None
    email_status: str | None = None
    meeting_status: str | None = None
    meeting_url: str | None = None
    meeting_error: str | None = None
