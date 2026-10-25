from collections.abc import Generator
from contextlib import asynccontextmanager

import mongomock
import pytest
from fastapi.testclient import TestClient
from pymongo.database import Database

from app.core.config import settings
from app.core.database import get_database
from app.main import app
from app.models import USERS, create_indexes
from app.security import hash_password


@pytest.fixture
def client(tmp_path, monkeypatch) -> Generator[TestClient, None, None]:
    mongo_client = mongomock.MongoClient(tz_aware=True)
    test_db = mongo_client["hrms_test"]
    create_indexes(test_db)
    test_db[USERS].insert_many([
        {
            "_id": "admin-id",
            "email": "admin@example.com",
            "password_hash": hash_password("test-password-123"),
            "role": "admin",
        },
        {
            "_id": "recruiter-id",
            "email": "recruiter@example.com",
            "password_hash": hash_password("test-password-123"),
            "role": "recruiter",
        },
    ])
    monkeypatch.setattr(settings, "resume_storage_dir", tmp_path / "private")
    monkeypatch.setattr(settings, "google_forms_integration_secret", "test-integration-secret")
    monkeypatch.setattr(settings, "embedding_api_key", None)
    monkeypatch.setattr(settings, "pinecone_api_key", None)
    monkeypatch.setattr(settings, "agent_api_key", None)
    for name in ("llm_provider", "llm_model", "llm_api_key", "llm_base_url", "hr_contact_email"):
        monkeypatch.setattr(settings, name, None)
    monkeypatch.setattr(settings, "meeting_provider", "none")
    monkeypatch.setattr(settings, "agent_api_base_url", None)
    monkeypatch.setattr(settings, "smtp_host", None)
    monkeypatch.setattr(settings, "google_calendar_service_account_file", None)

    def override_get_database() -> Generator[Database, None, None]:
        yield test_db

    @asynccontextmanager
    async def test_lifespan(_app):
        yield

    app.dependency_overrides[get_database] = override_get_database
    monkeypatch.setattr(app.router, "lifespan_context", test_lifespan)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    mongo_client.close()


@pytest.fixture
def admin_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "test-password-123"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
