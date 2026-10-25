import base64
from datetime import datetime, timedelta, timezone

import mongomock

from app.api.v1 import interview_workflow
from app.api.v1 import interviews as interview_routes


class AvailableCalendar:
    host_calendar_id = "host@example.com"

    def __init__(self):
        self.events = []
        self.available = True

    def list_events(self, _calendar_id, _start, _end):
        return []

    def is_available(self, _calendar_ids, _start, _end, **_kwargs):
        return self.available, None if self.available else "interviewer@example.com"

    def create_event(self, **event):
        self.events.append(event)
        return {"id": event["event_id"]}

    def update_event(self, event_id, **event):
        self.events.append({"event_id": event_id, **event})
        return {"id": event_id}

    def delete_event(self, _event_id):
        return None


def test_job_title_resolution_handles_spacing_and_minor_spelling_differences():
    database = mongomock.MongoClient().test
    database["jobs"].insert_many([
        {"_id": "job-1", "title": "Front-End Developer", "status": "published"},
        {"_id": "job-2", "title": "Data Analyst", "status": "published"},
    ])

    resolved = interview_workflow._resolve_job(
        database,
        "Schedule Technical Round 1 for Frontend Developer applicants",
        None,
    )

    assert resolved["_id"] == "job-1"


def test_job_title_resolution_asks_for_clarification_when_titles_are_ambiguous():
    database = mongomock.MongoClient().test
    database["jobs"].insert_many([
        {"_id": "job-1", "title": "Frontend Developer", "status": "published"},
        {"_id": "job-2", "title": "Front End Developer", "status": "published"},
    ])

    assert interview_workflow._resolve_job(database, "Schedule for Frontend Developer", None) is None
    _job, matches = interview_workflow._resolve_job_candidates(database, "Schedule for Frontend Developer", None)
    assert {job["title"] for job in matches} == {"Frontend Developer", "Front End Developer"}
    clarification = interview_workflow._job_clarification(matches, "Which job is this scheduling request for?")
    assert clarification["status"] == "clarification_required"
    assert "Frontend Developer" in clarification["clarifying_question"]
    assert "Front End Developer" in clarification["clarifying_question"]
    assert {job["title"] for job in clarification["matching_jobs"]} == {"Frontend Developer", "Front End Developer"}


def test_job_title_resolution_accepts_intern_wording_and_word_order_variants():
    database = mongomock.MongoClient().test
    database["jobs"].insert_many([
        {"_id": "job-1", "title": "AI Automation Intern", "status": "published"},
        {"_id": "job-2", "title": "Data Analyst", "status": "published"},
    ])

    for prompt in (
        "Schedule Technical Interview 1 for AI Automation Internship",
        "Prepare a plan for Automation AI Intern candidates",
        "  ai   automation   intern  ",
    ):
        resolved = interview_workflow._resolve_job(database, prompt, None)
        assert resolved is not None, prompt
        assert resolved["_id"] == "job-1"


def test_round_resolution_accepts_round_as_alias_for_interview_and_minor_typo():
    resolved = interview_workflow._resolve_round(
        {
            "interview_rounds": [{
                "round_id": "technical-1",
                "name": "Technical Interview 1",
                "order": 1,
                "required_interviewers": ["interviewer@example.com"],
            }]
        },
        "Schedule Techincal Round 1",
    )

    assert resolved is not None
    assert resolved.round_id == "technical-1"


ROUNDS = {"interview_rounds": [
    {"round_id": "t1", "name": "Technical Interview 1 - DSA and Problem Solving", "order": 1,
     "required_interviewers": ["a@example.com"]},
    {"round_id": "t2", "name": "Technical Interview 2", "order": 2, "required_interviewers": ["a@example.com"]},
    {"round_id": "hr", "name": "HR Round", "order": 3, "required_interviewers": ["a@example.com"]},
]}


def test_round_resolution_understands_abbreviations_ordinals_and_numbers():
    resolve = lambda text: getattr(interview_workflow._resolve_round(ROUNDS, text), "round_id", None)

    assert resolve("schedule tech 2 for Pallavi") == "t2"
    assert resolve("Schedule tech 1 for 5 candidates") == "t1"
    assert resolve("book the second technical round") == "t2"
    assert resolve("schedule hr round for everyone") == "hr"
    assert resolve("schedule the final round") == "hr"
    assert resolve("schedule 5 candidates") is None
    assert resolve("schedule technical interview") is None  # ambiguous between rounds 1 and 2


