def test_hr_endpoints_require_authentication(client):
    assert client.get("/api/v1/recruitment/applications").status_code == 401
    assert client.get("/api/v1/interviews").status_code == 401


def test_signup_uses_email_and_password_and_never_grants_admin(client):
    created = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "new@example.com",
            "password": "another-secure-password",
            "role": "admin",
        },
    )
    assert created.status_code == 201
    assert created.json()["user"]["role"] == "recruiter"
    assert client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {created.json()['access_token']}"},
    ).json()["email"] == "new@example.com"
    duplicate = client.post(
        "/api/v1/auth/signup",
        json={"email": "new@example.com", "password": "another-secure-password"},
    )
    assert duplicate.status_code == 409
