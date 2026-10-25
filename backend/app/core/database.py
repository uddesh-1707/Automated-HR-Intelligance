from collections.abc import Generator
from typing import Annotated

from fastapi import Depends
from pymongo import MongoClient
from pymongo.database import Database

from app.core.config import settings

client = MongoClient(
    settings.mongodb_uri,
    serverSelectionTimeoutMS=5000,
    tz_aware=True,
)
database = client[settings.mongodb_database]


def get_database() -> Generator[Database, None, None]:
    yield database


MongoDatabase = Annotated[Database, Depends(get_database)]
