"""Security regression tests.

These pin the access-control and input-hardening fixes so they can't
silently regress:

* Routers that were previously unauthenticated now require a bearer token
  (HRV, IMU, RQ1 study ratings, round plans).
* The HRV SSE live stream stays reachable without a token — the browser's
  EventSource can't send headers, so the unguessable session UUID is the
  access control there (same model as the MJPEG preview route).
* Password policy rejects weak passwords.
* CSV upload endpoints reject oversized payloads with 413.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from api.services.terms import TERMS_VERSION

BOGUS = str(uuid4())


# ── Auth now required on previously-open routers ──────────────────────────────


def test_imu_routes_require_auth(client: TestClient) -> None:
    """Unauthenticated IMU access is rejected with 401 (was wide open)."""
    assert client.get(f"/v2/sessions/{BOGUS}/imu/samples").status_code == 401
    assert client.post(f"/v2/sessions/{BOGUS}/imu/synth").status_code == 401


def test_hrv_routes_require_auth(client: TestClient) -> None:
    """Unauthenticated HRV data access is rejected with 401."""
    assert client.get(f"/v2/sessions/{BOGUS}/hrv/samples").status_code == 401
    assert client.get(f"/v2/sessions/{BOGUS}/hrv/status").status_code == 401
    assert client.get(f"/v2/sessions/{BOGUS}/hrv/metrics").status_code == 401


def test_rq1_ratings_require_auth(client: TestClient) -> None:
    """The bulk research-data dump must not be world-readable."""
    assert client.get("/studies/rq1/ratings").status_code == 401
    assert client.get(f"/studies/rq1/sessions/{BOGUS}/ratings").status_code == 401


def test_round_plans_require_auth(client: TestClient) -> None:
    assert client.get("/round_plans").status_code == 401


def test_protected_routes_work_with_token(authed_client: TestClient) -> None:
    """Same routes succeed once authenticated — proves we gated, not broke, them."""
    assert authed_client.get("/studies/rq1/ratings").status_code == 200
    assert authed_client.get("/round_plans").status_code == 200
    # Unknown session → 404 (not 401), i.e. we got past the auth gate.
    assert authed_client.get(f"/v2/sessions/{BOGUS}/imu/samples").status_code == 404
    assert authed_client.get(f"/v2/sessions/{BOGUS}/hrv/samples").status_code == 404


# ── SSE stream stays open (UUID is the access control) ────────────────────────


def test_hrv_live_stream_not_auth_gated(client: TestClient) -> None:
    """EventSource can't send a token; an unknown session returns 404, not 401.

    A 401 here would mean the stream got an auth gate and would break the
    live HRV chart in the browser.
    """
    assert client.get(f"/v2/sessions/{BOGUS}/hrv/live").status_code == 404


# ── Password policy ───────────────────────────────────────────────────────────


def _register(client: TestClient, password: str) -> int:
    return client.post(
        "/auth/register",
        json={
            "email": f"pw-{uuid4().hex[:8]}@alion.test",
            "password": password,
            "name": "PW Test",
            "role": "fighter",
            "accept_terms": True,
            "terms_version": TERMS_VERSION,
        },
    ).status_code


def test_password_too_short_rejected(client: TestClient) -> None:
    assert _register(client, "Abc123") == 422  # 6 chars, below the 8 floor


def test_password_without_number_rejected(client: TestClient) -> None:
    assert _register(client, "alllettershere") == 422


def test_password_without_letter_rejected(client: TestClient) -> None:
    assert _register(client, "12345678") == 422


def test_strong_password_accepted(client: TestClient) -> None:
    assert _register(client, "ValidPass123") == 201


# ── Upload size limits ────────────────────────────────────────────────────────


def test_imu_upload_rejects_oversized(authed_client: TestClient, monkeypatch) -> None:
    """Oversized IMU CSV returns 413 instead of being written to disk."""
    from api.routes import imu

    # Shrink the cap so we don't have to generate 100 MB in a test.
    monkeypatch.setattr(imu, "_MAX_IMU_UPLOAD_BYTES", 1024)

    fid = authed_client.post("/fighters", json={"name": "IMU-size"}).json()["id"]
    sid = authed_client.post("/sessions", json={"fighter_id": fid, "source": "hrv_replay"}).json()[
        "id"
    ]

    big = b"t_ms,ax_g,ay_g,az_g\n" + b"0,1.0,0.0,0.0\n" * 500  # > 1 KB
    r = authed_client.post(
        f"/v2/sessions/{sid}/imu/upload",
        files={"file": ("imu.csv", big, "text/csv")},
    )
    assert r.status_code == 413


def test_hrv_upload_rejects_oversized(authed_client: TestClient, monkeypatch) -> None:
    """Oversized HRV CSV returns 413 and leaves no file behind."""
    from api.routes import hrv

    monkeypatch.setattr(hrv, "_MAX_HRV_UPLOAD_BYTES", 1024)

    fid = authed_client.post("/fighters", json={"name": "HRV-size"}).json()["id"]
    sid = authed_client.post("/sessions", json={"fighter_id": fid, "source": "hrv_replay"}).json()[
        "id"
    ]

    big = b"rr_ms\n" + b"800\n" * 500  # > 1 KB
    r = authed_client.post(
        f"/v2/sessions/{sid}/hrv/upload",
        files={"file": ("hrv.csv", big, "text/csv")},
    )
    assert r.status_code == 413
    assert not (hrv._HRV_DIR / f"{sid}.csv").exists()


# ── JWT secret hardening (fail closed in production) ──────────────────────────


def test_secret_key_fatal_in_production(monkeypatch) -> None:
    """A missing/default secret must abort startup on a real deployment."""
    import pytest

    from api.routes import auth

    monkeypatch.delenv("ALION_JWT_SECRET", raising=False)
    monkeypatch.setenv("ALION_ENV", "production")
    with pytest.raises(RuntimeError, match="ALION_JWT_SECRET must be set"):
        auth._resolve_secret_key()


def test_railway_env_triggers_production(monkeypatch) -> None:
    """Railway's auto-injected var counts as production even without ALION_ENV."""
    from api.routes import auth

    monkeypatch.delenv("ALION_ENV", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    assert auth._is_production() is True


def test_explicit_secret_accepted_in_production(monkeypatch) -> None:
    """A real secret in production resolves cleanly."""
    from api.routes import auth

    monkeypatch.setenv("ALION_ENV", "production")
    monkeypatch.setenv("ALION_JWT_SECRET", "a-real-strong-secret-value-123")
    assert auth._resolve_secret_key() == "a-real-strong-secret-value-123"


def test_dev_falls_back_to_default(monkeypatch) -> None:
    """Outside production, a missing secret warns but still boots."""
    from api.routes import auth

    monkeypatch.delenv("ALION_JWT_SECRET", raising=False)
    monkeypatch.delenv("ALION_ENV", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    assert auth._resolve_secret_key() == auth._DEV_SECRET
