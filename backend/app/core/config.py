import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[3] / ".env",
        env_ignore_empty=True,
        extra="ignore",
    )

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_database: str = "peopleflow"
    jwt_secret_key: str = secrets.token_urlsafe(48)
    jwt_expiration_minutes: int = Field(default=60, ge=5, le=1440)
    resume_storage_dir: Path = Path("./private_uploads")
    max_resume_size_bytes: int = Field(default=5 * 1024 * 1024, ge=1, le=10 * 1024 * 1024)
    google_forms_integration_secret: str | None = None
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = Field(default=None, min_length=12, max_length=128)
    frontend_origin: str = "http://localhost:5173"
    embedding_api_key: str | None = None
    # Provider-independent LLM settings (LLM_PROVIDER, LLM_MODEL, LLM_API_KEY, LLM_BASE_URL, LLM_TEMPERATURE ...).
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_temperature: float = Field(default=0.0, ge=0, le=2)
    llm_max_tokens: int = Field(default=1024, ge=64, le=16000)
    llm_timeout_seconds: float = Field(default=45, ge=5, le=300)
    # Legacy names, still honoured when the LLM_* equivalents are unset.
    agent_api_key: str | None = None
    agent_api_base_url: str | None = None
    agent_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, ge=256, le=3072)
    embedding_model_version: str = "1"
    pinecone_api_key: str | None = None
    pinecone_index_name: str = "peopleflow-resumes"
    pinecone_cloud: str = "aws"
    pinecone_region: str = "us-east-1"
    matching_scoring_version: str = "hybrid-v1"
    public_application_url: str = "http://localhost:5173/apply"
    background_task_retries: int = Field(default=3, ge=0, le=10)
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    smtp_starttls: bool = True
    # Interview evaluation links and meeting provider
    evaluation_link_expiry_days: int = Field(default=14, ge=1, le=90)
    evaluation_allow_before_start: bool = False
    meeting_provider: str = "none"  # none | zoom | webhook
    meeting_link_expiry_hours: int = Field(default=24, ge=1, le=720)
    zoom_account_id: str | None = None
    zoom_client_id: str | None = None
    zoom_client_secret: str | None = None
    meeting_webhook_url: str | None = None
    meeting_webhook_secret: str | None = None
    hr_contact_email: str | None = None
    api_public_url: str = "http://localhost:8000/api/v1"
    # Resume shortlisting (weights are percentages and must total 100).
    shortlist_weights: dict[str, float] = Field(default_factory=lambda: {
        "required_skills": 40.0,
        "responsibilities": 20.0,
        "projects_internships": 20.0,
        "experience": 10.0,
        "preferred_skills": 10.0,
    })
    shortlist_min_score: float = Field(default=50.0, ge=0, le=100)
    shortlist_min_required_ratio: float = Field(default=0.5, ge=0, le=1)
    shortlist_default_limit: int = Field(default=10, ge=1, le=500)
    shortlist_semantic_threshold: float = Field(default=0.42, ge=0, le=1)
    shortlist_use_llm: bool = True
    skill_synonyms: dict[str, list[str]] = Field(default_factory=dict)
    google_calendar_service_account_file: Path | None = None
    google_calendar_host_id: str = ""
    google_calendar_hr_id: str | None = None

    @field_validator("jwt_secret_key")
    @classmethod
    def require_strong_jwt_secret(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("JWT_SECRET_KEY must contain at least 32 characters.")
        return value

    @field_validator("shortlist_weights")
    @classmethod
    def validate_shortlist_weights(cls, value: dict[str, float]) -> dict[str, float]:
        if abs(sum(value.values()) - 100) > 0.01 or any(item < 0 for item in value.values()):
            raise ValueError("SHORTLIST_WEIGHTS must be non-negative numbers totalling 100.")
        return value

    @field_validator("google_forms_integration_secret")
    @classmethod
    def validate_integration_secret(cls, value: str | None) -> str | None:
        if value and len(value) < 32:
            raise ValueError("GOOGLE_FORMS_INTEGRATION_SECRET must contain at least 32 characters.")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
