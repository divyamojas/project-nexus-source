from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Books route tests
# ---------------------------------------------------------------------------

class TestBooksAuth:
    """All /books routes require a valid JWT."""

    def test_list_books_no_auth(self, client):
        resp = client.get("/books")
        assert resp.status_code in (401, 403)

    def test_get_book_no_auth(self, client):
        resp = client.get("/books/some-uuid")
        assert resp.status_code in (401, 403)

    def test_create_book_no_auth(self, client):
        resp = client.post("/books", json={"catalog_id": "some-uuid", "condition": "good"})
        assert resp.status_code in (401, 403)

    def test_delete_book_no_auth(self, client):
        resp = client.delete("/books/some-uuid")
        assert resp.status_code in (401, 403)

    def test_archive_book_no_auth(self, client):
        resp = client.patch("/books/some-uuid/archive", json={"archived": True})
        assert resp.status_code in (401, 403)


class TestBooksValidation:
    """Input validation tests that don't need a DB."""

    def test_create_book_missing_fields(self, client):
        resp = client.post(
            "/books",
            json={},
            headers={"Authorization": "Bearer invalidtoken"},
        )
        # 401 (bad token) or 422 (validation error)
        assert resp.status_code in (401, 422)

    def test_archive_book_missing_body(self, client):
        resp = client.patch(
            "/books/some-uuid/archive",
            json={},
            headers={"Authorization": "Bearer invalidtoken"},
        )
        # 401 (bad token) or 422 (validation error)
        assert resp.status_code in (401, 422)


class TestCatalogAuth:
    """All /catalog routes require a valid JWT."""

    def test_search_no_auth(self, client):
        resp = client.get("/catalog/search?q=python")
        assert resp.status_code in (401, 403)

    def test_lookup_no_auth(self, client):
        resp = client.get("/catalog/lookup?title=Python&author=Guido")
        assert resp.status_code in (401, 403)

    def test_create_catalog_no_auth(self, client):
        resp = client.post(
            "/catalog", json={"title": "Python", "author": "Guido"}
        )
        assert resp.status_code in (401, 403)

    def test_get_catalog_no_auth(self, client):
        resp = client.get("/catalog/some-uuid")
        assert resp.status_code in (401, 403)


class TestHealthEndpoint:
    def test_health_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "leaflet-api"


class TestLegalEndpoints:
    """Legal endpoints require no auth."""

    def test_privacy_policy(self, client):
        resp = client.get("/legal/privacy")
        assert resp.status_code == 200
        data = resp.json()
        assert "content" in data
        assert len(data["content"]) > 0

    def test_terms_of_service(self, client):
        resp = client.get("/legal/terms")
        assert resp.status_code == 200
        data = resp.json()
        assert "content" in data
        assert len(data["content"]) > 0