def test_round_resolution_falls_back_to_semantic_similarity(monkeypatch):
    monkeypatch.setattr(
        interview_workflow,
        "semantic_scores",
        lambda _query, texts: [0.1, 0.1, 0.8] if len(texts) == 3 else None,
    )

    assert interview_workflow._resolve_round(ROUNDS, "schedule the culture chat").round_id == "hr"


def test_job_resolution_falls_back_to_semantic_similarity(monkeypatch):
    database = mongomock.MongoClient().test
    database["jobs"].insert_many([
        {"_id": "job-1", "title": "Machine Learning Engineer", "status": "published"},
        {"_id": "job-2", "title": "Accountant", "status": "published"},
    ])
    monkeypatch.setattr(interview_workflow, "semantic_scores", lambda _query, _texts: [0.7, 0.1])

    job, _ = interview_workflow._resolve_job_candidates(database, "schedule the AI modelling hire", None)

    assert job["_id"] == "job-1"


def test_requested_count_and_named_candidates():
    assert interview_workflow._requested_count("schedule the interview of 5 candidates") == 5
    assert interview_workflow._requested_count("schedule top three applicants") == 3
    assert interview_workflow._requested_count("schedule tech 2 within 5 days") is None
    pairs = [
        ({"_id": "a1"}, {"name": "Pallavi Ranamale", "email": "p@example.com"}),
        ({"_id": "a2"}, {"name": "Tejas Sawant", "email": "t@example.com"}),
    ]
    assert interview_workflow._named_application_ids("schedule tech 2 for Pallavi", pairs, set()) == {"a1"}
    assert interview_workflow._named_application_ids("schedule tech 2 for tejas sawant", pairs, set()) == {"a2"}
    assert interview_workflow._named_application_ids("schedule tech 2 for everyone", pairs, set()) == set()


def test_plan_honours_count_names_round_abbreviation_and_selected_dates(client, admin_headers, monkeypatch):
    fake_calendar = AvailableCalendar()
    monkeypatch.setattr(interview_workflow, "GoogleCalendar", lambda: fake_calendar)
    job_id = create_job(client, admin_headers)
    configure_rounds(client, admin_headers, job_id)
    applications = {
        name: create_application(client, job_id, f"{name.lower()}@example.com", f"{name} Tester")
        for name in ("Anna", "Bella", "Carla")
    }
    for application in applications.values():
        record_round_one(client, admin_headers, application, "passed")

    by_name = client.post(
        "/api/v1/interviews/assistant",
        headers=admin_headers,
        json={"job_id": job_id, "instruction": "Schedule tech 2 for Bella"},
    ).json()
    assert by_name["round"]["round_id"] == "technical-2"
    assert [item["candidate_email"] for item in by_name["items"]] == ["bella@example.com"]

    counted = client.post(
        "/api/v1/interviews/assistant",
        headers=admin_headers,
        json={"job_id": job_id, "instruction": "Schedule tech 2 interviews for 2 candidates"},
    ).json()
    assert counted["counts"]["awaiting_confirmation"] == 2
    assert len(counted["items"]) == 2

    inferred = client.post(
        "/api/v1/interviews/assistant",
        headers=admin_headers,
        json={"job_id": job_id, "instruction": "Schedule the interview of 2 candidates"},
    ).json()
    assert inferred["round"]["round_id"] == "technical-2"
    assert inferred["round_inferred"] is True

    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=3)
    dated = client.post(
        "/api/v1/interviews/scheduling-plans",
        headers=admin_headers,
        json={
            "job_id": job_id,
            "round_id": "technical-2",
            "instruction": "Schedule the selected round",
            "window_start_date": tomorrow.isoformat(),
            "window_end_date": tomorrow.isoformat(),
        },
    )
    assert dated.status_code == 201, dated.text
    starts = [datetime.fromisoformat(item["starts_at"]) for item in dated.json()["items"] if item["starts_at"]]
    assert starts and all(start.date() == tomorrow for start in starts)

    past = client.post(
        "/api/v1/interviews/scheduling-plans",
        headers=admin_headers,
        json={
            "job_id": job_id,
            "round_id": "technical-2",
            "instruction": "Schedule the selected round",
            "window_start_date": "2020-01-01",
            "window_end_date": "2020-01-02",
        },
    )
    assert past.status_code == 422


