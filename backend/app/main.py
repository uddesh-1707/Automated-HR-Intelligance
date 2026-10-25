import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi import HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pymongo.errors import PyMongoError

from app.api.v1 import api_router
from app.core.config import settings
from app.core.database import client, database
from app.models import USERS, create_indexes, new_id
from app.security import hash_password
from app.services.llm import resolve_llm_config, validate_llm_config
from app.services.meetings import validate_meeting_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if bool(settings.bootstrap_admin_email) != bool(settings.bootstrap_admin_password):
        raise RuntimeError("Set both BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD, or leave both unset.")
    configuration_problems = validate_llm_config() + validate_meeting_config()
    if configuration_problems:
        raise RuntimeError("Invalid configuration: " + " ".join(configuration_problems))
    if resolve_llm_config() is None:
        logger.info("No LLM configured (LLM_PROVIDER/LLM_MODEL/LLM_API_KEY): the AI assistant's free-form answers are disabled.")
    try:
        client.admin.command("ping")
        create_indexes(database)
        if settings.bootstrap_admin_email and settings.bootstrap_admin_password:
            email = settings.bootstrap_admin_email.lower()
            result = database[USERS].update_one(
                {"email": email},
                {
                    "$setOnInsert": {
                        "_id": new_id(),
                        "email": email,
                        "password_hash": hash_password(settings.bootstrap_admin_password),
                        "role": "admin",
                        "created_at": datetime.now(timezone.utc),
                    }
                },
                upsert=True,
            )
            if result.upserted_id:
                logger.info("Created configured bootstrap administrator")
    except PyMongoError as exc:
        logger.exception("Unable to connect to configured MongoDB database")
        raise RuntimeError("MongoDB connection failed. Check MONGODB_URI and MONGODB_DATABASE.") from exc
    try:
        yield
    finally:
        client.close()


app = FastAPI(title="HRMS Recruitment MVP", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Integration-Key"],
)
app.include_router(api_router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    try:
        client.admin.command("ping")
    except PyMongoError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="MongoDB is unavailable")
    return {"status": "ok"}
