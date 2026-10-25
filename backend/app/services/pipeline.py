"""Keeps a candidate's human-readable pipeline status in step with their interview records."""

from datetime import datetime, timezone

from pymongo.database import Database

from app.models import APPLICATIONS, AUDIT_LOGS, JOBS

TERMINAL_STAGES = {"rejected", "hired"}


def pipeline_label(interview: dict, next_round: str | None = None, last_round: bool = False) -> str | None:
    """The status HR should see for the candidate given the state of this interview."""
    round_name = interview.get("round_name")
    if not round_name:
        return None
    status = interview.get("status")
    outcome = interview.get("outcome", "pending_evaluation")
    if status == "scheduled":
        return f"Shortlisted for {round_name}"
    if status == "cancelled":
        return f"Interview cancelled for {round_name}"
    if status == "completed":
        passed = f"Passed {round_name}"
        if next_round:
            passed += f" · eligible for {next_round}"
        elif last_round:
            passed += " · all configured rounds passed"
        return {
            "passed": passed,
            "failed": f"Not selected after {round_name}",
            "on_hold": f"On hold after {round_name}",
        }.get(outcome, f"Awaiting evaluation for {round_name}")
    return None


def set_pipeline_status(
    db: Database,
    application_id: str,
    label: str,
    *,
    round_id: str | None = None,
    user_id: str | None = None,
    move_to_interview_stage: bool = False,
) -> bool:
    """Persist the status and return True only when the application document was really updated."""
    application = db[APPLICATIONS].find_one({"_id": application_id}, {"stage": 1, "pipeline_status": 1})
    if application is None:
        return False
    now = datetime.now(timezone.utc)
    update = {"pipeline_status": label, "pipeline_round_id": round_id, "pipeline_updated_at": now, "updated_at": now}
    previous_stage = application.get("stage")
    if move_to_interview_stage and previous_stage not in TERMINAL_STAGES | {"interview"}:
        update["stage"] = "interview"
    result = db[APPLICATIONS].update_one({"_id": application_id}, {"$set": update})
    if result.matched_count != 1:
        return False
    if application.get("pipeline_status") != label or update.get("stage"):
        db[AUDIT_LOGS].insert_one({
            "event": "application_pipeline_status_updated",
            "application_id": application_id,
            "round_id": round_id,
            "previous_status": application.get("pipeline_status"),
            "new_status": label,
            "previous_stage": previous_stage,
            "new_stage": update.get("stage", previous_stage),
            "user_id": user_id,
            "created_at": now,
        })
    return True


def sync_pipeline_from_interview(db: Database, interview: dict | None, user_id: str | None = None) -> None:
    if not interview:
        return
    next_round, last_round = None, False
    if interview.get("status") == "completed" and interview.get("outcome") == "passed" and interview.get("job_id"):
        job = db[JOBS].find_one({"_id": interview["job_id"]}, {"interview_rounds": 1}) or {}
        rounds = sorted(job.get("interview_rounds", []), key=lambda item: item.get("order", 0))
        current = next((item for item in rounds if item.get("round_id") == interview.get("round_id")), None)
        if current:
            following = [item for item in rounds if item["order"] > current["order"]]
            next_round = following[0]["name"] if following else None
            last_round = not following
    label = pipeline_label(interview, next_round, last_round)
    if label:
        set_pipeline_status(
            db,
            interview["application_id"],
            label,
            round_id=interview.get("round_id"),
            user_id=user_id,
            move_to_interview_stage=interview.get("status") == "scheduled",
        )
