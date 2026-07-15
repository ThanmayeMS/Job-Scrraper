"""SQLAlchemy engine, session factory, and the declarative Base.

Sync engine (psycopg 3) is used everywhere: FastAPI runs sync path operations in
a threadpool, and Celery workers are sync too — one consistent model, no async
foot-guns. Swap to the async engine later if you need it.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from jobradar.config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)

SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, class_=Session
)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yields a session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