def test_chat_scheduling_works_without_llm_and_answers_follow_up_questions(client, admin_headers, monkeypatch):
    fake_calendar = AvailableCalendar()
    monkeypatch.setattr(interview_workflow, "GoogleCalendar", lambda: fake_calendar)
    job_id = create_job(client, admin_headers)
    configure_rounds(client, admin_headers, job_id)
    application = create_application(client, job_id, "pallavi@example.com", "Pallavi Ranamale")
    record_round_one(client, admin_headers, application, "passed")

    response = client.post(
        "/api/v1/recruitment/assistant",
        headers=admin_headers,
        json={"message": "schedule tech 2 for Pallavi", "job_id": job_id},
    )
    assert response.status_code == 200, response.text
    plan = response.json()["tool_calls"][0]["result"]
    assert plan["round"]["round_id"] == "technical-2"
    assert plan["counts"]["awaiting_confirmation"] == 1
    assert "Nothing is booked" in response.json()["message"]

    clarification = client.post(
        "/api/v1/recruitment/assistant",
        headers=admin_headers,
        json={"message": "schedule the interview for Pallavi", "job_id": job_id},
    ).json()
    assert clarification["tool_calls"][0]["result"]["round"]["round_id"] == "technical-2"


def test_confirmed_plan_updates_status_schedule_and_sends_each_email_once(client, admin_headers, monkeypatch):
    from app.services import interview_notifications

    sent = []
    monkeypatch.setattr(interview_notifications, "send_email", lambda **mail: sent.append(mail))
    fake_calendar = AvailableCalendar()
    monkeypatch.setattr(interview_workflow, "GoogleCalendar", lambda: fake_calendar)
    job_id = create_job(client, admin_headers)
    configure_rounds(client, admin_headers, job_id)
    chosen = create_application(client, job_id, "chosen@example.com", "Chosen Person")
    other = create_application(client, job_id, "other@example.com", "Other Person")
    record_round_one(client, admin_headers, chosen, "passed")
    record_round_one(client, admin_headers, other, "failed")

    reply = client.post(
        "/api/v1/recruitment/assistant",
        headers=admin_headers,
        json={"message": "Schedule interviews for the 4 shortlisted candidates for Technical Round 2", "job_id": job_id},
    ).json()
    plan = reply["tool_calls"][0]["result"]
    assert plan["round"]["round_id"] == "technical-2"
    assert plan["requested_count"] == 4
    assert "Nothing is booked and no email has been sent" in reply["message"]
    assert sent == []  # nothing is emailed before HR confirms

    newer = client.post(
        "/api/v1/interviews/assistant",
        headers=admin_headers,
        json={"job_id": job_id, "instruction": "Schedule Technical Interview 2"},
    ).json()
    stale = client.post(f"/api/v1/interviews/scheduling-plans/{plan['plan_id']}/confirm", headers=admin_headers)
    assert stale.status_code == 409 and "newer plan" in stale.json()["detail"]

    confirmed = client.post(
        f"/api/v1/interviews/scheduling-plans/{newer['plan_id']}/confirm", headers=admin_headers
    ).json()
    assert confirmed["notifications"]["sent"] == 1
    recipients = sorted(mail["recipient"] for mail in sent)
    assert recipients == ["chosen@example.com", "interviewer@example.com"]
    candidate_mail = next(mail for mail in sent if mail["recipient"] == "chosen@example.com")
    assert "shortlisted for Technical Interview 2" in candidate_mail["body"]

    applications = {
        item["id"]: item for item in client.get("/api/v1/recruitment/applications", headers=admin_headers).json()
    }
    assert applications[chosen]["pipeline_status"] == "Shortlisted for Technical Interview 2"
    assert applications[chosen]["stage"] == "interview"
    assert applications[other]["pipeline_status"] == "Not selected after Technical Interview 1"

    schedule = [item for item in client.get("/api/v1/interviews", headers=admin_headers).json() if item["round_id"] == "technical-2"]
    assert len(schedule) == 1
    assert schedule[0]["candidate_email"] == "chosen@example.com"
    assert schedule[0]["email_status"] == "sent"
    assert schedule[0]["pipeline_status"] == "Shortlisted for Technical Interview 2"

    again = client.post(
        f"/api/v1/interviews/scheduling-plans/{newer['plan_id']}/resend-notifications", headers=admin_headers
    )
    assert again.status_code == 200
    assert len(sent) == 2  # idempotent: nothing is sent twice


