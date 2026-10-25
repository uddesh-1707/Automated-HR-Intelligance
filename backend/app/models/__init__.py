"""MongoDB collection names and startup index definitions."""

from uuid import uuid4

from pymongo.database import Database


USERS = "users"
JOBS = "jobs"
CANDIDATES = "candidates"
APPLICATIONS = "applications"
INTERVIEWS = "interviews"
AUDIT_LOGS = "audit_logs"
EMAIL_LOGS = "email_logs"
INTERVIEW_PLANS = "interview_plans"
PROGRESSION_OVERRIDES = "progression_overrides"
EVALUATION_TOKENS = "evaluation_tokens"


def new_id() -> str:
    return str(uuid4())


def create_indexes(db: Database) -> None:
    db[USERS].create_index("email", unique=True)
    db[JOBS].create_index("title_normalized", unique=True)
    db[JOBS].create_index("public_token", unique=True, sparse=True)
    db[JOBS].create_index([("status", 1), ("deadline", 1)])
    db[CANDIDATES].create_index("email", unique=True)
    db[APPLICATIONS].create_index(
        [("candidate_id", 1), ("job_id", 1)],
        unique=True,
        name="uq_application_candidate_job",
    )
    db[APPLICATIONS].create_index("stage")
    db[APPLICATIONS].create_index("applied_at")
    db[APPLICATIONS].create_index([("job_id", 1), ("stage", 1), ("ats_score", -1)])
    db[APPLICATIONS].create_index([("processing_status", 1), ("updated_at", 1)])
    db[INTERVIEWS].create_index("application_id")
    db[INTERVIEWS].create_index([("status", 1), ("starts_at", 1)])
    db[INTERVIEWS].create_index(
        [("application_id", 1), ("round_id", 1)],
        unique=True,
        partialFilterExpression={"round_id": {"$exists": True}},
        name="uq_application_interview_round",
    )
    db[INTERVIEW_PLANS].create_index([("status", 1), ("created_at", -1)])
    db[PROGRESSION_OVERRIDES].create_index(
        [("application_id", 1), ("round_id", 1)],
        unique=True,
        partialFilterExpression={"active": True},
        name="uq_active_progression_override",
    )
    db[PROGRESSION_OVERRIDES].create_index([("application_id", 1), ("created_at", -1)])
    db[EVALUATION_TOKENS].create_index("token_hash", unique=True)
    db[EVALUATION_TOKENS].create_index("interview_id")
    db[AUDIT_LOGS].create_index([("application_id", 1), ("created_at", -1)])
    db[AUDIT_LOGS].create_index([("job_id", 1), ("created_at", -1)])
    db[EMAIL_LOGS].create_index("idempotency_key", unique=True)
    db[EMAIL_LOGS].create_index("retry_of", unique=True, sparse=True)
    db[EMAIL_LOGS].create_index([("application_id", 1), ("delivery_status", 1)])
    db[EMAIL_LOGS].create_index([("application_id", 1), ("created_at", -1)])
