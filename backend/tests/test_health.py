"""GL-1-3: /v1/health returns 200 with a reachable DB, 503 otherwise."""

from __future__ import annotations

from fastapi.testclient import TestClient
from groundline_api.deps import get_db
from groundline_api.main import app
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def test_health_ok_with_reachable_db():
    """Real `select 1` against the local dev Postgres."""
    with TestClient(app) as client:
        resp = client.get("/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_503_when_db_unreachable():
    """Override get_db with a session bound to a dead address."""
    bad_engine = create_engine(
        "postgresql+psycopg://nobody:nothing@localhost:59999/nope",
        connect_args={"connect_timeout": 2},
    )
    bad_session = sessionmaker(bind=bad_engine, autoflush=False, future=True)

    def bad_db():
        db = bad_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = bad_db
    try:
        with TestClient(app) as client:
            resp = client.get("/v1/health")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 503
    assert resp.json()["detail"] == "database unreachable"
