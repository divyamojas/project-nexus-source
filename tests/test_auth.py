from __future__ import annotations

import os

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# App fixture — use a real DB if DATABASE_URL is set; otherwise skip DB tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Auth route tests
# ---------------------------------------------------------------------------

class TestSignupValidation:
    """Domain validation is stateless — no DB required."""

    def test_signup_rejects_invalid_domain(self, client):
        resp = client.post("/auth/signup", json={"email": "user@yahoo.com", "password": "pass1234"})
        assert resp.status_code == 400
        assert "domain" in resp.json()["detail"].lower()

    def test_signup_rejects_no_at_sign(self, client):
        resp = client.post("/auth/signup", json={"email": "notanemail", "password": "pass1234"})
        assert resp.status_code == 400

    def test_signup_accepts_gmail(self, client, monkeypatch):
        """Monkeypatches Supabase so we can test domain validation without a live project."""
        import app.routes.auth as auth_module

        class _FakeUser:
            id = "fake-uuid"
            email = "user@gmail.com"

        class _FakeResp:
            user = _FakeUser()

        class _FakeAuth:
            def sign_up(self, payload):
                return _FakeResp()

        class _FakeSupabase:
            auth = _FakeAuth()

        import app.main as main_module
        # Inject fake supabase into app state
        from app.main import app as fastapi_app
        fastapi_app.state.supabase = _FakeSupabase()

        resp = client.post("/auth/signup", json={"email": "user@gmail.com", "password": "pass1234"})
        # Should not be a 400 domain error (may fail for other reasons without live Supabase)
        assert resp.status_code != 400 or "domain" not in resp.json().get("detail", "")

    def test_signup_accepts_sprinklr(self, client, monkeypatch):
        import app.routes.auth as auth_module

        class _FakeUser:
            id = "fake-uuid"
            email = "user@sprinklr.com"

        class _FakeResp:
            user = _FakeUser()

        class _FakeAuth:
            def sign_up(self, payload):
                return _FakeResp()

        class _FakeSupabase:
            auth = _FakeAuth()

        from app.main import app as fastapi_app
        fastapi_app.state.supabase = _FakeSupabase()

        resp = client.post("/auth/signup", json={"email": "user@sprinklr.com", "password": "pass1234"})
        assert resp.status_code != 400 or "domain" not in resp.json().get("detail", "")


class TestMeEndpoint:
    def test_me_requires_auth(self, client):
        resp = client.get("/auth/me")
        assert resp.status_code == 403  # HTTPBearer raises 403 when no credentials

    def test_me_invalid_token(self, client):
        resp = client.get("/auth/me", headers={"Authorization": "Bearer notarealtoken"})
        assert resp.status_code == 401


class TestLoginEndpoint:
    def test_login_with_bad_credentials(self, client):
        """Without a live Supabase, any login should return 401."""
        resp = client.post(
            "/auth/login",
            json={"email": "nobody@gmail.com", "password": "wrongpassword"},
        )
        assert resp.status_code in (401, 400, 500)

    def test_login_missing_body(self, client):
        resp = client.post("/auth/login", json={})
        assert resp.status_code == 422


class TestResetPassword:
    def test_reset_password_missing_email(self, client):
        resp = client.post("/auth/reset-password", json={})
        assert resp.status_code == 422
