from datetime import datetime, timezone
import logging

from fastapi import APIRouter, HTTPException, status
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.api.deps import CurrentUser
from app.core.database import MongoDatabase
from app.models import USERS, new_id
from app.schemas import LoginRequest, SignupRequest, TokenRead, UserRead
from app.security import create_access_token, hash_password, verify_password

router = APIRouter()
logger = logging.getLogger(__name__)


def user_read(user: dict) -> UserRead:
    return UserRead(id=user["_id"], email=user["email"], role=user["role"])


@router.post("/login", response_model=TokenRead)
def login(data: LoginRequest, db: MongoDatabase) -> TokenRead:
    user = db[USERS].find_one({"email": data.email.lower()})
    if user is None or not verify_password(data.password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")
    return TokenRead(access_token=create_access_token(user["_id"], user["role"]), user=user_read(user))


@router.post("/signup", response_model=TokenRead, status_code=status.HTTP_201_CREATED)
def signup(data: SignupRequest, db: MongoDatabase) -> TokenRead:
    email = data.email.lower()
    user = {
        "_id": new_id(),
        "email": email,
        "password_hash": hash_password(data.password),
        "role": "recruiter",
        "created_at": datetime.now(timezone.utc),
    }
    try:
        db[USERS].insert_one(user)
    except DuplicateKeyError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists")
    except PyMongoError:
        logger.exception("Unable to create recruiter account")
        raise
    return TokenRead(access_token=create_access_token(user["_id"], user["role"]), user=user_read(user))


@router.get("/me", response_model=UserRead)
def me(user: CurrentUser) -> UserRead:
    return user_read(user)
