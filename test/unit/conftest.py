"""Shared pytest fixtures for the museums test suite."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from museums.api.main import app
from museums.data.db import Base, get_db

# ---------------------------------------------------------------------------
# In-memory database — StaticPool keeps a single connection so that tables
# created before the test remain visible after a session.commit() releases
# the connection back to the pool (critical for :memory: databases).
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session():
    """Yield a transient, in-memory SQLite session with all tables created."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    Base.metadata.drop_all(engine)
    engine.dispose()


# ---------------------------------------------------------------------------
# FastAPI TestClient wired to in-memory DB
# ---------------------------------------------------------------------------


@pytest.fixture
def client(db_session):
    """TestClient for the FastAPI app using the in-memory db_session.

    * Dependency override makes every request share the same session so that
      data written in one request is visible to the next.
    * The lifespan's init_db() is patched out to avoid touching the real DB.
    """

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    # Patch init_db so the lifespan doesn't create the real on-disk database
    with patch("museums.api.main.init_db"):
        with TestClient(app, raise_server_exceptions=True) as tc:
            yield tc

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Sample domain data
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_museums():
    """Three museum dicts with visitors well above the 2 M threshold."""
    return [
        {
            "name": "Alpha Museum",
            "city": "Paris",
            "country": "France",
            "visitors": 8_000_000,
        },
        {
            "name": "Beta Museum",
            "city": "London",
            "country": "UK",
            "visitors": 6_000_000,
        },
        {
            "name": "Gamma Museum",
            "city": "New York",
            "country": "USA",
            "visitors": 4_500_000,
        },
    ]


@pytest.fixture
def sample_populations():
    """City → population mapping matching sample_museums cities."""
    return {
        "Paris": 2_161_000,
        "London": 8_982_000,
        "New York": 8_336_000,
    }
