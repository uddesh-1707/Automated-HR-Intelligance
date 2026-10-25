from datetime import datetime, timedelta, timezone

import base64


def application_id(client, headers):
    job = client.post(
        "/api/v1/recruitment/jobs",
        headers=headers,
        json={"title": "Engineer", "description": "Build backend services"},
    )
    client.post(f"/api/v1/recruitment/jobs/{job.json()['id']}/publish", headers=headers)
    resume = base64.b64encode(b"Candidate\ncandidate@example.com\nPython").decode()
    response = client.post(
        "/api/v1/recruitment/google-forms/applications",
        headers={"X-Integration-Key": "test-integration-secret"},
        json={
            "name": "Casey Candidate",
            "email": "candidate@example.com",
            "job_title": "Engineer",
            "resume_filename": "candidate.txt",
            "resume_content_type": "text/plain",
            "resume_base64": resume,
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_interview_scheduling_validation_and_status_updates(client, admin_headers):
    application = application_id(client, admin_headers)
    endpoint = "/api/v1/interviews"
    base = {
        "application_id": application,
        "starts_at": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        "timezone": "America/New_York",
        "interview_type": "video",
        "interviewers": ["Riley Interviewer"],
        "location": "https://meet.example.com/room",
        "notes": "Discuss team fit",
    }
    invalid_timezone = {**base, "timezone": "Not/A_Timezone"}
    assert client.post(endpoint, headers=admin_headers, json=invalid_timezone).status_code == 422
    invalid_date = {**base, "starts_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()}
    assert client.post(endpoint, headers=admin_headers, json=invalid_date).status_code == 422
    created = client.post(endpoint, headers=admin_headers, json=base)
    assert created.status_code == 201
    interview = created.json()
    assert interview["candidate_name"] == "Casey Candidate"
    assert interview["status"] == "scheduled"
    completed = {key: value for key, value in interview.items() if key not in ("id", "candidate_name", "candidate_email", "job_title")}
    completed["status"] = "completed"
    update = client.put(f"{endpoint}/{interview['id']}", headers=admin_headers, json=completed)
    assert update.status_code == 200
    assert update.json()["status"] == "completed"
    assert client.get(endpoint, headers=admin_headers).json()[0]["status"] == "completed"
