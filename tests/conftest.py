"""Test fixtures — uses an in-memory SQLite engine, isolated per test."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    from api.deps import db_session
    from api.main import app

    def _override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[db_session] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def authed_client(session: Session) -> Iterator[TestClient]:
    """TestClient pre-configured with an admin JWT — for routes that require auth."""
    from api.deps import db_session
    from api.main import app
    from api.services.terms import TERMS_VERSION

    def _override() -> Iterator[Session]:
        yield session

    app.dependency_overrides[db_session] = _override
    with TestClient(app) as c:
        resp = c.post(
            "/auth/register",
            json={
                "email": "test-admin@alion.test",
                "password": "TestPass123!",
                "name": "Test Admin",
                "role": "admin",
                "accept_terms": True,
                "terms_version": TERMS_VERSION,
            },
        )
        token = resp.json()["access_token"]
        c.headers.update({"Authorization": f"Bearer {token}"})
        yield c
    app.dependency_overrides.clear()