def test_failed_email_does_not_block_booking_and_can_be_retried(client, admin_headers, monkeypatch):
    from app.services import interview_notifications
    from app.services.email_notifications import EmailNotConfigured

    outbox = []
    state = {"configured": False}

    def fake_send(**mail):
        if not state["configured"]:
            raise EmailNotConfigured("Email sending is disabled.")
        outbox.append(mail)

    monkeypatch.setattr(interview_notifications, "send_email", fake_send)
    monkeypatch.setattr(interview_workflow, "GoogleCalendar", lambda: AvailableCalendar())
    job_id = create_job(client, admin_headers)
    configure_rounds(client, admin_headers, job_id)
    application = create_application(client, job_id, "retry@example.com", "Retry Person")
    record_round_one(client, admin_headers, application, "passed")
    plan = client.post(
        "/api/v1/interviews/assistant",
        headers=admin_headers,
        json={"job_id": job_id, "instruction": "Schedule Technical Interview 2"},
    ).json()
    confirmed = client.post(f"/api/v1/interviews/scheduling-plans/{plan['plan_id']}/confirm", headers=admin_headers).json()
    assert confirmed["counts"]["scheduled"] == 1
    assert confirmed["notifications"]["not_configured"] == 1
    assert confirmed["items"][0]["email_status"] == "not_configured"

    state["configured"] = True
    retried = client.post(
        f"/api/v1/interviews/scheduling-plans/{plan['plan_id']}/resend-notifications", headers=admin_headers
    ).json()
    assert retried["notifications"]["sent"] == 1
    assert len(outbox) == 2
    client.post(f"/api/v1/interviews/scheduling-plans/{plan['plan_id']}/resend-notifications", headers=admin_headers)
    assert len(outbox) == 2


