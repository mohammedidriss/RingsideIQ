"""Terms and conditions (ADR-016): accepted at sign-up, asked for at the next
sign-in when they weren't (an admin made the account) or when they change, and
nothing but seeing and accepting them works until they are."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.services import terms


def _signup(client: TestClient, email: str, **extra: object) -> object:
    return client.post(
        "/auth/register",
        json={"email": email, "password": "ValidPass123", "name": "Lina Khoury", **extra},
    )


def _login(client: TestClient, email: str) -> dict[str, str]:
    r = client.post("/auth/login", data={"username": email, "password": "ValidPass123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_the_terms_are_public_and_marked_as_a_placeholder(client: TestClient) -> None:
    r = client.get("/auth/terms")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == terms.TERMS_VERSION and body["placeholder"] is True
    assert "PLACEHOLDER" in body["text"]


def test_signing_up_requires_accepting_the_current_terms(client: TestClient) -> None:
    assert _signup(client, "a@alion.test").status_code == 422  # type: ignore[attr-defined]
    wrong = _signup(client, "b@alion.test", accept_terms=True, terms_version="draft-0")
    assert wrong.status_code == 422 and "changed" in wrong.json()["detail"]  # type: ignore[attr-defined]
    ok = _signup(client, "c@alion.test", accept_terms=True, terms_version=terms.TERMS_VERSION)
    assert ok.status_code == 201  # type: ignore[attr-defined]
    user = ok.json()["user"]  # type: ignore[attr-defined]
    assert user["terms_version"] == terms.TERMS_VERSION and user["terms_accepted_at"]


def test_an_account_made_by_an_admin_is_asked_at_first_sign_in(
    authed_client: TestClient, client: TestClient
) -> None:
    r = authed_client.post(
        "/auth/admin/users",
        json={
            "email": "lina@alion.test",
            "password": "ValidPass123",
            "name": "Lina Khoury",
            "role": "coach",
        },
    )
    assert r.status_code == 201, r.text
    auth = _login(client, "lina@alion.test")

    blocked = client.get("/fighters", headers=auth)
    assert blocked.status_code == 403 and blocked.json()["detail"].startswith("terms_not_accepted")
    me = client.get("/auth/me", headers=auth)  # they can still see who they are
    assert me.status_code == 200 and me.json()["terms_version"] is None

    r = client.post("/auth/terms/accept", json={"version": terms.TERMS_VERSION}, headers=auth)
    assert r.status_code == 200 and r.json()["terms_version"] == terms.TERMS_VERSION
    assert client.get("/fighters", headers=auth).status_code == 200


def test_changed_terms_are_asked_for_again(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _signup(client, "d@alion.test", accept_terms=True, terms_version=terms.TERMS_VERSION)
    auth = _login(client, "d@alion.test")
    assert client.get("/fighters", headers=auth).status_code == 200

    old = terms.TERMS_VERSION
    monkeypatch.setattr(terms, "TERMS_VERSION", "1.0")
    assert client.get("/fighters", headers=auth).status_code == 403
    stale = client.post("/auth/terms/accept", json={"version": old}, headers=auth)
    assert stale.status_code == 409  # accepting the version they read before won't do
    assert (
        client.post("/auth/terms/accept", json={"version": "1.0"}, headers=auth).status_code == 200
    )
    assert client.get("/fighters", headers=auth).status_code == 200


def test_accepting_needs_a_signed_in_user(client: TestClient) -> None:
    r = client.post("/auth/terms/accept", json={"version": terms.TERMS_VERSION})
    assert r.status_code == 401
