"""
Unit & Integration Tests for the Flask Application
Run: pytest tests/ -v --cov=app --cov-report=xml
"""

import pytest
import json
from app import create_app, db, Item


# ── Fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def app():
    """Create test app with in-memory SQLite."""
    test_config = {
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SECRET_KEY": "test-secret",
        "ENV": "test",
    }
    app = create_app(config=test_config)
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def clean_db(app):
    """Clean DB between tests."""
    with app.app_context():
        yield
        db.session.rollback()
        Item.query.delete()
        db.session.commit()


# ── Health endpoint tests ─────────────────────────────────────────────────────
class TestHealthEndpoints:

    def test_liveness_returns_200(self, client):
        resp = client.get("/health/live")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "UP"

    def test_readiness_returns_200_when_db_ok(self, client):
        resp = client.get("/health/ready")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "UP"
        assert data["db"] == "UP"

    def test_startup_returns_200(self, client):
        resp = client.get("/health/startup")
        assert resp.status_code == 200

    def test_readiness_includes_version(self, client):
        resp = client.get("/health/ready")
        data = json.loads(resp.data)
        assert "version" in data
        assert "env" in data
        assert "timestamp" in data


# ── Version endpoint tests ────────────────────────────────────────────────────
class TestVersionEndpoint:

    def test_version_returns_app_info(self, client):
        resp = client.get("/api/v1/version")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["app"] == "python-cicd-app"
        assert "version" in data
        assert "env" in data


# ── CRUD endpoint tests ───────────────────────────────────────────────────────
class TestItemsAPI:

    def test_get_items_empty(self, client):
        resp = client.get("/api/v1/items")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["items"] == []
        assert data["total"] == 0

    def test_create_item_success(self, client):
        payload = {"name": "Test Item", "description": "A test item"}
        resp = client.post("/api/v1/items",
                           data=json.dumps(payload),
                           content_type="application/json")
        assert resp.status_code == 201
        data = json.loads(resp.data)
        assert data["name"] == "Test Item"
        assert data["description"] == "A test item"
        assert "id" in data
        assert "created_at" in data

    def test_create_item_missing_name_returns_400(self, client):
        resp = client.post("/api/v1/items",
                           data=json.dumps({"description": "no name"}),
                           content_type="application/json")
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert "error" in data

    def test_get_item_by_id(self, client, app):
        with app.app_context():
            item = Item(name="Get Me", description="fetch test")
            db.session.add(item)
            db.session.commit()
            item_id = item.id

        resp = client.get(f"/api/v1/items/{item_id}")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["name"] == "Get Me"

    def test_get_nonexistent_item_returns_404(self, client):
        resp = client.get("/api/v1/items/999999")
        assert resp.status_code == 404

    def test_update_item(self, client, app):
        with app.app_context():
            item = Item(name="Old Name")
            db.session.add(item)
            db.session.commit()
            item_id = item.id

        resp = client.put(f"/api/v1/items/{item_id}",
                          data=json.dumps({"name": "New Name"}),
                          content_type="application/json")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["name"] == "New Name"

    def test_delete_item(self, client, app):
        with app.app_context():
            item = Item(name="Delete Me")
            db.session.add(item)
            db.session.commit()
            item_id = item.id

        resp = client.delete(f"/api/v1/items/{item_id}")
        assert resp.status_code == 200

        # Confirm deleted
        resp2 = client.get(f"/api/v1/items/{item_id}")
        assert resp2.status_code == 404

    def test_get_items_pagination(self, client, app):
        with app.app_context():
            for i in range(15):
                db.session.add(Item(name=f"Item {i}"))
            db.session.commit()

        resp = client.get("/api/v1/items?page=1&per_page=5")
        data = json.loads(resp.data)
        assert len(data["items"]) == 5
        assert data["total"] == 15
        assert data["pages"] == 3

    def test_create_and_list_multiple_items(self, client):
        names = ["Alpha", "Beta", "Gamma"]
        for name in names:
            client.post("/api/v1/items",
                        data=json.dumps({"name": name}),
                        content_type="application/json")

        resp = client.get("/api/v1/items")
        data = json.loads(resp.data)
        assert data["total"] == 3
        returned_names = [i["name"] for i in data["items"]]
        for name in names:
            assert name in returned_names


# ── Metrics endpoint test ─────────────────────────────────────────────────────
class TestMetrics:

    def test_metrics_endpoint_accessible(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        # Prometheus text format
        assert b"flask_http_request_total" in resp.data or b"#" in resp.data