def test_calendar_status_reports_unconfigured_calendar_with_setup_steps(client, admin_headers):
    response = client.get("/api/v1/interviews/calendar-status", headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["configured"] is False
    assert response.json()["steps"]


def test_job_deletion_requires_confirmation_for_applications_and_cleans_up(client, admin_headers):
    job_id = create_job(client, admin_headers)
    create_application(client, job_id, "gone@example.com", "Gone Candidate")

    refused = client.delete(f"/api/v1/recruitment/jobs/{job_id}", headers=admin_headers)
    assert refused.status_code == 409
    assert "1 application" in refused.json()["detail"]

    deleted = client.delete(
        f"/api/v1/recruitment/jobs/{job_id}?delete_applications=true", headers=admin_headers
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted_applications"] == 1
    assert client.get(f"/api/v1/recruitment/jobs/{job_id}", headers=admin_headers).status_code == 404
    assert client.get("/api/v1/recruitment/applications", headers=admin_headers).json() == []
    assert client.delete(f"/api/v1/recruitment/jobs/{job_id}", headers=admin_headers).status_code == 404


def create_job(client, headers):
    response = client.post(
        "/api/v1/recruitment/jobs",
        headers=headers,
        json={"title": "Workflow Engineer", "description": "Build dependable software."},
    )
    assert response.status_code == 201, response.text
    job_id = response.json()["id"]
    published = client.post(f"/api/v1/recruitment/jobs/{job_id}/publish", headers=headers)
    assert published.status_code == 200, published.text
    return job_id


def create_application(client, job_id, email, name):
    resume = base64.b64encode(f"{name}\n{email}\nPython".encode()).decode()
    response = client.post(
        "/api/v1/recruitment/google-forms/applications",
        headers={"X-Integration-Key": "test-integration-secret"},
        json={
            "name": name,
            "email": email,
            "job_title": "Workflow Engineer",
            "resume_filename": f"{name.lower()}.txt",
            "resume_content_type": "text/plain",
            "resume_base64": resume,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def configure_rounds(client, headers, job_id):
    response = client.put(
        f"/api/v1/interviews/jobs/{job_id}/rounds",
        headers=headers,
        json={
            "rounds": [
                {
                    "round_id": "technical-1",
                    "name": "Technical Interview 1",
                    "order": 1,
                    "duration_minutes": 60,
                    "interview_type": "technical",
                    "required_interviewers": ["interviewer@example.com"],
                },
                {
                    "round_id": "technical-2",
                    "name": "Technical Interview 2",
                    "order": 2,
                    "duration_minutes": 60,
                    "interview_type": "technical",
                    "required_interviewers": ["interviewer@example.com"],
                    "prerequisite_round_ids": ["technical-1"],
                },
            ],
        },
    )
    assert response.status_code == 200, response.text


def record_round_one(client, headers, application_id, outcome="passed"):
    response = client.post(
        "/api/v1/interviews",
        headers=headers,
        json={
            "application_id": application_id,
            "starts_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            "timezone": "UTC",
            "interview_type": "technical",
            "interviewers": ["Interviewer"],
            "interviewer_emails": ["interviewer@example.com"],
            "round_id": "technical-1",
            "round_name": "Technical Interview 1",
            "status": "completed",
            "outcome": outcome,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_plan_uses_recorded_outcomes_and_confirmation_creates_invited_event(
    client, admin_headers, monkeypatch
):
    fake_calendar = AvailableCalendar()
    monkeypatch.setattr(interview_workflow, "GoogleCalendar", lambda: fake_calendar)
    job_id = create_job(client, admin_headers)
    configure_rounds(client, admin_headers, job_id)
    passed_app = create_application(client, job_id, "passed@example.com", "Pass Candidate")
    waiting_app = create_application(client, job_id, "waiting@example.com", "Waiting Candidate")
    record_round_one(client, admin_headers, passed_app, "passed")
    record_round_one(client, admin_headers, waiting_app, "pending_evaluation")
    pending = client.post(
        "/api/v1/interviews/assistant",
        headers=admin_headers,
        json={
            "job_id": job_id,
            "instruction": "Show candidates who completed Technical Interview 1 but are awaiting evaluation.",
        },
    )
    assert pending.status_code == 200
    assert [item["candidate_email"] for item in pending.json()["results"]] == ["waiting@example.com"]

    plan_response = client.post(
        "/api/v1/interviews/scheduling-plans",
        headers=admin_headers,
        json={
            "job_id": job_id,
            "instruction": "Schedule Technical Interview 2 for everyone who passed Technical Interview 1 within the next three days.",
        },
    )
    assert plan_response.status_code == 201, plan_response.text
    plan = plan_response.json()
    assert plan["counts"] == {
        "eligible": 1,
        "scheduled": 0,
        "awaiting_confirmation": 1,
        "awaiting_evaluation": 1,
        "unable_to_schedule": 0,
    }
    assert {item["candidate_email"]: item["status"] for item in plan["items"]} == {
        "passed@example.com": "awaiting_confirmation",
        "waiting@example.com": "awaiting_evaluation",
    }
    assert plan["candidate_calendar_policy"].startswith("Candidate calendar availability is not checked")

    confirmed = client.post(
        f"/api/v1/interviews/scheduling-plans/{plan['plan_id']}/confirm",
        headers=admin_headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["counts"]["scheduled"] == 1
    assert fake_calendar.events == []  # scheduling is email-based: no calendar events or invitations
    assert client.post(
        f"/api/v1/interviews/scheduling-plans/{plan['plan_id']}/confirm",
        headers=admin_headers,
    ).json()["counts"]["scheduled"] == 1
    scheduled = client.get("/api/v1/interviews", headers=admin_headers).json()
    assert sum(item.get("round_id") == "technical-2" for item in scheduled) == 1
    reschedule_plan = client.post(
        "/api/v1/interviews/scheduling-plans",
        headers=admin_headers,
        json={
            "job_id": job_id,
            "instruction": "Reschedule Technical Interview 2 for candidates who passed Technical Interview 1 within the next three days.",
        },
    )
    assert reschedule_plan.status_code == 201, reschedule_plan.text
    assert reschedule_plan.json()["counts"]["awaiting_confirmation"] == 1
    moved = client.post(
        f"/api/v1/interviews/scheduling-plans/{reschedule_plan.json()['plan_id']}/confirm",
        headers=admin_headers,
    )
    assert moved.status_code == 200
    assert moved.json()["counts"]["scheduled"] == 1
    assert len([item for item in client.get("/api/v1/interviews", headers=admin_headers).json() if item.get("round_id") == "technical-2"]) == 1


def test_unavailable_calendar_does_not_create_events_and_ambiguous_round_asks(
    client, admin_headers, monkeypatch
):
    fake_calendar = AvailableCalendar()
    monkeypatch.setattr(interview_workflow, "GoogleCalendar", lambda: fake_calendar)
    job_id = create_job(client, admin_headers)
    configure_rounds(client, admin_headers, job_id)
    application = create_application(client, job_id, "candidate@example.com", "Taylor Candidate")
    record_round_one(client, admin_headers, application, "passed")
    response = client.post(
        "/api/v1/interviews/assistant",
        headers=admin_headers,
        json={"job_id": job_id, "instruction": "Schedule Technical Interview 2 for candidates who passed Technical Interview 1."},
    )
    assert response.status_code == 200
    plan_id = response.json()["plan_id"]
    assert response.json()["counts"]["awaiting_confirmation"] == 1
    fake_calendar.available = False
    confirmation = client.post(
        f"/api/v1/interviews/scheduling-plans/{plan_id}/confirm",
        headers=admin_headers,
    )
    assert confirmation.status_code == 200
    assert confirmation.json()["counts"]["scheduled"] == 0
    assert fake_calendar.events == []

    ambiguous = client.post(
        "/api/v1/interviews/assistant",
        headers=admin_headers,
        json={"job_id": job_id, "instruction": "Schedule the next round for everyone."},
    )
    assert ambiguous.json()["round"]["round_id"] == "technical-2"


def test_outcome_cannot_be_recorded_before_completion(client, admin_headers, monkeypatch):
    monkeypatch.setattr(interview_routes, "GoogleCalendar", AvailableCalendar)
    job_id = create_job(client, admin_headers)
    configure_rounds(client, admin_headers, job_id)
    application = create_application(client, job_id, "candidate@example.com", "Taylor Candidate")
    upcoming = client.post(
        "/api/v1/interviews",
        headers=admin_headers,
        json={
            "application_id": application,
            "starts_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "timezone": "UTC",
            "interview_type": "technical",
            "interviewers": ["Interviewer"],
            "interviewer_emails": ["interviewer@example.com"],
            "round_id": "technical-1",
            "status": "scheduled",
        },
    )
    assert upcoming.status_code == 201, upcoming.text
    outcome = client.patch(
        f"/api/v1/interviews/{upcoming.json()['id']}/outcome",
        headers=admin_headers,
        json={"outcome": "passed"},
    )
    assert outcome.status_code == 409


def test_progression_override_is_explicit_audited_and_revocable(client, admin_headers, monkeypatch):
    fake_calendar = AvailableCalendar()
    monkeypatch.setattr(interview_workflow, "GoogleCalendar", lambda: fake_calendar)
    job_id = create_job(client, admin_headers)
    configure_rounds(client, admin_headers, job_id)
    application = create_application(client, job_id, "override@example.com", "Override Candidate")
    record_round_one(client, admin_headers, application, "failed")

    override = client.post(
        f"/api/v1/interviews/applications/{application}/progression-overrides",
        headers=admin_headers,
        json={
            "round_id": "technical-2",
            "reason": "Hiring lead explicitly approved an additional technical evaluation.",
            "explicitly_authorized": True,
        },
    )
    assert override.status_code == 201, override.text
    assert client.get("/api/v1/interviews", headers=admin_headers).json()[0]["outcome"] == "failed"
    plan = client.post(
        "/api/v1/interviews/scheduling-plans",
        headers=admin_headers,
        json={
            "job_id": job_id,
            "instruction": "Schedule Technical Interview 2 for everyone who passed Technical Interview 1 within the next three days.",
        },
    ).json()
    assert plan["counts"]["eligible"] == 1
    assert plan["items"][0]["progression_override_id"] == override.json()["_id"]
    revoked = client.delete(
        f"/api/v1/interviews/applications/{application}/progression-overrides/technical-2",
        headers=admin_headers,
    )
    assert revoked.status_code == 200
    result = client.post(
        f"/api/v1/interviews/scheduling-plans/{plan['plan_id']}/confirm",
        headers=admin_headers,
    )
    assert result.status_code == 200
    assert result.json()["counts"]["scheduled"] == 0
    assert result.json()["items"][0]["status"] == "ineligible"
    assert result.json()["items"][0]["reason"].startswith("The progression override")
