import base64


def create_job(client, headers):
    response = client.post(
        "/api/v1/recruitment/jobs",
        headers=headers,
        json={"title": "People Operations Manager", "department": "People", "description": "Support the People team"},
    )
    assert response.status_code == 201
    published = client.post(f"/api/v1/recruitment/jobs/{response.json()['id']}/publish", headers=headers)
    assert published.status_code == 200, published.text
    return response.json()


def submission(**updates):
    payload = {
        "name": "Alex Example",
        "email": "alex@example.com",
        "phone": None,
        "job_title": "People Operations Manager",
        "resume_filename": "alex.txt",
        "resume_content_type": "text/plain",
        "resume_base64": base64.b64encode(
            b"Alex Example\nalex@example.com\n5 years of experience\nPython, React\nBachelor degree"
        ).decode(),
    }
    payload.update(updates)
    return payload


def test_google_intake_is_secret_protected_and_creates_private_review(client, admin_headers, tmp_path):
    create_job(client, admin_headers)
    path = "/api/v1/recruitment/google-forms/applications"
    invalid_key = client.post(path, headers={"X-Integration-Key": "wrong"}, json=submission())
    assert invalid_key.status_code == 401
    response = client.post(path, headers={"X-Integration-Key": "test-integration-secret"}, json=submission())
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["stage"] == "needs_review"
    assert body["candidate"]["name"] == "Alex Example"
    assert "python" in body["candidate"]["skills"]
    assert body["candidate"]["experience_years"] == 5
    resume_path = tmp_path / "private"
    stored = list(resume_path.iterdir())
    assert len(stored) == 1
    assert stored[0].name != "alex.txt"
    assert client.get(f"/api/v1/recruitment/applications/{body['id']}/resume", headers=admin_headers).status_code == 200
    assert client.get(f"/api/v1/recruitment/applications/{body['id']}/resume").status_code == 401


def test_google_intake_rejects_duplicates_and_unreadable_files(client, admin_headers):
    create_job(client, admin_headers)
    endpoint = "/api/v1/recruitment/google-forms/applications"
    headers = {"X-Integration-Key": "test-integration-secret"}
    assert client.post(endpoint, headers=headers, json=submission()).status_code == 201
    duplicate = client.post(endpoint, headers=headers, json=submission())
    assert duplicate.status_code == 409
    unreadable = submission(
        email="different@example.com",
        resume_base64=base64.b64encode(b"   ").decode(),
    )
    assert client.post(endpoint, headers=headers, json=unreadable).status_code == 422
    unsupported = submission(
        email="different@example.com",
        resume_filename="resume.rtf",
        resume_content_type="application/rtf",
    )
    assert client.post(endpoint, headers=headers, json=unsupported).status_code == 415
    assert len(client.get("/api/v1/recruitment/applications", headers=admin_headers).json()) == 1
