"""Pytest fixtures.

Requires a reachable Postgres (with pgvector) via DATABASE_URL — if it can't be
reached, DB-backed tests are skipped rather than erroring.

LLM credentials are forced empty here (before the app is imported) so tests are
hermetic: any inline scoring/embedding/CV work no-ops fast instead of calling out to
a real provider. Auth, jobs, tracker, and matching endpoints are all exercised without
network access.
"""

import os

os.environ["PORTKEY_API_KEY"] = ""
os.environ["PORTKEY_VIRTUAL_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

import jobradar.db.models  # noqa: F401
from jobradar.db.base import Base, engine
from jobradar.main import app


@pytest.fixture(scope="session", autouse=True)
def _setup_db():
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            Base.metadata.create_all(conn)
    except OperationalError:
        pytest.skip("Database not reachable — skipping DB-backed tests", allow_module_level=True)
    yield


@pytest.fixture
def client():
    return TestClient(app)
