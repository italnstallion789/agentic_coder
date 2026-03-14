from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from agentic_coder.config import get_settings


@lru_cache(maxsize=1)
def create_db_engine() -> object:
    settings = get_settings()
    return create_engine(settings.database_url, pool_pre_ping=True)


@lru_cache(maxsize=1)
def create_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=create_db_engine(), autoflush=False, autocommit=False)


def get_session() -> Iterator[Session]:
    session_factory = create_session_factory()
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
